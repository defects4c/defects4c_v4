#!/bin/bash
#
# bulk_git_clone_v2.sh
#
# Build and run git setup commands for projects discovered from local json files.
#
# Usage:
#   ./bulk_git_clone_v2.sh
#       Run in mini mode for all discovered projects.
#
#   ./bulk_git_clone_v2.sh full
#       Run in full mode for all discovered projects.
#
#   ./bulk_git_clone_v2.sh mini
#       Run in mini mode for all discovered projects.
#       Mini mode excludes:
#         - llvm___llvm*
#         - projects not in bugs_14.txt whitelist
#
#   ./bulk_git_clone_v2.sh full <project>
#       Run in full mode for one specific project.
#
#   ./bulk_git_clone_v2.sh mini <project>
#       Run in mini mode for one specific project.
#       Note: if <project> matches llvm___llvm*, it will be excluded in mini mode.
#
#   ./bulk_git_clone_v2.sh <project>
#       Backward-compatible form.
#       Treated as: ./bulk_git_clone_v2.sh mini <project>
#
# Examples:
#   ./bulk_git_clone_v2.sh
#   ./bulk_git_clone_v2.sh mini
#   ./bulk_git_clone_v2.sh full
#   ./bulk_git_clone_v2.sh mini pytorch___pytorch
#   ./bulk_git_clone_v2.sh tensorflow___tensorflow
#
# Debug:
#   DEBUG=1 ./bulk_git_clone_v2.sh mini
#   DEBUG=0 ./bulk_git_clone_v2.sh full
#
# Notes:
#   - full mode includes all discovered projects
#   - mini mode excludes llvm___llvm* and filters to bugs_14.txt whitelist
#   - commands are written to /tmp/checklist.txt before execution
#   - failed commands are written to /tmp/checklist_failed.txt
#   - per-command logs are written to /tmp/bulk_git_clone_logs/

set -u
set -o pipefail

DEBUG="${DEBUG:-1}"

debug() {
    if [[ "$DEBUG" == "1" ]]; then
        echo "[DEBUG] $*" >&2
    fi
}

info() {
    echo "[INFO] $*" >&2
}

error() {
    echo "[ERROR] $*" >&2
}

trap 'error "Command failed at line $LINENO: $BASH_COMMAND"' ERR

mode="${1:-mini}"
project="${2:-}"

if [[ "$mode" != "mini" && "$mode" != "full" ]]; then
    project="$mode"
    mode="mini"
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
git_setup_script="${script_dir}/../out/git_setup.sh"

debug "script_dir: ${script_dir}"
debug "git_setup_script: ${git_setup_script}"

if [[ ! -f "${git_setup_script}" ]]; then
    error "git_setup.sh not found: ${git_setup_script}"
    exit 1
fi

debug "Mode: '${mode}'"
debug "Project: '${project}'"

if [[ -n "$project" ]]; then
    project_list=("$project")
    debug "Using single project from argument"
else
    debug "No project argument provided; scanning project*json files"
    mapfile -t project_list < <(
    find . -path './out' -prune -o -name 'project*json' -type f -print0 2>/dev/null |
    xargs -0 jq -r 'select(.repo_name != null) | .repo_name' 2>/dev/null |
    awk 'NF && $0 != "null"'
   )
fi


if [[ "$mode" == "mini" ]]; then
    debug "Applying mini mode filter: exclude llvm___llvm*"
    filtered_list=()
    for p in "${project_list[@]}"; do
        if [[ "$p" == *"llvm___llvm"* ]]; then
            debug "Excluded project: $p"
            continue
        fi
        filtered_list+=("$p")
    done
    project_list=("${filtered_list[@]}")

    # ── Mini-mode project whitelist (bugs_14.txt projects) ──────────
    declare -A MINI_PROJECTS=(
        [libgd___libgd]=1
        [danmar___cppcheck]=1
        [CLIUtils___CLI11]=1
        [the-tcpdump-group___tcpdump]=1
        [zeromq___libzmq]=1
        [php___php-src]=1
        [CESNET___libyang]=1
        [fmtlib___fmt]=1
        [mdadams___jasper]=1
        [nginx___njs]=1
        [KhronosGroup___SPIRV-Tools]=1
        [sqlite___sqlite]=1
    )
    debug "Applying mini mode project whitelist (${#MINI_PROJECTS[@]} projects)"
    filtered_list=()
    for p in "${project_list[@]}"; do
        if [[ -z "${MINI_PROJECTS[$p]:-}" ]]; then
            debug "Excluded by whitelist: $p"
            continue
        fi
        filtered_list+=("$p")
    done
    project_list=("${filtered_list[@]}")

    # ── Mini-mode commit whitelist (bugs_14.txt commit_after SHAs) ──
    declare -A MINI_COMMITS=(
        [2bb97f407c1145c850416a3bfbcc8cf124e68a19]=1
        [4996ec190ecf27a4bf018eb0dcd12e2a51fd550e]=1
        [de215ef978104a0e9efdc7b78a9d58cd529cf17a]=1
        [8509ef02eceb2bbb479cea10fe4a7ec6395f1a8b]=1
        [8934a7d6307267d301182f19ed162563717e29e3]=1
        [e942fb84fbe3a73a98a00d2a279425872b5fb9d2]=1
        [ecc63d0d3b0e1a62c90b58b1ccdb5ac16cb2400a]=1
        [b28b8b2fee6dfa6fcd13305c581bb835689ac3be]=1
        [140ede9c075c604632a87ee3bf0e881fb485d0e7]=1
        [287eaab3b2777daa5d0d0cf72d977196ba54efb7]=1
        [f25486c3d4aa472fec79150f2c41ed4333395d3d]=1
        [ab1702c7af9959366a5ddc4a75b4357d4e9ebdc1]=1
        [0391d0823ebfd7c37c07a54b8726cc417183a95f]=1
        [e59c562b3f6894f84c715772c4b116d7b5c01348]=1
    )
