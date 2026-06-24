#!/usr/bin/env python3
"""Oracle batch sweep — parallel.

Reads existing oracle_validation_*.csv (any), skips bugs already scored,
and runs the remaining bugs concurrently. Within one bug, fix+buggy are
serial (to avoid double-slot contention for the same bug).

Concurrency cap: --workers (default 8). Each worker calls /validate_oracle
with timeout=300s. The webapp's 3-slot-per-bug pool means a single bug
holds one slot at a time; across-bug parallelism is bounded by gunicorn
worker count (4 by default) plus IO wait during compile/test.
"""
import argparse, concurrent.futures as cf, csv, glob, json, os, sys, time, threading, urllib.request

WEBAPP = "http://127.0.0.1:8095"

# Default timeout (seconds per /validate_oracle call) when neither project.json
# nor bugs_list_new.json declares one.
DEFAULT_TIMEOUT = 300

# Hardcoded baseline used ONLY as a last-resort fallback if both metadata
# layers omit `oracle_timeout`. Derived from p95 latency × 2 of the prior sweep.
# To override per project, add `"oracle_timeout": N` to project.json's
# `c_compile` block; per-bug, add it to bugs_list_new.json's c_compile.
FALLBACK_TIMEOUT = {
    "KhronosGroup___SPIRV-Tools": 1500,
    "apache___arrow":             1500,
    "danmar___cppcheck":          1200,
    "facebook___rocksdb":         1500,
    "DynamoRIO___dynamorio":      1500,
    "the-tcpdump-group___tcpdump": 900,
    "uncrustify___uncrustify":    600,
    "php___php-src":              900,
    "fmtlib___fmt":               600,
    "zeromq___libzmq":            600,
    "skypjack___entt":            600,
    "libuv___libuv":              600,
    "CauldronDevelopmentLLC___cbang": 600,
}

# Metadata roots, host-side (bind-mounted to /src in container).
SRC_ROOT = "/home/wangjian/wj_code/defects4c_dirs/defects4c_docker_web4/defectsc_tpl"
META_DIRS = (os.path.join(SRC_ROOT, "projects_v1"),
             os.path.join(SRC_ROOT, "projects"))

_meta_cache = {}      # project -> (project_json_dict, bugs_list_array) or None


def _load_project_meta(project):
    """Return (project_json_dict, bugs_list) for a project, or (None, None).
    Looks in projects_v1 first (v1 metadata takes precedence), then projects."""
    if project in _meta_cache:
        return _meta_cache[project]
    for base in META_DIRS:
        pdir = os.path.join(base, project)
        pj_path = os.path.join(pdir, "project.json")
        bl_path = os.path.join(pdir, "bugs_list_new.json")
        if os.path.isfile(pj_path) and os.path.isfile(bl_path):
            try:
                pj = json.load(open(pj_path))
                bl = json.load(open(bl_path))
                _meta_cache[project] = (pj, bl)
                return pj, bl
            except Exception:
                pass
    _meta_cache[project] = (None, None)
    return None, None


def timeout_for(bug_id):
    """Resolve timeout for a bug, in priority order:
        1. bugs_list_new.json entry's `c_compile.oracle_timeout` (per-bug)
        2. project.json's `c_compile.oracle_timeout` (per-project)
        3. FALLBACK_TIMEOUT dict (hardcoded baseline)
        4. DEFAULT_TIMEOUT (300s)
    Same precedence as build/test flags: per-bug overrides project default.
    """
    project, sha = bug_id.split("@", 1)
    pj, bl = _load_project_meta(project)

    # Per-bug override
    if bl:
        for entry in bl:
            if entry.get("commit_after") == sha:
                v = (entry.get("c_compile") or {}).get("oracle_timeout")
                if isinstance(v, (int, float)) and v > 0:
                    return int(v)
                break

    # Per-project default
    if pj:
        v = (pj.get("c_compile") or {}).get("oracle_timeout")
        if isinstance(v, (int, float)) and v > 0:
            return int(v)

    # Hardcoded fallback
    return FALLBACK_TIMEOUT.get(project, DEFAULT_TIMEOUT)


ap = argparse.ArgumentParser()
ap.add_argument("--workers", type=int, default=8)
ap.add_argument("--csv", default=None, help="output CSV (default: oracle_validation_par_<ts>.csv)")
ap.add_argument("--resume-from", default=None, help="existing CSV to skip already-scored bugs (auto-detect newest if not given)")
ap.add_argument("--no-resume", action="store_true", help="ignore existing CSVs; re-score everything")
args = ap.parse_args()

OUT_CSV = args.csv or f"oracle_validation_par_{time.strftime('%Y%m%d_%H%M%S')}.csv"
OUT_SUMMARY = OUT_CSV.replace(".csv", ".summary.json")
LOG = OUT_CSV.replace(".csv", ".log")


