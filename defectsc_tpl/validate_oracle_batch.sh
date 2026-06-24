#!/usr/bin/env bash
set -uo pipefail
#
# validate_oracle_batch.sh — Batch oracle validation using defects4c CLI.
#
# For each warmed-up bug:
#   1. Get oracle diff: git --git-dir=..._gittree/.git diff commit_before commit_after -- src_file
#   2. Upload oracle patch via /build_patch
#   3. Submit via /fix, poll /status, check .status file
#
# Usage:
#   bash validate_oracle_batch.sh                              # validate all
#   bash validate_oracle_batch.sh --project danmar___cppcheck  # one project
#   bash validate_oracle_batch.sh --mode direct                # direct checkout only
#   bash validate_oracle_batch.sh --mode patch                 # diff-as-patch only

BASE_URL="${DEFECTS4C_URL:-http://127.0.0.1:8095}"
OUT_ROOT="${ROOT_DIR:-/out}"
MODE="all"
PROJECT_FILTER=""
TIMEOUT=60

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode) MODE="$2"; shift 2;;
        --project) PROJECT_FILTER="$2"; shift 2;;
        --url) BASE_URL="$2"; shift 2;;
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
BUGS=$(
    curl -s "$BASE_URL/list_defects_bugid" | python3 -c '
import sys, json

data = json.load(sys.stdin)
print(data.get("mode", "?"), file=sys.stderr)

for b in data.get("defects", []):
    print(b)
' 2>/tmp/d4c_mode.txt
  )


if [[ -z "$BUGS" ]]; then
    echo "ERROR: /list_defects_bugid returned no bugs (URL=$BASE_URL). Aborting." >&2
    exit 1
fi

# Convert newline-separated list into bash array
mapfile -t BUGS <<< "$BUGS"

printf '%s\n' "${BUGS[@]}"


BUGLIST_MODE=$(cat /tmp/d4c_mode.txt 2>/dev/null || echo "?")
TOTAL=$(echo "$BUGS" | wc -l)
echo "  Buglist mode: $BUGLIST_MODE"
echo "  Total bugs: $TOTAL"
echo "========================================"

PASS_DIRECT=0; FAIL_DIRECT=0; SKIP_DIRECT=0
PASS_PATCH=0; FAIL_PATCH=0; SKIP_PATCH=0
COUNT=0


