#!/usr/bin/env bash
# run_reproduce_ind.sh <sha>
#
# Individual reproduce: auto-detects project from SHA, then runs
# bug_helper_v1_out2.py reproduce for that single bug.
#
# Usage:
#   bash run_reproduce_ind.sh <sha>
#
# Example:
#   bash run_reproduce_ind.sh caa6ff7c2a6ef64df53e04701944aaa4712a1915

set -uo pipefail

sha=$1

if [[ -z "$sha" ]]; then
    echo "Usage: run_reproduce_ind.sh <sha>"
    exit 1
fi

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

# Auto-detect project from SHA by searching bugs_list*.json in both dirs
project=$(find "$SRC_DIR/projects_v1/" "$SRC_DIR/projects/" -name "bugs_list*.json" 2>/dev/null | \
          xargs grep "commit_after\": \"${sha}\"" 2>/dev/null | head -n 1 | \
          awk -F "/" '{
              # Find the directory name containing ___ (the project name)
              for (i=1; i<=NF; i++) {
                  if ($i ~ /___/) { print $i; exit }
              }
          }')

if [[ -z "$project" ]]; then
    echo "Project not found for SHA: $sha"
    exit 1
fi

echo "project==$project"
echo "sha==$sha"

test_log="/out/$project/logs/${sha}.log"

if [[ -f "$test_log" ]]; then
    echo "exist.....-->$test_log"
    exit 0
fi

cd "$SRC_DIR"
python3 "$SRC_DIR/bug_helper_v1_out2.py" reproduce "${project}@${sha}"
