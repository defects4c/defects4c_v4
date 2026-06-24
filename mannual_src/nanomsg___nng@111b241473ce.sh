#!/usr/bin/env bash
# ============================================================================
# Manual oracle-debug harness for ONE Defects4C bug.
#   Bug      : nanomsg___nng@111b241473ceeecee1f1c232d3c9879fb850361d
#   Category : BO
#   BUGGY_ONLY — fix commit WRONGLY FAILS (build/config/test broken at fix)
#   Expected : buggy should FAIL (it does), fix should PASS (currently FAILS = the problem)
# ----------------------------------------------------------------------------
#   commit_after (FIX)   : 111b241473ceeecee1f1c232d3c9879fb850361d
#   commit_before (BUGGY): e792d31b4a3b04658108e59edeab78495ac6b5a8
#   patched src file     : src/core/message.c
#   CVE                  : Memory Error: Uncontrolled Resource Consumption
# ============================================================================
# Usage:
#   bash nanomsg___nng@111b241473ce.sh            # run oracle check (fix+buggy) then drop into container
#   bash nanomsg___nng@111b241473ce.sh check      # only run the oracle check, no shell
#   bash nanomsg___nng@111b241473ce.sh shell      # skip check, go straight into the container to debug
# ============================================================================
set -uo pipefail

BUG="nanomsg___nng@111b241473ceeecee1f1c232d3c9879fb850361d"
PROJ="nanomsg___nng"
SHA="111b241473ceeecee1f1c232d3c9879fb850361d"
CB="e792d31b4a3b04658108e59edeab78495ac6b5a8"
SRC_FILE="src/core/message.c"
WEBAPP="http://127.0.0.1:8095"
COMPOSE_DIR="/home/wangjian/wj_code/defects4c_dirs/defects4c_docker_web4"
BUGDIR="/out/$PROJ/git_repo_dir_$SHA"
GITTREE="/out/$PROJ/_gittree_$SHA/.git"
cd "$COMPOSE_DIR"

MODE="${1:-all}"

run_check() {
  echo "================================================================"
  echo " ORACLE CHECK  $BUG"
  echo " category: BO  (fix wrongly FAILS)"
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