TOTAL=${#BUGS[@]}
echo "  Buglist mode: $BUGLIST_MODE"
echo "  Total bugs: $TOTAL"
echo "========================================"

for bug_id in "${BUGS[@]}"; do

    #for bug_id in $BUGS; do
    project=$(echo "$bug_id" | cut -d@ -f1)
    sha=$(echo "$bug_id" | cut -d@ -f2)

    # Project filter
    if [[ -n "$PROJECT_FILTER" ]] && [[ "$project" != "$PROJECT_FILTER" ]]; then
        continue
    fi

    COUNT=$((COUNT+1))
    echo ""
    echo "--- [$COUNT] $bug_id ---"

    # Paths (full SHA)
    work_dir="$OUT_ROOT/$project/git_repo_dir_${sha}"
    git_tree="$OUT_ROOT/$project/_gittree_${sha}/.git"
    log_dir="$OUT_ROOT/$project/logs"
    build_dir="$work_dir/build_${sha}"

    # Check if warmed up
    if [[ ! -d "$build_dir" ]]; then
        echo "  SKIP (build dir not found: $build_dir)"
        SKIP_DIRECT=$((SKIP_DIRECT+1))
        SKIP_PATCH=$((SKIP_PATCH+1))
        continue
    fi

    # Determine git prefix
    if [[ -d "$git_tree" ]]; then
        GIT="git --git-dir=$git_tree --work-tree=$work_dir"
    elif [[ -d "$work_dir/.git" ]]; then
        GIT="git -C $work_dir"
    else
        echo "  SKIP (no .git found)"
        SKIP_DIRECT=$((SKIP_DIRECT+1))
        SKIP_PATCH=$((SKIP_PATCH+1))
        continue
    fi

    # Get metadata for commit_before and src_file
    meta=$(curl -s "$BASE_URL/get_defect/$bug_id" --max-time 10)
    src_file=$(echo "$meta" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('fl_info',{}).get('src_file',''))" 2>/dev/null)
    commit_before=$(echo "$meta" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('metadata',{}).get('commit_before',''))" 2>/dev/null)

    if [[ -z "$src_file" ]]; then
        echo "  SKIP (no src_file in metadata)"
        SKIP_DIRECT=$((SKIP_DIRECT+1))
        SKIP_PATCH=$((SKIP_PATCH+1))
        continue
    fi

    # ── Mode 1: Direct checkout fix → test ──
    if [[ "$MODE" == "all" ]] || [[ "$MODE" == "direct" ]]; then
        # Checkout fix version (commit_after = sha)
        $GIT checkout -f "$sha" -- "$src_file" 2>/dev/null
        if [[ $? -ne 0 ]]; then
            echo "  direct/fix: SKIP (checkout failed)"
            SKIP_DIRECT=$((SKIP_DIRECT+1))
        else
            # Run test via validate_oracle endpoint (which calls bug_helper.cmd_test)
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
                    actual=$(echo "$result" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('actual','?'))" 2>/dev/null)
                    echo "  direct/fix: ❌ FAIL (actual=$actual)"
                    FAIL_DIRECT=$((FAIL_DIRECT+1))
                fi
            else
                error=$(echo "$result" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error','')[:120])" 2>/dev/null)
                echo "  direct/fix: SKIP ($error)"
                SKIP_DIRECT=$((SKIP_DIRECT+1))
            fi
        fi
    fi

    # ── Mode 2: Diff-as-patch ──
    if [[ "$MODE" == "all" ]] || [[ "$MODE" == "patch" ]]; then
        # Get oracle diff using git --git-dir
        diff_text=$($GIT diff "$commit_before" "$sha" -- "$src_file" 2>/dev/null)

        if [[ -z "$diff_text" ]]; then
            echo "  patch: SKIP (no diff available)"
            SKIP_PATCH=$((SKIP_PATCH+1))
        else
            # First checkout buggy version
            $GIT checkout -f "$commit_before" -- "$src_file" 2>/dev/null

            # Submit diff as patch via build_patch
            patch_result=$(python3 -c "
import sys, json, requests
diff = open('/dev/stdin').read()
r = requests.post('$BASE_URL/build_patch', json={
    'bug_id': '$bug_id',
    'llm_response': diff,
    'method': 'diff',
    'generate_diff': True,
    'persist_flag': True,
}, timeout=$TIMEOUT)
print(json.dumps(r.json()))
" <<< "$diff_text" 2>/dev/null)

            patch_ok=$(echo "$patch_result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('success',False))" 2>/dev/null)
            if [[ "$patch_ok" == "True" ]]; then
                fix_p=$(echo "$patch_result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('fix_p',''))" 2>/dev/null)
                echo "  patch: built → $(basename $fix_p)"

                # Submit fix
                fix_result=$(curl -s -X POST "$BASE_URL/fix" \
                    -H "Content-Type: application/json" \
                    -d "{\"bug_id\":\"$bug_id\",\"patch_path\":\"$fix_p\"}" \
                    --max-time 10)
                handle=$(echo "$fix_result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('handle',''))" 2>/dev/null)

                if [[ -n "$handle" ]]; then
                    # Poll status
                    for _ in $(seq 1 30); do
                        sleep 3
                        status_resp=$(curl -s "$BASE_URL/status/$handle" --max-time 5)
                        st=$(echo "$status_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null)
                        if [[ "$st" == "completed" ]] || [[ "$st" == "failed" ]]; then break; fi
                    done

                    # Check .status file directly
                    md5_hash=$(echo "$patch_result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('md5_hash',''))" 2>/dev/null)
                    status_file="$log_dir/patch_${sha}_fix.status"
                    if [[ -f "$status_file" ]]; then
                        status_content=$(cat "$status_file")
                        if [[ "$status_content" == *"success"* ]]; then
                            echo "  patch/fix: ✅ PASS (status=$status_content)"
                            PASS_PATCH=$((PASS_PATCH+1))
                        else
                            echo "  patch/fix: ❌ FAIL (status=$status_content)"
                            FAIL_PATCH=$((FAIL_PATCH+1))
                        fi
                    else
                        # Fallback: check via handle
                        rc=$(echo "$status_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('return_code','?'))" 2>/dev/null)
                        if [[ "$rc" == "0" ]]; then
                            echo "  patch/fix: ✅ PASS (rc=$rc)"
                            PASS_PATCH=$((PASS_PATCH+1))
                        else
                            echo "  patch/fix: ❌ FAIL (rc=$rc)"
                            FAIL_PATCH=$((FAIL_PATCH+1))
                        fi
                    fi
                else
                    echo "  patch/fix: SKIP (no handle)"
                    SKIP_PATCH=$((SKIP_PATCH+1))
                fi
            else
                error=$(echo "$patch_result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('error','')[:80])" 2>/dev/null)
                echo "  patch: SKIP (build failed: $error)"
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
