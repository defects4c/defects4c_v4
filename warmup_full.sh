#!/usr/bin/env bash
# warmup_full.sh — warm the bugs in bugs_to_warm.csv using parallel xargs.
# Must run INSIDE the container as root.
#
# Per bug: git_setup.sh (clone+fetch) then bug_helper reproduce (build+test).
# Status appended to /out/warmup_status.csv.

set -uo pipefail
WORKERS="${WORKERS:-4}"
INPUT="${INPUT:-/out/bugs_to_warm.csv}"
STATUS_CSV=/out/warmup_status.csv
LOG_DIR=/out/warmup_logs
mkdir -p "$LOG_DIR"

# Initialize CSV if absent
if [ ! -f "$STATUS_CSV" ]; then
    echo "project,sha_after,sha_before,version,phase,status,latency_s,log" > "$STATUS_CSV"
fi

warm_one() {
    local proj="$1" ca="$2" cb="$3" ver="$4"
    local log="$LOG_DIR/${proj}@${ca}.log"
    local t0=$(date +%s)
    local phase="unknown"
    local status="unknown"

    {
        echo "=== warm_one $proj@$ca ver=$ver cb=$cb ==="
        # Skip if already warmed (idempotent)
        if [ -d "/out/$proj/_gittree_$ca/.git" ] && [ -d "/out/$proj/git_repo_dir_$ca/build_$ca" ]; then
            echo "SKIP: already warmed"
            phase="skip"; status="already_warmed"
            return 0
        fi

        # Phase 1: git_setup.sh — clone + fetch
        phase="clone"
        echo "--- clone ---"
        if ! timeout 1200 bash /out/git_setup.sh "$proj" "$ca" "$cb"; then
            echo "CLONE FAILED"; status="clone_fail"; return 1
        fi

        # Phase 2: bug_helper reproduce (build + test)
        phase="reproduce"
        echo "--- reproduce ---"
        if ! timeout 1800 python3 /src/bug_helper_v1_out2.py reproduce "${proj}@${ca}"; then
            echo "REPRODUCE FAILED"; status="reproduce_fail"; return 1
        fi

        # Phase 3: verify artefacts
        phase="verify"
        if [ -d "/out/$proj/_gittree_$ca/.git" ] && [ -d "/out/$proj/git_repo_dir_$ca/build_$ca" ]; then
            status="warmed"
        else
            echo "VERIFY FAILED: missing _gittree or build_dir"; status="verify_fail"
            return 1
        fi
    } > "$log" 2>&1
    local rc=$?
    local t1=$(date +%s)
    local lat=$((t1 - t0))
    flock "$STATUS_CSV" -c "echo '$proj,$ca,$cb,$ver,$phase,$status,$lat,$log' >> '$STATUS_CSV'"
    return $rc
}

export -f warm_one
export STATUS_CSV LOG_DIR

echo "warmup_full.sh starting — INPUT=$INPUT WORKERS=$WORKERS"
echo "lines to process: $(wc -l < "$INPUT")"
echo "started at: $(date)"

# xargs parallel: each line is 'proj,ca,cb,ver'
< "$INPUT" tr ',' ' ' | xargs -P "$WORKERS" -L 1 bash -c 'warm_one "$@"' _

echo "all done at: $(date)"
echo
echo "=== final status counts ==="
awk -F, 'NR>1 {c[$6]++} END {for (k in c) print "  "k": "c[k]}' "$STATUS_CSV"
