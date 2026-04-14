#!/usr/bin/env bash
set -uo pipefail

[ "$EUID" -ne 0 ] && exit 1

USERNAME="defects4c_user"


chown -R "$USERNAME:$USERNAME" /src/ || true
chown -R "$USERNAME:$USERNAME" /out/ || true
chown -R "$USERNAME:$USERNAME" /patches/ || true
chown -R "$USERNAME:$USERNAME" /workspace/ || true

