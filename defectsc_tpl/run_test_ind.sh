#!/usr/bin/env bash
# run_test_ind.sh <sha> [patch_file]
#
# Pure rebuild + test. If patch_file is given, copies it into src_file first.
# If no patch_file, assumes src_file is already edited (by checkout or manual edit).
#
# Usage:
#   bash run_test_ind.sh <sha>                    # just rebuild+test
#   bash run_test_ind.sh <sha> /path/to/patch     # copy patch then rebuild+test

set -uo pipefail

sha=${1:-}
patch_file=${2:-}

if [[ -z "$sha" ]]; then
    echo "Usage: run_test_ind.sh <sha> [patch_file]"
    exit 1
fi

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

# Auto-detect project
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

if [[ -n "$patch_file" ]]; then
    echo "patch_file==$patch_file"
    # Use fix command which copies patch → src_file then rebuild+test
    cd "$SRC_DIR"
    python3 "$SRC_DIR/bug_helper_v1_out2.py" fix "${project}@${sha}" "$patch_file"
else
    # Pure rebuild+test (src_file already edited)
    cd "$SRC_DIR"
    python3 "$SRC_DIR/bug_helper_v1_out2.py" test "${project}@${sha}"
fi
