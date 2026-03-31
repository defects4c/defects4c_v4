#!/usr/bin/env bash
set -uo pipefail
#
# validate_oracle_batch.sh — Batch oracle validation for all warmed-up bugs.
#
# Two validation modes per bug:
#   1. Direct checkout: checkout commit_after src_file, rebuild, test → expect PASS
#   2. Diff-as-patch:   get diff(commit_before, commit_after) for src_file,
#                        submit as patch via /build_patch, run /fix → expect PASS
#
# Uses the buglist from the webapp (mini/full/custom).
#
# Usage:
#   bash validate_oracle_batch.sh               # validate all
#   bash validate_oracle_batch.sh --mode direct  # only direct checkout
#   bash validate_oracle_batch.sh --mode patch   # only diff-as-patch
#   bash validate_oracle_batch.sh --project danmar___cppcheck  # one project

BASE_URL="${DEFECTS4C_URL:-http://127.0.0.1:8095}"
MODE="${1:-all}"        # all | direct | patch
PROJECT_FILTER=""
TIMEOUT=30

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode) MODE="$2"; shift 2;;
        --project) PROJECT_FILTER="$2"; shift 2;;
        *) shift;;
    esac
done

echo "========================================"
echo "  Oracle Batch Validation"
echo "  URL: $BASE_URL"
echo "  Mode: $MODE"
echo "  Project filter: ${PROJECT_FILTER:-all}"
echo "========================================"

# Get bug list from webapp
BUGS=$(curl -s "$BASE_URL/list_defects_bugid" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(data.get('mode', '?'), file=sys.stderr)
for b in data.get('defects', []):
    print(b)
" 2>/tmp/d4c_mode.txt)

BUGLIST_MODE=$(cat /tmp/d4c_mode.txt 2>/dev/null || echo "?")
TOTAL=$(echo "$BUGS" | wc -l)
echo "  Buglist mode: $BUGLIST_MODE"
echo "  Total bugs: $TOTAL"
echo "========================================"

PASS_DIRECT=0; FAIL_DIRECT=0; SKIP_DIRECT=0
PASS_PATCH=0; FAIL_PATCH=0; SKIP_PATCH=0
COUNT=0

for bug_id in $BUGS; do
    project=$(echo "$bug_id" | cut -d@ -f1)
    sha=$(echo "$bug_id" | cut -d@ -f2)

    # Project filter
    if [[ -n "$PROJECT_FILTER" ]] && [[ "$project" != "$PROJECT_FILTER" ]]; then
        continue
    fi

    COUNT=$((COUNT+1))
    echo ""
    echo "--- [$COUNT] $bug_id ---"

    # ── Mode 1: Direct checkout (validate_oracle fix + buggy) ──
    if [[ "$MODE" == "all" ]] || [[ "$MODE" == "direct" ]]; then
        result=$(curl -s -X POST "$BASE_URL/validate_oracle" \
            -H "Content-Type: application/json" \
            -d "{\"bug_id\":\"$bug_id\",\"mode\":\"fix\"}" \
            --max-time $TIMEOUT)

        success=$(echo "$result" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('success',False))" 2>/dev/null)
        if [[ "$success" == "True" ]]; then
            match=$(echo "$result" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('matches_expectation',False))" 2>/dev/null)
            if [[ "$match" == "True" ]]; then
                echo "  direct/fix: ✅ PASS"
                PASS_DIRECT=$((PASS_DIRECT+1))
            else
                echo "  direct/fix: ❌ FAIL"
                FAIL_DIRECT=$((FAIL_DIRECT+1))
            fi
        else
            error=$(echo "$result" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error','')[:80])" 2>/dev/null)
            echo "  direct/fix: SKIP ($error)"
            SKIP_DIRECT=$((SKIP_DIRECT+1))
        fi
    fi

    # ── Mode 2: Diff-as-patch ──
    if [[ "$MODE" == "all" ]] || [[ "$MODE" == "patch" ]]; then
        # Get diff between commit_before and commit_after for src_file
        # via exec-shell inside the container
        diff_result=$(curl -s -X POST "$BASE_URL/api/exec-shell" \
            -H "Content-Type: application/json" \
            -d "{\"cmd\":\"cd /out/$project/git_repo_dir_$sha && git diff $sha~1 $sha 2>/dev/null || echo NO_DIFF\",\"cwd\":\"/tmp\"}" \
            --max-time $TIMEOUT)

        diff_text=$(echo "$diff_result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('stdout',''))" 2>/dev/null)

        if [[ -z "$diff_text" ]] || [[ "$diff_text" == *"NO_DIFF"* ]]; then
            echo "  patch: SKIP (no diff available)"
            SKIP_PATCH=$((SKIP_PATCH+1))
        else
            # Submit diff as patch
            patch_result=$(curl -s -X POST "$BASE_URL/build_patch" \
                -H "Content-Type: application/json" \
                -d "$(python3 -c "
import sys,json
diff = '''$diff_text'''[:50000]
print(json.dumps({'bug_id':'$bug_id','llm_response':diff,'method':'diff','generate_diff':True,'persist_flag':True}))
" 2>/dev/null)" \
                --max-time $TIMEOUT)

            patch_ok=$(echo "$patch_result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('success',False))" 2>/dev/null)
            if [[ "$patch_ok" == "True" ]]; then
                fix_p=$(echo "$patch_result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('fix_p',''))" 2>/dev/null)
                echo "  patch: built → $fix_p"

                # Submit fix
                fix_result=$(curl -s -X POST "$BASE_URL/fix" \
                    -H "Content-Type: application/json" \
                    -d "{\"bug_id\":\"$bug_id\",\"patch_path\":\"$fix_p\"}" \
                    --max-time $TIMEOUT)
                handle=$(echo "$fix_result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('handle',''))" 2>/dev/null)

                if [[ -n "$handle" ]]; then
                    # Poll status
                    for _ in $(seq 1 20); do
                        sleep 5
                        status=$(curl -s "$BASE_URL/status/$handle" --max-time 5 | \
                            python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','?'),d.get('return_code','?'))" 2>/dev/null)
                        read st rc <<< "$status"
                        if [[ "$st" == "completed" ]] || [[ "$st" == "failed" ]]; then break; fi
                    done
                    if [[ "$rc" == "0" ]]; then
                        echo "  patch/fix: ✅ PASS"
                        PASS_PATCH=$((PASS_PATCH+1))
                    else
                        echo "  patch/fix: ❌ FAIL (rc=$rc)"
                        FAIL_PATCH=$((FAIL_PATCH+1))
                    fi
                else
                    echo "  patch/fix: SKIP (no handle)"
                    SKIP_PATCH=$((SKIP_PATCH+1))
                fi
            else
                echo "  patch: SKIP (build failed)"
                SKIP_PATCH=$((SKIP_PATCH+1))
            fi
        fi
    fi
done

echo ""
echo "========================================"
echo "  Results ($BUGLIST_MODE mode, $COUNT bugs tested)"
echo "========================================"
if [[ "$MODE" == "all" ]] || [[ "$MODE" == "direct" ]]; then
    echo "  Direct: $PASS_DIRECT pass, $FAIL_DIRECT fail, $SKIP_DIRECT skip"
fi
if [[ "$MODE" == "all" ]] || [[ "$MODE" == "patch" ]]; then
    echo "  Patch:  $PASS_PATCH pass, $FAIL_PATCH fail, $SKIP_PATCH skip"
fi
echo "========================================"
