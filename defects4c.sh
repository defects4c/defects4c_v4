#!/usr/bin/env bash
set -uo pipefail
DEFECTS4C_URL="${DEFECTS4C_URL:-http://localhost:8095}"
CURL_TIMEOUT="${DEFECTS4C_CURL_TIMEOUT:-1900}"
POLL_INTERVAL="${DEFECTS4C_POLL_INTERVAL:-5}"

if [ $# -eq 0 ]; then
    cat <<'USAGE'
usage: defects4c <command> [options]

Commands:
  pids                                     List project IDs
  bids       -p <project>                  List bug SHAs
  info       -p <project> [-v <sha>]       View project/bug info
  checkout   -p <project> -v <sha> [-f]    Checkout buggy source
  compile    -p <project> -v <sha>         Check build exists (warmup)
  test       -p <project> -v <sha>         Run trigger tests (sync, waits for result)
  test -r    -p <project> -v <sha>         Run regression tests (all)
  reproduce  -p <project> -v <sha>         Full reproduce (async, polls until done)
  shell      <shell_command>               Run arbitrary shell command in container

Examples:
  defects4c pids
  defects4c info -p danmar___cppcheck -v caa6ff7
  defects4c compile -p danmar___cppcheck -v caa6ff7
  defects4c test -p danmar___cppcheck -v caa6ff7
  defects4c reproduce -p danmar___cppcheck -v caa6ff7
  defects4c shell "ls /out/danmar___cppcheck/logs/"
  defects4c shell "cat /out/danmar___cppcheck/logs/caa6ff7.log"
USAGE
    exit 1
fi

# ── Health check ──
if ! curl -s --max-time 3 "${DEFECTS4C_URL}/health" >/dev/null 2>&1; then
    echo "ERROR: cannot reach webapp at ${DEFECTS4C_URL}" >&2
    echo "  docker compose up -d" >&2
    exit 127
fi

# ── Shell subcommand: send {"cmd": "..."} to /api/exec ──
if [ "$1" = "shell" ]; then
    shift
    if [ $# -eq 0 ]; then
        echo "ERROR: shell subcommand requires a command string" >&2
        exit 1
    fi
    SHELL_CMD="$*"
    PAYLOAD=$(python3 -c "import json,sys; print(json.dumps({'cmd': sys.argv[1]}))" "$SHELL_CMD")

    TMPFILE=$(mktemp /tmp/d4c_resp.XXXXXX)
    trap 'rm -f "$TMPFILE"' EXIT

    HTTP_CODE=$(curl -s -o "$TMPFILE" -w "%{http_code}" \
        --max-time "$CURL_TIMEOUT" -X POST \
        -H "Content-Type: application/json" \
        -d "$PAYLOAD" "${DEFECTS4C_URL}/api/exec") || {
        echo "ERROR: request failed" >&2; exit 1
    }

    [ "$HTTP_CODE" -ge 500 ] && { echo "ERROR: HTTP ${HTTP_CODE}" >&2; cat "$TMPFILE" >&2; exit 1; }

    python3 -c "
import sys, json
with open('$TMPFILE') as f: data = json.load(f)
stdout = data.get('stdout', '')
stderr = data.get('stderr', '')
rc = data.get('returncode', 1)
if stdout: sys.stdout.write(stdout); sys.stdout.flush()
if stderr: sys.stderr.write(stderr); sys.stderr.flush()
sys.exit(rc)
"
    exit $?
fi

# ── D4J-compatible args mode ──
PAYLOAD=$(python3 -c "import json,sys; print(json.dumps({'args':sys.argv[1:]}))" "$@")

TMPFILE=$(mktemp /tmp/d4c_resp.XXXXXX)
trap 'rm -f "$TMPFILE"' EXIT

HTTP_CODE=$(curl -s -o "$TMPFILE" -w "%{http_code}" \
    --max-time "$CURL_TIMEOUT" -X POST \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD" "${DEFECTS4C_URL}/api/exec") || {
    echo "ERROR: request failed" >&2; exit 1
}

[ "$HTTP_CODE" -ge 500 ] && { echo "ERROR: HTTP ${HTTP_CODE}" >&2; cat "$TMPFILE" >&2; exit 1; }

# ── Parse response ──
export TMPFILE POLL_INTERVAL DEFECTS4C_URL
python3 << 'PYEOF'
import sys, json, time, urllib.request, os

url_base = os.environ.get("DEFECTS4C_URL", "http://localhost:8095")
poll_interval = int(os.environ.get("POLL_INTERVAL", "5"))

with open(os.environ["TMPFILE"]) as f:
    data = json.load(f)

stdout = data.get("stdout", "")
stderr = data.get("stderr", "")
rc = data.get("returncode", 1)
handle = data.get("handle", "")

# ═══════════════════════════════════════════════════════
#  Synchronous response (test, checkout, compile, info…)
# ═══════════════════════════════════════════════════════
if not handle:
    # -- trigger test returns passed/test_status/log_file/log_content directly --
    passed = data.get("passed")
    test_status = data.get("test_status", "")
    log_file = data.get("log_file", "")
    log_content = data.get("log_content", "")

    if passed is not None:
        verdict = "PASS" if passed else "FAIL"
        sys.stdout.write(f"Result: {verdict} (rc={rc})\n")
        if test_status:
            sys.stdout.write(f"Test status: {test_status}\n")
        if log_file:
            sys.stderr.write(f"log_file={log_file}\n")
        if data.get("status_file"):
            sys.stderr.write(f"status_file={data['status_file']}\n")
        if data.get("msg_file"):
            sys.stderr.write(f"msg_file={data['msg_file']}\n")
        # Print log content
        if log_content:
            sys.stdout.write("\n--- log output ---\n")
            sys.stdout.write(log_content)
            if not log_content.endswith("\n"):
                sys.stdout.write("\n")
            sys.stdout.write("--- end log ---\n")
        sys.stdout.flush()
        sys.stderr.flush()
        sys.exit(0 if passed else 1)

    # -- other sync commands (info, checkout, compile, etc.) --
    if stdout: sys.stdout.write(stdout); sys.stdout.flush()
    if stderr: sys.stderr.write(stderr); sys.stderr.flush()
    sys.exit(rc)

# ═══════════════════════════════════════════════════════
#  Async response (reproduce) — poll /status/{handle}
# ═══════════════════════════════════════════════════════
sys.stderr.write(f"[async] handle={handle} — polling for completion...\n")
sys.stderr.flush()

if stdout:
    sys.stdout.write(stdout); sys.stdout.flush()

while True:
    time.sleep(poll_interval)
    try:
        req = urllib.request.Request(f"{url_base}/status/{handle}")
        with urllib.request.urlopen(req, timeout=10) as resp:
            status_data = json.loads(resp.read().decode())
    except Exception as e:
        sys.stderr.write(f"[poll] error: {e}\n"); sys.stderr.flush()
        continue

    status = status_data.get("status", "unknown")

    if status in ("queued", "running"):
        sys.stderr.write(f"[poll] status={status}\n"); sys.stderr.flush()
        continue

    # ── Terminal state ──
    rc = status_data.get("return_code", 1)
    log_file = status_data.get("log_file", "")
    error = status_data.get("error", "")

    sys.stdout.write(f"Completed: status={status} rc={rc}\n")

    # Cat the log file
    if log_file:
        sys.stderr.write(f"log_file={log_file}\n"); sys.stderr.flush()
        try:
            payload = json.dumps({"cmd": f"cat {log_file}"}).encode()
            req = urllib.request.Request(
                f"{url_base}/api/exec",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                log_data = json.loads(resp.read().decode())
            log_content = log_data.get("stdout", "")
            if log_content:
                sys.stdout.write("\n--- log output ---\n")
                sys.stdout.write(log_content)
                if not log_content.endswith("\n"):
                    sys.stdout.write("\n")
                sys.stdout.write("--- end log ---\n")
                sys.stdout.flush()
        except Exception as e:
            sys.stderr.write(f"[log] could not cat log: {e}\n")

    if error:
        sys.stderr.write(error); sys.stderr.flush()
        if not error.endswith("\n"):
            sys.stderr.write("\n")

    sys.exit(rc)
PYEOF

