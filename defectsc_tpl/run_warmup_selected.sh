#!/usr/bin/env bash
set -uo pipefail
# Uses existing per-sha repos + bug_helper_v1_out2.py reproduce. No full clone.
PROJECT="danmar___cppcheck"
BASE="/out/${PROJECT}"
BUGS=(
    "caa6ff7c2a6ef64df53e04701944aaa4712a1915 TestAnalyzerInformation"
    "d0b6079a832d5c156af1e51274e09f28ee8677a7 TestCondition"
    "398fa280213771a0fa7a649a54dd6e6625d48e20 TestStl"
    "c4dcfef38564e97442fb6c122f5e67c908e0c665 TestSymbolDatabase"
    "4779f0e1725c5807b329798e12dbeaddbf568b65 TestSimplifyTemplate"
    "192c30ab1d3ec97ae7c6955d8953fac133b8e4ff TestTokenizer"
)
IDX="${1:-}"
[[ "$IDX" == "clean" ]] && { rm -rf "${BASE}/logs"; echo "Cleaned."; IDX=""; }
echo "========================================"
echo "  Warmup Selected: $PROJECT"
echo "========================================"
mkdir -p "${BASE}/logs"
OK=0; FAIL=0
for idx in "${!BUGS[@]}"; do
    [[ -n "$IDX" ]] && [[ "$idx" != "$IDX" ]] && continue
    read -r sha trigger <<< "${BUGS[$idx]}"
    repo="${BASE}/git_repo_dir_${sha}"
    echo ""
    echo "========== [$idx] ${sha:0:12} ${trigger} =========="
    if [[ ! -d "${repo}/.git" ]]; then
        echo "  ✗ Repo not found. Run: bash defectsc_tpl/bulk_git_clone_v2.sh mini"
        FAIL=$((FAIL+1)); continue
    fi
    echo "  [repo] OK"
    fix_status="${BASE}/logs/test_${sha}_fix.status"
    buggy_status="${BASE}/logs/test_${sha}_buggy.status"
    if [[ -f "$fix_status" ]] && [[ -f "$buggy_status" ]]; then
        fs=$(cat "$fix_status"); bs=$(cat "$buggy_status")
        if [[ "$fs" == *"success"* ]] && [[ "$bs" == *"FAILED"* ]]; then
            echo "  ✅ Already verified (fix=$fs buggy=$bs)"; OK=$((OK+1)); continue
        fi
    fi
    echo "  [reproduce] Running..."
    cd /src && python3 bug_helper_v1_out2.py reproduce "${PROJECT}@${sha}" 2>&1 | tail -5
    if [[ -f "$fix_status" ]] && [[ -f "$buggy_status" ]]; then
        fs=$(cat "$fix_status"); bs=$(cat "$buggy_status")
        if [[ "$fs" == *"success"* ]] && [[ "$bs" == *"FAILED"* ]]; then
            echo "  ✅ VERIFIED"; OK=$((OK+1))
        else echo "  ❌ NOT VERIFIED (fix=$fs buggy=$bs)"; FAIL=$((FAIL+1)); fi
    else echo "  ❌ No status files"; FAIL=$((FAIL+1)); fi
done
echo ""
echo "========================================"
echo "  Summary: ${OK} verified, ${FAIL} failed"
echo "========================================"
