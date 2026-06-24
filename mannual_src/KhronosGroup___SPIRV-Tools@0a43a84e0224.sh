#!/usr/bin/env bash
# ============================================================================
# Manual oracle-debug harness for ONE Defects4C bug.
#   Bug      : KhronosGroup___SPIRV-Tools@0a43a84e02245cca40ce187d1e427a5d0b4f3d13
#   Category : FO
#   FIX_ONLY  — buggy commit WRONGLY PASSES (test too lax / no ASAN / missing detection)
#   Expected : fix should PASS (it does), buggy should FAIL (currently PASSES = the problem)
# ----------------------------------------------------------------------------
#   commit_after (FIX)   : 0a43a84e02245cca40ce187d1e427a5d0b4f3d13
#   commit_before (BUGGY): 0ebcdc4d19de627023d538826672c816e3e90f32
#   patched src file     : source/opt/folding_rules.cpp
#   CVE                  : Sanitizer: Control Expression Error
# ============================================================================
# Usage:
#   bash KhronosGroup___SPIRV-Tools@0a43a84e0224.sh            # run oracle check (fix+buggy) then drop into container
#   bash KhronosGroup___SPIRV-Tools@0a43a84e0224.sh check      # only run the oracle check, no shell
#   bash KhronosGroup___SPIRV-Tools@0a43a84e0224.sh shell      # skip check, go straight into the container to debug
# ============================================================================
set -uo pipefail

BUG="KhronosGroup___SPIRV-Tools@0a43a84e02245cca40ce187d1e427a5d0b4f3d13"
PROJ="KhronosGroup___SPIRV-Tools"
SHA="0a43a84e02245cca40ce187d1e427a5d0b4f3d13"
CB="0ebcdc4d19de627023d538826672c816e3e90f32"
SRC_FILE="source/opt/folding_rules.cpp"
WEBAPP="http://127.0.0.1:8095"
COMPOSE_DIR="/home/wangjian/wj_code/defects4c_dirs/defects4c_docker_web4"
BUGDIR="/out/$PROJ/git_repo_dir_$SHA"
GITTREE="/out/$PROJ/_gittree_$SHA/.git"
cd "$COMPOSE_DIR"

MODE="${1:-all}"

run_check() {
  echo "================================================================"
  echo " ORACLE CHECK  $BUG"
  echo " category: FO  (buggy wrongly PASSES)"
  echo "================================================================"
  for m in fix buggy; do
    echo
    echo "----- mode=$m  (expect: $([ $m = fix ] && echo PASS || echo FAIL)) -----"
    curl -s -m 900 -X POST -H 'Content-Type: application/json' \
         -d "{\"bug_id\":\"$BUG\",\"mode\":\"$m\"}" \
         "$WEBAPP/validate_oracle" | python3 -m json.tool 2>/dev/null \
       || echo "  (request failed / webapp busy)"
  done
  echo
}

enter_container() {
  echo "================================================================"
  echo " MANUAL DEBUG — entering container at $BUGDIR"
  echo "----------------------------------------------------------------"
  echo " Useful commands inside:"
  echo "   # inspect git history (git dir is relocated to a sibling):"
  echo "   git --git-dir=$GITTREE --work-tree=$BUGDIR log --oneline -5"
  echo "   # show the fix vs buggy version of the patched file:"
  echo "   git --git-dir=$GITTREE show $SHA:$SRC_FILE      # fixed"
  echo "   git --git-dir=$GITTREE show $CB:$SRC_FILE       # buggy"
  echo "   # rebuild + test in place (reuses the rendered scripts):"
  echo "   bash inplace_build.sh  build_$SHA  /tmp/t.log"
  echo "   bash inplace_test.sh   build_$SHA  /tmp/t.log ; cat /tmp/t.status 2>/dev/null"
  echo "   # apply buggy source then rebuild to reproduce the bug:"
  echo "   git --git-dir=$GITTREE show $CB:$SRC_FILE > $SRC_FILE && bash inplace_rebuild.sh build_$SHA /tmp/t.log"
  echo "================================================================"
  docker compose exec -it defects4c bash -c "cd '$BUGDIR' 2>/dev/null || cd /out/$PROJ; exec bash"
}

case "$MODE" in
  check) run_check ;;
  shell) enter_container ;;
  all)   run_check; enter_container ;;
  *)     echo "usage: bash $0 [check|shell|all]"; exit 1 ;;
esac
