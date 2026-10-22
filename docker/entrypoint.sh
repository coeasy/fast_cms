#!/bin/sh
set -eu

cd /app/backend

if [ "${AUTO_MIGRATE:-true}" = "true" ]; then
  echo "[entrypoint] 执行数据库迁移"
  python -m app.cli db upgrade
  python -m app.cli seed
fi

if [ "${BACKUP_ON_START:-false}" = "true" ]; then
  echo "[entrypoint] 启动前备份"
  python -m app.cli backup || echo "[entrypoint] 备份失败或数据库尚未创建，已跳过"
fi

exec "$@"
