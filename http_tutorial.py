#!/usr/bin/env python3
"""
http_tutorial.py — Defects4C HTTP API tutorial.

Demonstrates all API endpoints against localhost:8095.

Usage:
    python3 http_tutorial.py                 # full workflow (random bug)
    python3 http_tutorial.py --bug 0         # specific bug index
    python3 http_tutorial.py --list          # show available bugs
    python3 http_tutorial.py --oracle 0      # oracle validation
    python3 http_tutorial.py --d4j-demo      # D4J /api/exec demo
    python3 http_tutorial.py --patch-demo    # patch methods demo
"""

import json, os, random, sys, time
import requests

BASE = os.getenv("DEFECTS4C_URL", "http://127.0.0.1:8095")


def _step(n, t): print(f"\n{'='*60}\n  Step {n}: {t}\n{'='*60}")

def _show(label, data):
    if not isinstance(data, dict): print(f"  [{label}] {data}"); return
    rc = data.get("returncode", data.get("return_code", "?"))
    print(f"  [{label}] rc={rc}")
    for k in ("stdout","stderr","error","fix_log","verdict"):
        v = data.get(k, "")
        if v:
            for line in str(v).strip().splitlines()[:6]:
                print(f"    {k}: {line[:140]}")

def _post(path, body):
    try: return requests.post(f"{BASE}{path}", json=body, timeout=1800).json()
    except Exception as e: return {"returncode": 1, "stderr": f"HTTP: {e}"}

def _get(path):
    try: return requests.get(f"{BASE}{path}", timeout=30).json()
    except Exception as e: return {"error": str(e)}


def get_all_bugs():
    """Get all bugs from the webapp. Returns (all_bugs, selected_bugs)."""
    data = _get("/list_defects_bugid")
    if data.get("status") != "success":
        print(f"  ERROR: {data}")
        return [], []
    return data.get("defects", []), data.get("selected", [])


def oracle_validate(bug_id):
    """Run oracle validation: fix mode (expect PASS) and buggy mode (expect FAIL)."""
    print(f"\n  Oracle: {bug_id}")
    for mode, expect in [("fix", "PASS"), ("buggy", "FAIL")]:
        r = _post("/validate_oracle", {"bug_id": bug_id, "mode": mode})
        if r.get("success"):
            actual = r.get("actual", "?")
            match = r.get("matches_expectation", False)
            print(f"    {mode}: expected={expect} actual={actual} {'✅' if match else '❌'}")
        else:
            print(f"    {mode}: ERROR — {r.get('error', 'unknown')}")


def demo_patch_methods(bug_id):
    """Demo all three patch submission methods: diff, replace-json, full-file."""
    _step("P", f"Patch methods demo for {bug_id}")

    # Get bug metadata
    dd = _get(f"/get_defect/{bug_id}")
    if dd.get("status") != "success":
        print(f"  ERROR: {dd}"); return
    fl = dd.get("fl_info", {})
    src = fl.get("src_file", "")
    hs = fl.get("hunk_start", 1)
    he = fl.get("hunk_end", 1)
    print(f"  src={src} hunk=[{hs},{he}]")

    # Method 1: direct (full code snippet in markdown fenced block)
    print(f"\n  --- Method 1: direct (markdown code block) ---")
    r = _post("/build_patch", {
        "bug_id": bug_id,
        "llm_response": "```cpp\n// placeholder fix\nint x = 0;\n```",
        "method": "direct",
        "generate_diff": True,
    })
    print(f"  success={r.get('success')}  md5={r.get('md5_hash','?')[:8]}")

    # Method 2: diff (unified diff format)
    print(f"\n  --- Method 2: diff (unified diff) ---")
    r = _post("/build_patch", {
        "bug_id": bug_id,
        "llm_response": "--- a/old.cpp\n+++ b/new.cpp\n@@ -1,1 +1,1 @@\n-old line\n+new line",
        "method": "diff",
        "generate_diff": True,
    })
    print(f"  success={r.get('success')}  error={r.get('error','none')}")

    # Method 3: replace-json (line range + content)
    print(f"\n  --- Method 3: replace JSON (line_start, line_end, content) ---")
    r = _post("/build_patch", {
        "bug_id": bug_id,
        "llm_response": json.dumps({
            "line_start": hs,
            "line_end": he,
            "content": "// replaced region\nint fixed = 1;\n",
        }),
        "method": "replace_json",
        "generate_diff": True,
    })
    print(f"  success={r.get('success')}  md5={r.get('md5_hash','?')[:8]}")

    # Method 4: full file content
    print(f"\n  --- Method 4: full file (entire file content) ---")
    r = _post("/build_patch", {
        "bug_id": bug_id,
        "llm_response": "// This is the entire file content\nint main() { return 0; }\n",
        "method": "full_file",
        "generate_diff": True,
    })
    print(f"  success={r.get('success')}  md5={r.get('md5_hash','?')[:8]}")


