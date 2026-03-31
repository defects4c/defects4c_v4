#!/usr/bin/env bash
# webapp_replay.sh — curl examples for all Defects4C endpoints.
# Usage: bash webapp_replay.sh [BASE_URL]
set -uo pipefail

BASE="${1:-http://127.0.0.1:8092}"
PROJECT="danmar___cppcheck"
SHA="caa6ff7c2a6ef64df53e04701944aaa4712a1915"
BUG_ID="${PROJECT}@${SHA}"

hr() { echo ""; echo "=== $1 ==="; }

hr "GET /health"
curl -s "$BASE/health" | python3 -m json.tool

hr "GET /projects"
curl -s "$BASE/projects" | python3 -m json.tool

hr "GET /list_defects_bugid"
curl -s "$BASE/list_defects_bugid" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'mode={d[\"mode\"]} total={d[\"total_count\"]} first5={d[\"defects\"][:5]}')"

hr "GET /get_defect/{id}"
curl -s "$BASE/get_defect/$BUG_ID" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'status={d.get(\"status\")} fl={d.get(\"fl_info\",{})}')"

hr "POST /api/exec — pids"
curl -s -X POST "$BASE/api/exec" -H "Content-Type: application/json" \
  -d '{"args":["pids"]}' | python3 -m json.tool

hr "POST /api/exec — bids"
curl -s -X POST "$BASE/api/exec" -H "Content-Type: application/json" \
  -d "{\"args\":[\"bids\",\"-p\",\"$PROJECT\"]}" | python3 -c "import sys,json; d=json.load(sys.stdin); lines=d.get('stdout','').strip().split('\n'); print(f'rc={d[\"returncode\"]} bugs={len(lines)}')"

hr "POST /api/exec — info (project)"
curl -s -X POST "$BASE/api/exec" -H "Content-Type: application/json" \
  -d "{\"args\":[\"info\",\"-p\",\"$PROJECT\"]}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('stdout','')[:200])"

hr "POST /api/exec — info (bug)"
curl -s -X POST "$BASE/api/exec" -H "Content-Type: application/json" \
  -d "{\"args\":[\"info\",\"-p\",\"$PROJECT\",\"-v\",\"$SHA\"]}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('stdout','')[:300])"

hr "POST /api/exec — checkout"
curl -s -X POST "$BASE/api/exec" -H "Content-Type: application/json" \
  -d "{\"args\":[\"checkout\",\"-p\",\"$PROJECT\",\"-v\",\"$SHA\"]}" | python3 -m json.tool

hr "POST /api/exec — compile"
curl -s -X POST "$BASE/api/exec" -H "Content-Type: application/json" \
  -d "{\"args\":[\"compile\",\"-p\",\"$PROJECT\",\"-v\",\"$SHA\"]}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'rc={d[\"returncode\"]} out={len(d.get(\"stdout\",\"\"))}c')"

hr "POST /api/exec — test"
curl -s -X POST "$BASE/api/exec" -H "Content-Type: application/json" \
  -d "{\"args\":[\"test\",\"-p\",\"$PROJECT\",\"-v\",\"$SHA\"]}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'rc={d[\"returncode\"]} out={len(d.get(\"stdout\",\"\"))}c')"

hr "POST /api/exec-shell — echo"
curl -s -X POST "$BASE/api/exec-shell" -H "Content-Type: application/json" \
  -d '{"cmd":"echo hello-from-replay","cwd":"/tmp"}' | python3 -m json.tool

hr "POST /api/exec-shell — git block test"
curl -s -X POST "$BASE/api/exec-shell" -H "Content-Type: application/json" \
  -d '{"cmd":"git commit -m test"}' | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'blocked: {\"blocked\" in d.get(\"stderr\",\"\").lower()}')"

hr "POST /api/upload + GET /api/download"
curl -s -X POST "$BASE/api/upload" -F "path=replay_test.txt" -F "file=@/dev/stdin" <<< "test content from replay" | python3 -m json.tool
curl -s "$BASE/api/download?path=replay_test.txt" && echo ""

hr "POST /build_patch — method=direct"
curl -s -X POST "$BASE/build_patch" -H "Content-Type: application/json" \
  -d "{\"bug_id\":\"$BUG_ID\",\"llm_response\":\"\`\`\`cpp\nint x=0;\n\`\`\`\",\"method\":\"direct\",\"generate_diff\":true}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'success={d.get(\"success\")} method={d.get(\"method\",\"?\")} md5={d.get(\"md5_hash\",\"?\")[:8]}')"

hr "POST /build_patch — method=replace_json"
curl -s -X POST "$BASE/build_patch" -H "Content-Type: application/json" \
  -d "{\"bug_id\":\"$BUG_ID\",\"llm_response\":\"{\\\"line_start\\\":1,\\\"line_end\\\":2,\\\"content\\\":\\\"// replaced\\\\n\\\"}\",\"method\":\"replace_json\"}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'success={d.get(\"success\")} method={d.get(\"method\",\"?\")}')"

hr "POST /build_patch — method=full_file"
curl -s -X POST "$BASE/build_patch" -H "Content-Type: application/json" \
  -d "{\"bug_id\":\"$BUG_ID\",\"llm_response\":\"// full file\\nint main(){}\\n\",\"method\":\"full_file\"}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'success={d.get(\"success\")} method={d.get(\"method\",\"?\")}')"

hr "POST /fix + GET /status"
PATCH_RESULT=$(curl -s -X POST "$BASE/build_patch" -H "Content-Type: application/json" \
  -d "{\"bug_id\":\"$BUG_ID\",\"llm_response\":\"\`\`\`cpp\nint x=0;\n\`\`\`\",\"method\":\"direct\",\"persist_flag\":true}")
FIX_P=$(echo "$PATCH_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('fix_p',''))")
if [ -n "$FIX_P" ]; then
    FIX_RESULT=$(curl -s -X POST "$BASE/fix" -H "Content-Type: application/json" \
      -d "{\"bug_id\":\"$BUG_ID\",\"patch_path\":\"$FIX_P\"}")
    HANDLE=$(echo "$FIX_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('handle',''))")
    echo "  handle=$HANDLE"
    sleep 2
    curl -s "$BASE/status/$HANDLE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'status={d.get(\"status\")} rc={d.get(\"return_code\",\"?\")}')"
fi

hr "POST /validate_oracle — fix mode"
curl -s -X POST "$BASE/validate_oracle" -H "Content-Type: application/json" \
  -d "{\"bug_id\":\"$BUG_ID\",\"mode\":\"fix\"}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'success={d.get(\"success\")} expected={d.get(\"expected\")} actual={d.get(\"actual\")} match={d.get(\"matches_expectation\")}')"

hr "POST /validate_oracle — buggy mode"
curl -s -X POST "$BASE/validate_oracle" -H "Content-Type: application/json" \
  -d "{\"bug_id\":\"$BUG_ID\",\"mode\":\"buggy\"}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'success={d.get(\"success\")} expected={d.get(\"expected\")} actual={d.get(\"actual\")} match={d.get(\"matches_expectation\")}')"

echo ""
echo "=== DONE ==="
