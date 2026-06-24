#!/usr/bin/env bash
set -uo pipefail

[ "$EUID" -ne 0 ] && exit 1

USERNAME="defects4c_user"
id "$USERNAME" &>/dev/null || useradd -m -G root -s /bin/bash "$USERNAME"

if command -v sudo >/dev/null 2>&1; then
    mkdir -p /etc/sudoers.d
    echo "$USERNAME ALL=(ALL) NOPASSWD: ALL" > "/etc/sudoers.d/$USERNAME"
    chmod 0440 "/etc/sudoers.d/$USERNAME"
fi

cpu_count="${1:-$(nproc)}"

(
    set -e

    find /src/projects* -name 'bugs_list_new.json' -print0 \
    | sort -z \
    | while IFS= read -r -d '' f; do
        project="$(basename "$(dirname "$f")")"
        jq -r --arg p "$project" '.[] | "\($p) \(.commit_after)"' "$f"
    done \
    | sort -u \
    | xargs -n 2 -P "$cpu_count" bash -c '
        project="$1"
        sha="$2"
        bash /src/run_reproduce.sh "$project" "$sha"
    ' _
)
rc=$?

# ── Create pool slots ──────────────────────────────────────
echo "Creating pool slots..."
find /src/projects* -name 'bugs_list_new.json' -print0 \
| sort -z \
| while IFS= read -r -d '' f; do
    project="$(basename "$(dirname "$f")")"
    jq -r --arg p "$project" '.[] | "\($p) \(.commit_after)"' "$f"
done \
| sort -u \
| xargs -n 2 -P "$cpu_count" bash -c '
    project="$1"
    sha="$2"
    golden="/out/$project/git_repo_dir_${sha}"
    if [[ ! -d "$golden" ]]; then
        echo "[pool] SKIP $project@$sha — golden not found"
        exit 0
    fi
    for i in 0 1 2; do
        slot="${golden}/__s${i}"
        if [[ -d "$slot" ]]; then
            echo "[pool] SKIP $project@$sha slot $i — already exists"
            continue
        fi
        echo "[pool] Creating $project@$sha slot $i"
        rsync -a --exclude=.git --exclude="__s*" --exclude="*.lock" "$golden/" "$slot/"
        if [[ ! -d "$slot/.git" ]]; then
            git -C "$slot" init -q
            git -C "$slot" add -A 2>/dev/null
            git -C "$slot" -c user.email=d4c@local -c user.name=d4c commit -q -m baseline_buggy 2>/dev/null || true
        fi
    done
' _

chmod -R a+rwX /out/

chown -R "$USERNAME:$USERNAME" /src/ || true
chown -R "$USERNAME:$USERNAME" /out/ || true
chown -R "$USERNAME:$USERNAME" /patches/ || true
chown -R "$USERNAME:$USERNAME" /workspace/ || true

exit "$rc"
