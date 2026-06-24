#!/usr/bin/env bash
# run_all_checks.sh — run ONLY the oracle check (no container shell) for every bug,
# print a one-line PASS/FAIL summary. For debugging a single bug use its own script.
set -uo pipefail
cd "$(dirname "$0")"
for s in *@*.sh; do
  echo "### $s"
  bash "$s" check 2>/dev/null | python3 -c "
import sys,json,re
buf=sys.stdin.read()
for m in re.findall(r'\{[^{}]*verdict[^{}]*\}', buf, re.S):
    try:
        d=json.loads(m); print('   %-6s actual=%-5s %s'%(d['mode'],d.get('actual'),d.get('verdict','')[:50]))
    except: pass
" || echo "   (parse failed)"
done
