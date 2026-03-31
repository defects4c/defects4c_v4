#!/usr/bin/env bash
# run_patch_ind.sh <sha> <patch_file_path>
#
# Individual patch: auto-detects project from SHA, then runs
# bug_helper_v1_out2.py fix for that single patch file.
#
# Usage:
#   bash run_patch_ind.sh <sha> <patch_file_path>
#
# Example:
#   bash run_patch_ind.sh caa6ff7c2a6ef64df53e04701944aaa4712a1915 /patches/danmar___cppcheck/abc123@caa6ff7...___file.cpp

set -uo pipefail

sha=${1:-}
patch_file=${2:-}

if [[ -z "$sha" ]] || [[ -z "$patch_file" ]]; then
    echo "Usage: run_patch_ind.sh <sha> <patch_file_path>"
    exit 1
fi

if [[ ! -f "$patch_file" ]]; then
    echo "Patch file not found: $patch_file"
    exit 1
fi

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

# Auto-detect project from SHA by searching bugs_list*.json in both dirs
project=$(find "$SRC_DIR/projects_v1/" "$SRC_DIR/projects/" -name "bugs_list*.json" 2>/dev/null | \
          xargs grep "commit_after\": \"${sha}\"" 2>/dev/null | head -n 1 | \
          awk -F "/" '{
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
echo "patch_file==$patch_file"

# Extract md5 from patch filename (format: <md5>@<sha>___<filename>)
tmp=$(basename "$patch_file")
OLD_IFS=$IFS
IFS="@"
read -ra parts <<< "$tmp"
IFS=$OLD_IFS
md5="${parts[0]}"

test_log="/out/${project}/logs/patch_${sha}_${md5}.log"

if [[ -f "$test_log" ]]; then
    echo "exist.....-->$test_log"
    exit 0
fi

cd "$SRC_DIR"
python3 "$SRC_DIR/bug_helper_v1_out2.py" fix "${project}@${sha}" "$patch_file"
