#!/usr/bin/env bash
# run_patch.sh <sha>
sha=$1

project=$(find projects_v1/ projects/ -name "bugs_list*.json" 2>/dev/null | \
          xargs grep "commit_after\": \"${sha}\"" 2>/dev/null | head -n 1 | \
          awk -F "/" '{print $2}')

if [[ -z "$project" ]]; then
    echo "Project not found for SHA: $sha"
    exit 1
fi

size=$(find /patches/$project/ -name "*@${sha}___*" 2>/dev/null | wc -l)
patch_list=$(find /patches/$project/ -name "*@${sha}___*" 2>/dev/null)

echo "size==$size"

if [[ $size -eq 0 ]]; then
    echo "empty queue"
    exit 1
fi

for one_file in $patch_list; do
    tmp=$(basename $one_file)
    OLD_IFS=$IFS
    IFS="@"
    read -ra parts <<< "$tmp"
    IFS=$OLD_IFS
    md5="${parts[0]}"
    sha_tmp="${parts[1]}"
    OLD_IFS=$IFS
    IFS="___"
    read -ra parts <<< "$sha_tmp"
    IFS=$OLD_IFS
    sha="${parts[0]}"

    test_log="/out/${project}/logs/patch_${sha}_${md5}.log"

    if [[ ! -f $test_log ]]; then
        python3 bug_helper_v1_out2.py fix "${project}@${sha}" $one_file
    else
        echo "exist.....-->"$test_log
    fi
done