fi

info "scan project_list... ${project_list[*]}"
debug "project_list count: ${#project_list[@]}"

check_list=()
declare -A seen_jobs

for one_project in "${project_list[@]}"; do
    info "Processing project: $one_project"

    #mapfile -t bug_files < <(find . -name '*bug*json' -type f | grep "$one_project" || true)
	mapfile -t bug_files < <(
    find . -path './out' -prune -o -name '*bug*json' -type f -print 2>/dev/null |
    grep -F "$one_project" || true
	)

    debug "Matched bug files count: ${#bug_files[@]}"
    if [[ ${#bug_files[@]} -gt 0 ]]; then
        printf '[DEBUG] matched bug file: %s\n' "${bug_files[@]}" >&2
    fi

    if [[ ${#bug_files[@]} -eq 0 ]]; then
        error "No matching bug json files found for project: $one_project"
        continue
    fi

    mapfile -t commit_pairs < <(
        printf '%s\n' "${bug_files[@]}" |
        xargs jq -r '.[] | select(.commit_after != null and .commit_before != null) | "\(.commit_after) \(.commit_before)"' 2>/dev/null
    )

    debug "valid commit pair count: ${#commit_pairs[@]}"

    if [[ ${#commit_pairs[@]} -eq 0 ]]; then
        error "No valid commit pairs found for project: $one_project"
        continue
    fi

    for pair in "${commit_pairs[@]}"; do
        read -r commit_after commit_before <<< "$pair"

        if [[ -z "$commit_after" || -z "$commit_before" || "$commit_after" == "null" || "$commit_before" == "null" ]]; then
            error "Skipping invalid commit pair for $one_project: after='${commit_after}' before='${commit_before}'"
            continue
        fi

        # In mini mode, filter commit pairs to bugs_14.txt whitelist
        if [[ "$mode" == "mini" && -z "${MINI_COMMITS[$commit_after]:-}" ]]; then
            debug "Excluded by commit whitelist: $one_project $commit_after"
            continue
        fi

        job_key="${one_project}|${commit_after}|${commit_before}"
        if [[ -n "${seen_jobs[$job_key]:-}" ]]; then
            debug "Skipping duplicate job: $job_key"
            continue
        fi
        seen_jobs[$job_key]=1

        repo="bash ${git_setup_script} ${one_project} ${commit_after} ${commit_before}"
        check_list+=("$repo")
        debug "Added command: $repo"
    done
done

info "now will setup totally ${#check_list[@]} projects"



cpu_count=$(( ($(nproc) - 1) / 2 ))
if [[ "$cpu_count" -lt 1 ]]; then
    cpu_count=1
fi
debug "cpu_count: $cpu_count"


run_checkout() {
    debug "Writing checklist to /tmp/checklist.txt"
    printf "%s\n" "${check_list[@]}" > /tmp/checklist.txt

    info "Checklist written to /tmp/checklist.txt"

    local dedup_file="/tmp/checklist_dedup.txt"
    awk '!seen[$0]++' /tmp/checklist.txt > "$dedup_file"
    mv "$dedup_file" /tmp/checklist.txt

    local log_dir="/tmp/bulk_git_clone_logs"
    mkdir -p "$log_dir"
    : > /tmp/checklist_failed.txt

    xargs -I {} -P "$cpu_count" bash -c '
        cmd="$1"
        log_dir="$2"

        safe_name="$(echo "$cmd" | sed "s#[ /]#_#g")"
        log_file="${log_dir}/${safe_name}.log"

        echo "[RUN] $cmd" >&2
        echo "[RUN] $cmd" > "$log_file"

        if ! eval "$cmd" >> "$log_file" 2>&1; then
            echo "[FAILED] $cmd" >&2
            echo "[FAILED LOG] $log_file" >&2
            printf "%s\n" "$cmd :: $log_file" >> /tmp/checklist_failed.txt
            exit 1
        fi

        echo "[OK] $cmd" >&2
    ' _ {} "$log_dir" < /tmp/checklist.txt

    if [[ -s /tmp/checklist_failed.txt ]]; then
        error "One or more git setup commands failed"
        error "Failed commands:"
        cat /tmp/checklist_failed.txt >&2
        exit 1
    fi
}

run_checkout
