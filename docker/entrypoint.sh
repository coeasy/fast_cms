#!/bin/sh
set -eu

cd /app/backend

if [ "${AUTO_MIGRATE:-true}" = "true" ]; then
  echo "[entrypoint] 执行数据库迁移"
  # P1-4：数据库级迁移锁在 CLI 内获取（MySQL GET_LOCK / SQLite 文件锁），
  # 多副本并发启动只允许一个真正迁移；数据库冷启动（如 MySQL 首启）未就绪时
  # 带退避重试，避免进程在 DB 就绪前退出。
  retries="${MIGRATE_RETRIES:-30}"
  i=0
  until python -m app.cli db upgrade; do
    i=$((i + 1))
    if [ "$i" -ge "$retries" ]; then
      echo "[entrypoint] 数据库迁移在 ${retries} 次重试后仍失败" >&2
      exit 1
    fi
    echo "[entrypoint] 数据库未就绪或迁移被占用，第 ${i}/${retries} 次重试（5s 后）"
    sleep 5
  done
  # 镜像固定 APP_ENV=production，seed 会拒绝内置默认口令。此前 compose 把
  # INITIAL_ADMIN_PASSWORD 留空，导致「干净 checkout 直接 docker compose up」
  # 在 seed 这一步失败。这里在未显式注入时生成一次强口令并持久化到数据卷：
  # 一条命令能起、不用文档口令、重启后口令不变，三者同时成立。
  if [ -z "${INITIAL_ADMIN_PASSWORD:-}" ]; then
    pw_file="${DATA_DIR:-/app/backend/data}/initial_admin_password.txt"
    if [ -f "$pw_file" ]; then
      INITIAL_ADMIN_PASSWORD=$(cat "$pw_file")
    else
      INITIAL_ADMIN_PASSWORD=$(python -c 'import secrets; print(secrets.token_urlsafe(18))')
      (umask 077; printf '%s\n' "$INITIAL_ADMIN_PASSWORD" > "$pw_file")
      echo "[entrypoint] 首次启动：初始管理员 admin 的口令为 $INITIAL_ADMIN_PASSWORD" >&2
      echo "[entrypoint] 已同时保存到数据卷 $pw_file，请妥善保存后删除该文件" >&2
    fi
    export INITIAL_ADMIN_PASSWORD
  fi
  echo "[entrypoint] 写入基线数据"
  python -m app.cli seed
fi

if [ "${BACKUP_ON_START:-false}" = "true" ]; then
  echo "[entrypoint] 启动前备份"
  python -m app.cli backup || echo "[entrypoint] 备份失败或数据库尚未创建，已跳过"
fi

exec "$@"
