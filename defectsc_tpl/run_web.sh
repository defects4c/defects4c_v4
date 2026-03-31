#!/usr/bin/env bash
set -euo pipefail

if [ -d /src/.venv ]; then
    source /src/.venv/bin/activate
fi

exec gunicorn -k uvicorn.workers.UvicornWorker \
    --workers "${D4C_WORKERS:-4}" \
    --bind "0.0.0.0:${D4C_PORT:-11111}" \
    --timeout 600 \
    --graceful-timeout 120 \
    --keep-alive 5 \
    --backlog 2048 \
    --worker-tmp-dir /tmp \
    webapp:app
