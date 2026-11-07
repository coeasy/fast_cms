#!/usr/bin/env python3
"""依赖漏洞扫描门禁（B-7 / Phase 0-2）。

CI 会在安装阶段装好 ``pip-audit`` 并强约束：依赖里有已知 CVE 即非零退出。
本地跑 ``make check`` 时 pip-audit 可能没装（属于 dev/CI 工具，不是运行时依赖），
此时打印提示并返回 0（跳过），避免本地门禁因可选工具缺失而误红。

用法：
    python scripts/check_security.py
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQ = ROOT / "backend" / "requirements.txt"


def main() -> int:
    if importlib.util.find_spec("pip_audit") is None:
        print("[security] pip-audit 未安装，跳过依赖漏洞扫描（CI 会安装并强制约束）")
        return 0
    if not REQ.exists():
        print(f"[security] 未找到 {REQ}，跳过")
        return 0
    print(f"[security] pip-audit 扫描 {REQ} ...")
    proc = subprocess.run(
        [sys.executable, "-m", "pip_audit", "-r", str(REQ)],
        check=False,
    )
    if proc.returncode != 0:
        print("!! 依赖存在已知 CVE，详见上方 pip-audit 输出", flush=True)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
