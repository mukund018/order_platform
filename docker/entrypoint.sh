#!/bin/sh
# Shared entrypoint for every python container.
#
# CMD is always given as a python module and its arguments, e.g.
#   uvicorn app.main:app --host 0.0.0.0 --port 8002
# so debugpy can be slipped in front of it without a second Dockerfile.
set -e

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    alembic upgrade head
fi

if [ "${DEBUG_ATTACH:-0}" = "1" ]; then
    if [ "${DEBUG_WAIT:-0}" = "1" ]; then
        # Blocks until VS Code attaches. Only useful when the bug is in startup.
        exec python -m debugpy --listen "0.0.0.0:${DEBUG_PORT:-5678}" --wait-for-client -m "$@"
    fi
    exec python -m debugpy --listen "0.0.0.0:${DEBUG_PORT:-5678}" -m "$@"
fi

exec python -m "$@"
