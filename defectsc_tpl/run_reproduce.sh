#!/usr/bin/env bash
# run_reproduce.sh <project> [sha]
project=$1
project=$(basename "$project")
sha=$2

if [ ! -z "$sha" ]; then
    sha_list=($sha)
    size=1
else
    sha_list=$(jq -cr ".[]|.commit_after" /src/projects_v1/$project/bugs_list_new.json 2>/dev/null || \
               jq -cr ".[]|.commit_after" /src/projects/$project/bugs_list_new.json 2>/dev/null)
    size=$(echo "$sha_list" | wc -l)
fi

echo "size==$size"

for one_sha in $sha_list; do
    test_log="/out/$project/logs/${one_sha}.log"
    if [[ ! -f $test_log ]]; then
        python3 /src/bug_helper_v1_out2.py reproduce "${project}@${one_sha}"
    else
        echo "exist.....-->"$test_log
    fi
done
