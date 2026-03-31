#!/usr/bin/env bash
set -uo pipefail
DEFECTS4C_URL="${DEFECTS4C_URL:-http://localhost:8092}"
CURL_TIMEOUT="${DEFECTS4C_CURL_TIMEOUT:-1900}"

if [ $# -eq 0 ]; then
    cat <<'USAGE'
usage: defects4c <command> [options]

Commands:
  pids                                     List project IDs
  bids       -p <project>                  List bug SHAs
  info       -p <project> [-v <sha>]       View project/bug info
  checkout   -p <project> -v <sha> [-f]    Checkout buggy source
  compile    -p <project> -v <sha>         Compile (rebuild)
  test       -p <project> -v <sha>         Run trigger tests
  test -r    -p <project> -v <sha>         Run regression tests (all)
  reproduce  -p <project> -v <sha>         Full reproduce

Examples:
  defects4c pids
  defects4c info -p danmar___cppcheck -v caa6ff7
  defects4c compile -p danmar___cppcheck -v caa6ff7
  defects4c test -p danmar___cppcheck -v caa6ff7
  defects4c test -r -p danmar___cppcheck -v caa6ff7
USAGE
    exit 1
fi

if ! curl -s --max-time 3 "${DEFECTS4C_URL}/health" >/dev/null 2>&1; then
    echo "ERROR: cannot reach webapp at ${DEFECTS4C_URL}" >&2
    echo "  docker compose up -d" >&2
    exit 127
fi

# Build JSON args array
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

# Parse and replay response
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