def http_get(path, timeout=15):
    with urllib.request.urlopen(f"{WEBAPP}{path}", timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"), strict=False)


def http_post(path, payload, timeout=DEFAULT_TIMEOUT):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{WEBAPP}{path}", data=body,
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
        return json.loads(raw, strict=False), time.time() - t0, None
    except Exception as e:
        return None, time.time() - t0, str(e)


def score_bug(bug):
    """Score one bug — fix then buggy serially within this worker."""
    rows = []
    bug_timeout = timeout_for(bug)
    for mode in ("fix", "buggy"):
        d, lat, err = http_post("/validate_oracle", {"bug_id": bug, "mode": mode}, timeout=bug_timeout)
        if d is None:
            rows.append(dict(bug_id=bug, mode=mode, expected="", actual="",
                             matches="", verdict="",
                             latency_s=f"{lat:.1f}", error=(err or "")[:200]))
        else:
            rows.append(dict(bug_id=bug, mode=mode,
                             expected=d.get("expected", ""),
                             actual=d.get("actual", ""),
                             matches=str(d.get("matches_expectation", "")),
                             verdict=(d.get("verdict") or "")[:80],
                             latency_s=f"{lat:.1f}",
                             error=(d.get("error") or "")[:200]))
    return bug, rows


def main():
    bugs = http_get("/list_defects_bugid").get("defects", [])
    print(f"[{time.strftime('%H:%M:%S')}] total registered: {len(bugs)}", flush=True)

    # Resume: collect bug_ids already fully scored (both fix+buggy) in newest CSV
    already = set()
    if not args.no_resume:
        resume_csv = args.resume_from
        if not resume_csv:
            existing = sorted(glob.glob("oracle_validation_*.csv"), reverse=True)
            existing = [c for c in existing if c != OUT_CSV]
            resume_csv = existing[0] if existing else None
        if resume_csv and os.path.isfile(resume_csv):
            # Only treat a bug as "done" if BOTH rows have non-empty matches
            # AND no error. Errored/empty rows are retried.
            by_bug = {}
            for r in csv.DictReader(open(resume_csv)):
                by_bug.setdefault(r["bug_id"], {})[r["mode"]] = r
            for bug, m in by_bug.items():
                if {"fix", "buggy"} <= set(m.keys()):
                    f = m["fix"]; b = m["buggy"]
                    f_ok = (f.get("matches") in ("True", "False")) and not f.get("error")
                    b_ok = (b.get("matches") in ("True", "False")) and not b.get("error")
                    if f_ok and b_ok:
                        already.add(bug)
            print(f"[{time.strftime('%H:%M:%S')}] resuming from {resume_csv}: {len(already)} bugs already scored (cleanly), "
                  f"will retry {len(by_bug) - len(already)} errored/incomplete",
                  flush=True)

    todo = [b for b in bugs if b not in already]
    print(f"[{time.strftime('%H:%M:%S')}] todo: {len(todo)} bugs, workers={args.workers}", flush=True)

    fieldnames = ["bug_id", "mode", "expected", "actual", "matches",
                  "verdict", "latency_s", "error"]
    f_out = open(OUT_CSV, "w", newline="")
    w = csv.DictWriter(f_out, fieldnames=fieldnames)
    w.writeheader()
    lock = threading.Lock()

    # Pre-copy ONLY cleanly-resumed rows so the new CSV is the single source of truth.
    # Errored rows are dropped so the retried bugs replace them.
    if not args.no_resume and 'resume_csv' in locals() and resume_csv:
        for r in csv.DictReader(open(resume_csv)):
            if r["bug_id"] in already:
                w.writerow({k: r.get(k, "") for k in fieldnames})
        f_out.flush()

    done = 0
    started = time.time()
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(score_bug, b): b for b in todo}
        for fut in cf.as_completed(futs):
            bug = futs[fut]
            try:
                _, rows = fut.result()
            except Exception as e:
                rows = [dict(bug_id=bug, mode=m, expected="", actual="", matches="",
                             verdict="", latency_s="0", error=str(e)[:200])
                        for m in ("fix", "buggy")]
            with lock:
                for r in rows:
                    w.writerow(r)
                f_out.flush()
                done += 1
                tf = next((r for r in rows if r["mode"] == "fix"), {})
                tb = next((r for r in rows if r["mode"] == "buggy"), {})
                fix_act = tf.get("actual") or "ERR"
                buggy_act = tb.get("actual") or "ERR"
                fix_match = tf.get("matches") or ""
                buggy_match = tb.get("matches") or ""
                rate = done / (time.time() - started + 0.001) * 60
                err_msg = (tf.get("error") or tb.get("error") or "")[:60]
                print(f"[{time.strftime('%H:%M:%S')}] {done:3}/{len(todo)} "
                      f"{bug[:55]:55} fix={fix_act:5} buggy={buggy_act:5} "
                      f"({rate:.1f}/min) {err_msg}", flush=True)

    f_out.close()

    # Summary
    by_bug = {}
    for r in csv.DictReader(open(OUT_CSV)):
        by_bug.setdefault(r["bug_id"], {})[r["mode"]] = r
    cats = dict(passed_both=0, fix_only=0, buggy_only=0, neither=0, errored=0, unwarmed=0)
    for bug, m in by_bug.items():
        f = m.get("fix", {}); b = m.get("buggy", {})
        fe = (f.get("error") or "").lower(); be = (b.get("error") or "").lower()
        if "repo not found" in fe or "repo not found" in be: cats["unwarmed"] += 1
        elif fe or be: cats["errored"] += 1
        else:
            fok = f.get("matches") == "True"; bok = b.get("matches") == "True"
            if fok and bok: cats["passed_both"] += 1
            elif fok: cats["fix_only"] += 1
            elif bok: cats["buggy_only"] += 1
            else: cats["neither"] += 1
    summary = dict(total_bugs=len(by_bug), counts=cats,
                   passed_both_pct=round(100 * cats["passed_both"] / max(1, len(by_bug)), 1),
                   out_csv=OUT_CSV, completed_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                   wallclock_s=round(time.time() - started, 1))
    with open(OUT_SUMMARY, "w") as f:
        json.dump(summary, f, indent=2)
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
