#!/usr/bin/env python3
"""Oracle batch sweep — for every registered bug, call /validate_oracle in
both `fix` and `buggy` modes; record a CSV row per call.

Serial. Gentle on the in-flight agents (1 slot at a time per bug).
"""
import csv, json, os, sys, time, urllib.request

WEBAPP = os.environ.get("WEBAPP", "http://127.0.0.1:8095")
TIMEOUT = int(os.environ.get("ORACLE_TIMEOUT", "300"))
OUT_CSV = os.environ.get("OUT_CSV", f"oracle_validation_{time.strftime('%Y%m%d_%H%M%S')}.csv")
OUT_SUMMARY = OUT_CSV.replace(".csv", ".summary.json")


def http_get(path, timeout=10):
    with urllib.request.urlopen(f"{WEBAPP}{path}", timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"), strict=False)


def http_post(path, payload, timeout=TIMEOUT):
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


def main():
    print(f"WEBAPP={WEBAPP} TIMEOUT={TIMEOUT}s OUT_CSV={OUT_CSV}", flush=True)
    bugs = http_get("/list_defects_bugid", timeout=15).get("defects", [])
    print(f"total bugs registered: {len(bugs)}", flush=True)

    if not bugs:
        print("no bugs — abort", flush=True)
        sys.exit(1)

    # Per-bug categorization
    counts = dict(passed_both=0, fix_only=0, buggy_only=0, neither=0, errored=0, unwarmed=0)
    by_bug = {}  # bug_id -> {fix: row, buggy: row}

    fieldnames = ["bug_id", "mode", "expected", "actual", "matches",
                  "verdict", "latency_s", "error"]
    with open(OUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()

        for i, bug in enumerate(bugs, 1):
            by_bug[bug] = {}
            for mode in ("fix", "buggy"):
                d, latency, err = http_post("/validate_oracle",
                                            {"bug_id": bug, "mode": mode})
                if d is None:
                    row = dict(bug_id=bug, mode=mode, expected="",
                               actual="", matches="", verdict="",
                               latency_s=f"{latency:.1f}", error=err[:200])
                else:
                    row = dict(bug_id=bug, mode=mode,
                               expected=d.get("expected", ""),
                               actual=d.get("actual", ""),
                               matches=str(d.get("matches_expectation", "")),
                               verdict=(d.get("verdict") or "")[:80],
                               latency_s=f"{latency:.1f}",
                               error=(d.get("error") or "")[:200])
                w.writerow(row)
                f.flush()
                by_bug[bug][mode] = row
                print(f"[{i}/{len(bugs)}] {bug[:50]:50} {mode:6} "
                      f"{row['actual'] or 'ERR':5} matches={row['matches']:5} "
                      f"({row['latency_s']}s) "
                      f"{(row['error'] or row['verdict'])[:50]}", flush=True)

            # Categorize this bug
            fix_row = by_bug[bug].get("fix", {})
            buggy_row = by_bug[bug].get("buggy", {})
            fix_err = (fix_row.get("error") or "")
            buggy_err = (buggy_row.get("error") or "")
            if ("not found" in fix_err.lower() or "run warmup" in fix_err.lower() or
                "not found" in buggy_err.lower() or "run warmup" in buggy_err.lower()):
                counts["unwarmed"] += 1
            elif fix_err or buggy_err:
                counts["errored"] += 1
            else:
                fix_ok = fix_row.get("matches") == "True"
                buggy_ok = buggy_row.get("matches") == "True"
                if fix_ok and buggy_ok:
                    counts["passed_both"] += 1
                elif fix_ok:
                    counts["fix_only"] += 1
                elif buggy_ok:
                    counts["buggy_only"] += 1
                else:
                    counts["neither"] += 1

            # Brief sleep — gentle on the slots used by in-flight agents
            time.sleep(0.2)

    summary = dict(
        total_bugs=len(bugs),
        counts=counts,
        passed_both_pct=round(100 * counts["passed_both"] / len(bugs), 1),
        out_csv=OUT_CSV,
        completed_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    with open(OUT_SUMMARY, "w") as f:
        json.dump(summary, f, indent=2)
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
