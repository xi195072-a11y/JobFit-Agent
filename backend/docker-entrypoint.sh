#!/bin/sh
# Phase 6 §5：容器启动即保证 schema 已就绪（幂等 migration），再拉起 API。
# 依赖 compose 的 db healthcheck（depends_on: service_healthy）。
set -e

echo "[entrypoint] alembic upgrade head"
alembic upgrade head

echo "[entrypoint] starting api"
exec "$@"
