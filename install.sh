#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

# 依次尝试：已有 venv -> conda（Miniconda/Anaconda）-> 系统 python3
PYTHON=""
for candidate in "backend/.venv/bin/python" "backend/.venv/Scripts/python.exe"; do
  if [ -x "$candidate" ]; then PYTHON="$candidate"; break; fi
done

if [ -z "$PYTHON" ]; then
  for base in "$HOME/miniconda3" "$HOME/anaconda3" "$HOME/miniforge3" /opt/conda /opt/miniconda3 /opt/anaconda3 /usr/local/miniconda3 "${CONDA_PREFIX_1:-}"; do
    [ -n "$base" ] || continue
    if [ -x "$base/bin/python" ]; then PYTHON="$base/bin/python"; break; fi
  done
fi

if [ -z "$PYTHON" ]; then
  for candidate in "${CONDA_EXE:-}" conda mamba; do
    [ -n "$candidate" ] && command -v "$candidate" >/dev/null 2>&1 || continue
    base=$("$candidate" info --base 2>/dev/null || true)
    if [ -n "$base" ] && [ -x "$base/bin/python" ]; then PYTHON="$base/bin/python"; break; fi
  done
fi

if [ -z "$PYTHON" ]; then
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 \
      && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1; then
      PYTHON="$candidate"
      break
    fi
  done
fi

if [ -z "$PYTHON" ]; then
  echo "未找到 Python 3.12+，也没有可用的 Miniconda/Anaconda。" >&2
  echo "请先安装 Miniconda（https://docs.conda.io/en/latest/miniconda.html）或 Python 3.12+。" >&2
  exit 1
fi

echo "使用解释器：$PYTHON"
exec "$PYTHON" scripts/install.py "$@"
