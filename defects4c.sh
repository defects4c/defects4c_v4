#!/usr/bin/env bash
set -uo pipefail
DEFECTS4C_URL="${DEFECTS4C_URL:-http://localhost:8095}"
CURL_TIMEOUT="${DEFECTS4C_CURL_TIMEOUT:-1900}"
POLL_INTERVAL="${DEFECTS4C_POLL_INTERVAL:-5}"
VERBOSE="${DEFECTS4C_VERBOSE:-0}"

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

Environment:
  DEFECTS4C_URL              Server URL (default: http://localhost:8095)
  DEFECTS4C_CURL_TIMEOUT     Curl timeout in seconds (default: 1900)
  DEFECTS4C_VERBOSE          Set to 1 for debug output (includes curl --verbose)

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

dbg() { [ "$VERBOSE" = "1" ] && echo "[DEBUG] $*" >&2; }

# Build extra curl flags: add --verbose when VERBOSE=1
CURL_EXTRA=""
[ "$VERBOSE" = "1" ] && CURL_EXTRA="--verbose"

# ── Shared error handler ──
handle_curl_error() {
    local curl_rc="$1" target_url="$2" payload="$3" tmpfile="$4" errfile="$5"
    echo "ERROR: request failed (curl exit code: $curl_rc)" >&2
    echo "  URL:     $target_url" >&2
    echo "  Payload: $payload" >&2
    case $curl_rc in
        6)  echo "  Reason:  Could not resolve host" >&2 ;;
        7)  echo "  Reason:  Failed to connect to server" >&2 ;;
        22) echo "  Reason:  HTTP error" >&2 ;;
        23) echo "  Reason:  Write error — curl could not save response to disk" >&2
            echo "  Disk:    $(df -h /tmp 2>/dev/null | tail -1)" >&2
            echo "  TMPFILE: $tmpfile ($(wc -c < "$tmpfile" 2>/dev/null || echo '?') bytes written)" >&2
            if [ -s "$tmpfile" ]; then
                echo "  Partial response (first 500 chars):" >&2
                head -c 500 "$tmpfile" >&2
                echo >&2
            fi
            ;;
        28) echo "  Reason:  Operation timed out (${CURL_TIMEOUT}s)" >&2 ;;
        35) echo "  Reason:  SSL/TLS handshake failure" >&2 ;;
        52) echo "  Reason:  Empty reply from server" >&2 ;;
        56) echo "  Reason:  Recv failure (connection reset)" >&2 ;;
        *)  echo "  Reason:  curl error $curl_rc (see https://curl.se/libcurl/c/libcurl-errors.html)" >&2 ;;
    esac
    if [ -s "$errfile" ]; then
        echo "  Curl stderr:" >&2
        cat "$errfile" >&2
    fi
}

# ── Health check ──
dbg "Health check: ${DEFECTS4C_URL}/health"
if ! curl -s --max-time 3 "${DEFECTS4C_URL}/health" >/dev/null 2>&1; then
    echo "ERROR: cannot reach webapp at ${DEFECTS4C_URL}" >&2
    echo "  docker compose up -d" >&2
    exit 127
fi
dbg "Health check passed"

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
    ERRFILE=$(mktemp /tmp/d4c_err.XXXXXX)
    trap 'rm -f "$TMPFILE" "$ERRFILE"' EXIT

    TARGET_URL="${DEFECTS4C_URL}/api/exec"
    dbg "POST $TARGET_URL"
    dbg "Payload: $PAYLOAD"
    dbg "Timeout: ${CURL_TIMEOUT}s"

    # shellcheck disable=SC2086
    HTTP_CODE=$(curl -s $CURL_EXTRA -o "$TMPFILE" -w "%{http_code}" \
        --max-time "$CURL_TIMEOUT" -X POST \
        -H "Content-Type: application/json" \
        -d "$PAYLOAD" "$TARGET_URL" 2>"$ERRFILE")
    CURL_RC=$?

    if [ $CURL_RC -ne 0 ]; then
        handle_curl_error "$CURL_RC" "$TARGET_URL" "$PAYLOAD" "$TMPFILE" "$ERRFILE"
        exit 1
    fi

    dbg "HTTP $HTTP_CODE, response size: $(wc -c < "$TMPFILE") bytes"

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
ERRFILE=$(mktemp /tmp/d4c_err.XXXXXX)
trap 'rm -f "$TMPFILE" "$ERRFILE"' EXIT

TARGET_URL="${DEFECTS4C_URL}/api/exec"
dbg "POST $TARGET_URL"
dbg "Payload: $PAYLOAD"
dbg "Timeout: ${CURL_TIMEOUT}s"

# shellcheck disable=SC2086
HTTP_CODE=$(curl -s $CURL_EXTRA -o "$TMPFILE" -w "%{http_code}" \
    --max-time "$CURL_TIMEOUT" -X POST \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD" "$TARGET_URL" 2>"$ERRFILE")
CURL_RC=$?

if [ $CURL_RC -ne 0 ]; then
    handle_curl_error "$CURL_RC" "$TARGET_URL" "$PAYLOAD" "$TMPFILE" "$ERRFILE"
    exit 1
fi

dbg "HTTP $HTTP_CODE, response size: $(wc -c < "$TMPFILE") bytes"
dbg "Response body: $(head -c 500 "$TMPFILE")"

if [ "$HTTP_CODE" -ge 400 ]; then
    echo "ERROR: HTTP ${HTTP_CODE}" >&2
    echo "  URL:     $TARGET_URL" >&2
    echo "  Payload: $PAYLOAD" >&2
    echo "  Response: $(cat "$TMPFILE")" >&2
    exit 1
fi

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

