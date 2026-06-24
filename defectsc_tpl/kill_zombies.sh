#!/usr/bin/env bash
# kill_zombies.sh - Kill runaway build/test processes exceeding timeout.
# For Defects4C benchmark (~350 bugs). Run via cron every 5 minutes.
# */5 * * * * /src/kill_zombies.sh >> /out/kill_zombies.log 2>&1

MAX_AGE_MIN=45
LOG_PREFIX="[kill_zombies $(date +%%Y-%%m-%%d\ %%H:%%M:%%S)]"
killed=0

for pattern in autotest inplace_build.sh run_reproduce.sh run_puretest.sh run_patch.sh; do
    while read -r pid etime cmd; do
        [ -z "$pid" ] && continue
        days=0; hours=0; mins=0
        if [[ "$etime" == *-* ]]; then
            days="${etime%%-*}"
            etime="${etime#*-}"
        fi
        IFS=: read -ra parts <<< "$etime"
        case ${#parts[@]} in
            3) hours=${parts[0]}; mins=${parts[1]} ;;
            2) mins=${parts[0]} ;;
        esac
        total_min=$(( days * 1440 + hours * 60 + mins ))
        if [ "$total_min" -ge "$MAX_AGE_MIN" ]; then
            echo "$LOG_PREFIX KILL pid=$pid age=${total_min}m pattern=$pattern cmd=$cmd"
            pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d " ")
            if [ -n "$pgid" ] && [ "$pgid" != "1" ] && [ "$pgid" != "0" ]; then
                kill -9 -"$pgid" 2>/dev/null || kill -9 "$pid" 2>/dev/null
            else
                kill -9 "$pid" 2>/dev/null
            fi
            killed=$((killed + 1))
        fi
    done < <(ps -eo pid,etime,args 2>/dev/null | grep -E "$pattern" | grep -v grep | grep -v kill_zombies)
done

if [ "$killed" -gt 0 ]; then
    echo "$LOG_PREFIX Killed $killed zombie process(es)"
fi
