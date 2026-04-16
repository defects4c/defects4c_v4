#!/bin/bash

set -u
set -o pipefail

error() {
    echo "[ERROR] $*" >&2
}

# Validate that a git-dir is actually a working git repository
# Usage: validate_git_dir <git-dir-path> <work-tree-path>
validate_git_dir() {
    local gitdir="$1"
    local worktree="$2"

    if [[ ! -d "$gitdir" ]]; then
        return 1
    fi

    # Must have essential git internals: HEAD, objects, refs
    if [[ ! -f "$gitdir/HEAD" || ! -d "$gitdir/objects" || ! -d "$gitdir/refs" ]]; then
        echo "WARNING: $gitdir is missing essential git internals (HEAD/objects/refs)"
        return 1
    fi

    # Actually test with git rev-parse
    if ! git --git-dir="$gitdir" --work-tree="$worktree" rev-parse --git-dir >/dev/null 2>&1; then
        echo "WARNING: git rev-parse failed on $gitdir"
        return 1
    fi

    return 0
}

trap 'error "git_setup.sh failed for project=${project_name:-unknown} at line $LINENO: $BASH_COMMAND"' ERR

# Simplified git repository setup with selective commit fetching
# Usage: ./git_setup.sh <project_name> <commit1> <commit2> [commit3] ...

if [ $# -lt 3 ]; then
    echo "Usage: $0 <project_name> <commit1> <commit2> [commit3] ..."
    echo "Example: $0 php___php-src 1bd103df00f49cf4d4ade2cfe3f456ac058a4eae a3598dd7c9b182debcb54b9322b1dece14c9b533"
    exit 1
fi

project_name="$1"
shift
commits=("$@")

for sha in "${commits[@]}"; do
    if [[ -z "$sha" || "$sha" == "null" ]]; then
        echo "ERROR: Invalid commit sha: '$sha'"
        echo "Project: $project_name"
        exit 1
    fi
done

# Convert project name to GitHub URL format
raw_repo="${project_name/___/\/}"
github_url="https://github.com/${raw_repo}"

# Resolve output dir relative to where git_setup.sh is located
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
base_out_dir="${script_dir}"

# Use first commit as the main directory identifier
main_commit="${commits[0]}"
target_dir="${base_out_dir}/${project_name}/git_repo_dir_${main_commit}"
target_dir_git="${base_out_dir}/${project_name}/git_repo_dir_${main_commit}_gittree"

echo "Setting up repository for project: $project_name"
echo "GitHub URL: $github_url"
echo "Base output directory: $base_out_dir"
echo "Target directory (work-tree): $target_dir"
echo "Target directory (git-dir):   $target_dir_git"
echo "Commits to fetch: ${commits[*]}"

# ─── Three-case logic ───────────────────────────────────────────────

# Case 3: Both exist, .git is already in target_dir_git → validate and skip
if [[ -d "$target_dir" && -d "$target_dir_git" && ! -d "$target_dir/.git" && -d "$target_dir_git/.git" ]]; then
    if validate_git_dir "$target_dir_git/.git" "$target_dir"; then
        echo "✓ Already set up and validated: work-tree at $target_dir, git-dir at $target_dir_git. Skipping."
        exit 0
    else
        echo "WARNING: $target_dir_git/.git exists but is broken. Nuking both and redoing full pipeline."
        rm -rf "$target_dir" "$target_dir_git"
    fi
fi

# Case 2: target_dir exists but target_dir_git does not → .git is still inside target_dir, move it
if [[ -d "$target_dir" && ! -d "$target_dir_git" && -d "$target_dir/.git" ]]; then
    echo "Work-tree exists with .git inside. Moving .git to $target_dir_git ..."
    mkdir -p "$target_dir_git"
    mv "$target_dir/.git" "$target_dir_git/.git"
    if validate_git_dir "$target_dir_git/.git" "$target_dir"; then
        echo "✓ Moved and validated .git at $target_dir_git/.git"
        exit 0
    else
        echo "WARNING: Moved .git but it is broken. Nuking both and redoing full pipeline."
        rm -rf "$target_dir" "$target_dir_git"
    fi
fi

# Case 1: target_dir does not exist → full pipeline
# Even if target_dir_git exists, delete it (stale/incomplete state)
if [[ -d "$target_dir_git" ]]; then
    echo "target_dir does not exist but target_dir_git does. Removing stale git-dir: $target_dir_git"
    rm -rf "$target_dir_git"
fi

# Also clean up target_dir if it somehow exists but is broken (no .git, no separate git-dir)
if [[ -d "$target_dir" && ! -d "$target_dir/.git" ]]; then
    echo "target_dir exists but has no .git and no separate git-dir. Removing broken work-tree: $target_dir"
    rm -rf "$target_dir"
fi

# ─── Full pipeline: init, fetch, checkout, then separate .git ───────

echo "Creating directory: $target_dir"
mkdir -p "$target_dir"
cd "$target_dir" || {
    echo "ERROR: Cannot change to directory: $target_dir"
    exit 1
}

echo "Initializing git repository..."
if [[ ! -d .git ]]; then
    git init
fi

git clean -dfx

echo "Adding remote origin: $github_url"
if git remote get-url origin >/dev/null 2>&1; then
    git remote set-url origin "$github_url"
else
    git remote add origin "$github_url"
fi

echo "Fetching commits..."
for sha in "${commits[@]}"; do
    echo "Fetching commit: $sha"
    if ! timeout 1200 git fetch --depth 1 origin "$sha"; then
        echo "ERROR: Failed to fetch commit $sha"
        exit 1
    fi
    echo "✓ Successfully fetched: $sha"
done

echo ""
echo "Testing checkout to first commit..."
if timeout 1200 git checkout "${commits[0]}"; then
    echo "✓ Successfully checked out: ${commits[0]}"
    echo ""
    echo "Current branch/commit:"
    git log --oneline -1
else
    echo "ERROR: Failed to checkout ${commits[0]}"
    exit 1
fi

echo "Updating submodules..."
if ! timeout 1200 git submodule update --init --recursive --jobs 1; then
    echo "ERROR: Submodule update failed"
    echo "Project: $project_name"
    echo "Target directory: $target_dir"
    exit 1
fi
echo "Submodule update finished successfully"

# ─── Move .git into the separate git-dir ─────────────────────────────

echo "Separating .git into: $target_dir_git"
mkdir -p "$target_dir_git"
mv "$target_dir/.git" "$target_dir_git/.git"

# ─── Final assertion ─────────────────────────────────────────────────

if [[ -d "$target_dir/.git" ]]; then
    echo "ERROR: $target_dir/.git still exists after move. This should not happen."
    exit 1
fi

if [[ ! -d "$target_dir_git/.git" ]]; then
    echo "ERROR: $target_dir_git/.git does not exist after move. This should not happen."
    exit 1
fi

if ! validate_git_dir "$target_dir_git/.git" "$target_dir"; then
    echo "ERROR: $target_dir_git/.git exists but is not a valid git repository after full pipeline."
    exit 1
fi

echo "✓ Done. Work-tree: $target_dir | Git-dir: $target_dir_git"

