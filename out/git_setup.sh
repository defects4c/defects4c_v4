#!/bin/bash

set -u
set -o pipefail

error() {
    echo "[ERROR] $*" >&2
}

validate_git_dir() {
    local gitdir="$1"
    local worktree="$2"
    if [[ ! -d "$gitdir" ]]; then return 1; fi
    if [[ ! -f "$gitdir/HEAD" || ! -d "$gitdir/objects" || ! -d "$gitdir/refs" ]]; then
        echo "WARNING: $gitdir is missing essential git internals"
        return 1
    fi
    if ! git --git-dir="$gitdir" --work-tree="$worktree" rev-parse --git-dir >/dev/null 2>&1; then
        echo "WARNING: git rev-parse failed on $gitdir"
        return 1
    fi
    return 0
}

trap 'error "git_setup.sh failed for project=${project_name:-unknown} at line $LINENO: $BASH_COMMAND"' ERR

if [ $# -lt 3 ]; then
    echo "Usage: $0 <project_name> <commit1> <commit2> [commit3] ..."
    exit 1
fi

project_name="$1"
shift
commits=("$@")

for sha in "${commits[@]}"; do
    if [[ -z "$sha" || "$sha" == "null" ]]; then
        echo "ERROR: Invalid commit sha: '$sha'"
        exit 1
    fi
done

raw_repo="${project_name/___/\/}"
github_url="https://github.com/${raw_repo}"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
base_out_dir="${script_dir}"

main_commit="${commits[0]}"
target_dir="${base_out_dir}/${project_name}/git_repo_dir_${main_commit}"
gittree_dir="${base_out_dir}/${project_name}/_gittree_${main_commit}"

echo "Setting up repository for project: $project_name"
echo "GitHub URL: $github_url"
echo "Target directory: $target_dir"
echo "Gittree directory: $gittree_dir"
echo "Commits to fetch: ${commits[*]}"

# ─── Idempotency: if BOTH locations have .git, error out ───────────
if [[ -d "$target_dir/.git" && -d "$gittree_dir/.git" ]]; then
    echo "ERROR: BOTH $target_dir/.git AND $gittree_dir/.git exist. Refusing to proceed."
    exit 1
fi

# ─── Idempotency: already migrated → exit 0 ────────────────────────
if [[ -d "$gittree_dir/.git" && -d "$target_dir" && ! -d "$target_dir/.git" ]]; then
    if validate_git_dir "$gittree_dir/.git" "$target_dir"; then
        echo "✓ Already migrated and validated: history at $gittree_dir/.git, work-tree at $target_dir. Skipping."
        exit 0
    else
        echo "WARNING: $gittree_dir/.git exists but is broken. Nuking and redoing."
        rm -rf "$target_dir" "$gittree_dir"
    fi
fi

# ─── Case: target_dir has .git but sibling does NOT — do the move at end of pipeline ──
# (handled by relocate trailer below; here we just allow the validate-and-skip path
#  for the in-place state to fall through to the full pipeline if it's broken.)
if [[ -d "$target_dir/.git" && ! -d "$gittree_dir/.git" ]]; then
    if validate_git_dir "$target_dir/.git" "$target_dir"; then
        echo "Pre-existing $target_dir/.git is valid; will relocate to $gittree_dir/.git at end."
        # Skip the full clone pipeline; jump straight to relocation.
        mkdir -p "$gittree_dir"
        mv "$target_dir/.git" "$gittree_dir/.git"
        if ! validate_git_dir "$gittree_dir/.git" "$target_dir"; then
            echo "ERROR: post-move validation failed."
            exit 1
        fi
        echo "✓ Done. Repository at: $target_dir (history relocated to _gittree_${main_commit}/.git)"
        exit 0
    else
        echo "WARNING: $target_dir/.git exists but is broken. Nuking and redoing."
        rm -rf "$target_dir" "$gittree_dir"
    fi
fi

# ─── Case 2: target_dir exists but no .git (and no _gittree) ──────
if [[ -d "$target_dir" && ! -d "$target_dir/.git" && ! -d "$gittree_dir/.git" ]]; then
    echo "target_dir exists but has no .git (and no gittree). Removing broken work-tree: $target_dir"
    rm -rf "$target_dir"
fi

# Clean up any stale empty _gittree (no .git inside)
if [[ -d "$gittree_dir" && ! -d "$gittree_dir/.git" ]]; then
    rm -rf "$gittree_dir"
fi

# ─── Full pipeline: init, fetch, checkout ──────────────────────────
echo "Creating directory: $target_dir"
mkdir -p "$target_dir"
cd "$target_dir" || { echo "ERROR: Cannot cd to $target_dir"; exit 1; }

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

echo "Testing checkout to first commit..."
if timeout 1200 git checkout "${commits[0]}"; then
    echo "✓ Successfully checked out: ${commits[0]}"
    git log --oneline -1
else
    echo "ERROR: Failed to checkout ${commits[0]}"
    exit 1
fi

echo "Updating submodules..."
if ! timeout 1200 git submodule update --init --recursive --jobs 1; then
    echo "ERROR: Submodule update failed"
    exit 1
fi
echo "Submodule update finished successfully"

# ─── Relocate .git OUT of target_dir into sibling _gittree_<sha>/ ──
if [[ ! -d "$target_dir/.git" ]]; then
    echo "ERROR: $target_dir/.git does not exist after pipeline."
    exit 1
fi

if [[ -d "$gittree_dir/.git" ]]; then
    echo "ERROR: $gittree_dir/.git unexpectedly exists pre-relocation."
    exit 1
fi

mkdir -p "$gittree_dir"
mv "$target_dir/.git" "$gittree_dir/.git"

if ! validate_git_dir "$gittree_dir/.git" "$target_dir"; then
    echo "ERROR: .git is not valid after relocation."
    exit 1
fi

echo "✓ Done. Repository at: $target_dir (history relocated to _gittree_${main_commit}/.git)"
