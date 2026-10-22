#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

PYTHON=""
if [ -x "backend/.venv/bin/python" ]; then
  PYTHON="backend/.venv/bin/python"
elif [ -x "backend/.venv/Scripts/python.exe" ]; then
  PYTHON="backend/.venv/Scripts/python.exe"
else
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 \
      && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1; then
      PYTHON="$candidate"
      break
    fi
  done
fi

if [ -z "$PYTHON" ]; then
  echo "未找到 Python 3.12+，请安装后加入 PATH。" >&2
  exit 1
fi

exec "$PYTHON" scripts/start.py "$@"