def demo_d4j(bugs):
    """Demo D4J-compatible /api/exec interface."""
    if not bugs:
        print("  No bugs available"); return
    bug_id = bugs[0]
    project, sha = bug_id.split("@")
    print(f"\n=== D4J /api/exec demo: {bug_id[:40]} ===\n")
    for label, args in [
        ("pids",       ["pids"]),
        ("bids",       ["bids", "-p", project]),
        ("info",       ["info", "-p", project, "-v", sha]),
        ("checkout",   ["checkout", "-p", project, "-v", sha]),
        ("compile",    ["compile", "-p", project, "-v", sha]),
        ("test",       ["test", "-p", project, "-v", sha]),
    ]:
        print(f"\n--- {label} ---")
        _show(label, _post("/api/exec", {"args": args}))


def main():
    bugs, selected = get_all_bugs()

    for i, a in enumerate(sys.argv):
        if a == "--list":
            print(f"\n  Bugs ({len(bugs)}, selected={len(selected)}):")
            for j, b in enumerate(bugs[:20]):
                sel = " *" if b in selected else ""
                print(f"  [{j}] {b}{sel}")
            if len(bugs) > 20:
                print(f"  ... and {len(bugs)-20} more")
            return
        if a == "--oracle":
            idx = int(sys.argv[i+1]) if i+1 < len(sys.argv) else 0
            pool = selected or bugs
            if idx < len(pool):
                return oracle_validate(pool[idx])
            print(f"  Bug index {idx} out of range (0..{len(pool)-1})")
            return
        if a == "--d4j-demo": return demo_d4j(selected or bugs)
        if a == "--patch-demo":
            idx = int(sys.argv[i+1]) if i+1 < len(sys.argv) else 0
            pool = selected or bugs
            if idx < len(pool):
                return demo_patch_methods(pool[idx])
            return
        if a == "--bug":
            bug_idx = int(sys.argv[i+1]) if i+1 < len(sys.argv) else 0

    # Default: full workflow — prefer selected (warmed-up) bugs
    _step(0, "Health check")
    try:
        h = requests.get(f"{BASE}/health", timeout=5).json()
        print(f"  Status: {h.get('status')}")
        if h.get("status") != "ok": return
    except Exception as e:
        print(f"  ERROR: {e}"); return

    pool = selected or bugs
    _step(1, f"List bugs ({len(bugs)} total, {len(selected)} selected/warmed)")
    if not pool: print("  No bugs loaded"); return

    bug_idx = locals().get("bug_idx", random.randint(0, len(pool)-1))
    bug_idx %= len(pool)
    bug_id = pool[bug_idx]
    print(f"  [{bug_idx}] {bug_id}")

    _step(2, "Get defect metadata")
    dd = _get(f"/get_defect/{bug_id}")
    if dd.get("status") != "success": print(f"  ERROR: {dd}"); return
    fl = dd.get("fl_info", {})
    print(f"  src: {fl.get('src_file')}  hunk: [{fl.get('hunk_start')},{fl.get('hunk_end')}]")

    project, sha = bug_id.split("@")

    _step(3, "Checkout (restore buggy source)")
    r = _post("/api/exec", {"args": ["checkout", "-p", project, "-v", sha]})
    _show("checkout", r)

    _step(4, "Compile")
    r = _post("/api/exec", {"args": ["compile", "-p", project, "-v", sha]})
    _show("compile", r)
    if r.get("returncode", 1) != 0:
        print("  ⚠️  Compile failed"); return

    _step(5, "Trigger test (should FAIL on buggy code)")
    r = _post("/api/exec", {"args": ["test", "-p", project, "-v", sha]})
    _show("test", r)

    _step(6, "Oracle validation")
    oracle_validate(bug_id)

    _step(7, "Build patch (dummy)")
    pr = _post("/build_patch", {"bug_id": bug_id,
        "llm_response": "```cpp\n// dummy fix\n```", "method": "direct",
        "generate_diff": True, "persist_flag": True})
    print(f"  success={pr.get('success')}")
    if pr.get("success"):
        print(f"  patch: {pr.get('fix_p')}")

    _step(8, "Submit fix")
    if pr.get("success"):
        fr = _post("/fix", {"bug_id": bug_id, "patch_path": pr["fix_p"]})
        handle = fr.get("handle")
        print(f"  handle: {handle}")

        _step(9, "Poll status")
        for i in range(10):
            sd = _get(f"/status/{handle}")
            s = sd.get("status", "?")
            print(f"  [{i+1}] status={s}")
            if s in ("completed", "failed"): break
            time.sleep(5)

    print(f"\n{'='*60}\n  Done.\n{'='*60}\n")


if __name__ == "__main__":
    main()
