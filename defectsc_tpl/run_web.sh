#!/usr/bin/env bash
set -euo pipefail

if [ -d /src/.venv ]; then
    source /src/.venv/bin/activate
fi



: "${D4C_WORKERS:=$(( $(nproc 2>/dev/null || echo 4) / 4 ))}"
[ "$D4C_WORKERS" -lt 2 ] && D4C_WORKERS=2
echo "Starting gunicorn with $D4C_WORKERS workers (nproc=$(nproc 2>/dev/null || echo unknown))"

exec gunicorn -k uvicorn.workers.UvicornWorker \
    --workers "$D4C_WORKERS" \
    --bind "0.0.0.0:${D4C_PORT:-11111}" \
    --timeout 600 \
    --graceful-timeout 120 \
    --keep-alive 5 \
    --backlog 2048 \
    --worker-tmp-dir /tmp \
    --reload \
    --access-logfile /out/access.log \
    --error-logfile /out/error.log \
    --capture-output \
    webapp:app   2>&1  |tee /out/exec.log 

#exec gunicorn -k uvicorn.workers.UvicornWorker \
#    --workers "${D4C_WORKERS:-4}" \
#    --bind "0.0.0.0:${D4C_PORT:-11111}" \
#    --timeout 600 \
#    --graceful-timeout 120 \
#    --keep-alive 5 \
#    --backlog 2048 \
#    --worker-tmp-dir /tmp \
#    webapp:app
