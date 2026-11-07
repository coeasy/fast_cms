#!/usr/bin/env python3
"""前端静态校验（P2-2 回归门禁；P2-9 让“前端类型检查”承诺落到真实命令）。

扫描 backend/app/web/static/js 下所有 .js，验证：
1. 每个 ``import { x } from '<rel>'`` 的符号 x 在目标模块确实被导出；
2. ``export * from`` 链（core.js 聚合出口）可解析；
3. 动态 ``import('<rel>')`` 目标文件存在；
4. 每个 .js 通过 ``node --check`` 语法校验（前端为原生 JS，无 TypeScript，
   因此用 Node 语法检查 + 导入解析共同替代历史文档里的“前端类型检查”；
   node 不可用时该项降级为警告，不影响导入解析这层硬门禁）。

核心文件拆分后，页面改为按需从 core/<module>.js 导入；本检查保证任何
导入符号/路径的拼写错误在门禁层就被拦截，而不是等到浏览器运行时才暴露。

用法：
    python scripts/check_frontend_imports.py
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "backend" / "app" / "web" / "static" / "js"

EXPORT_RE = re.compile(r"export\s+(?:async\s+)?(?:function|const|let|var|class)\s+([A-Za-z_$][\w$]*)")
EXPORT_STAR_RE = re.compile(r"export\s+\*\s+from\s*'([^']+)'")
IMPORT_RE = re.compile(r"import\s*\{([\s\S]*?)\}\s*from\s*'([^']+)'")
DYN_IMPORT_RE = re.compile(r"await\s+import\('([^']+)'\)")

export_cache: dict[Path, set[str]] = {}


def exports_of(file: Path) -> set[str]:
    file = file.resolve()
    if file in export_cache:
        return export_cache[file]
    export_cache[file] = set()
    if not file.exists():
        return export_cache[file]
    src = file.read_text(encoding="utf-8")
    names: set[str] = set()
    for m in EXPORT_RE.finditer(src):
        names.add(m.group(1))
    for m in EXPORT_STAR_RE.finditer(src):
        names |= exports_of((file.parent / m.group(1)).resolve())
    export_cache[file] = names
    return names


def find_node() -> str | None:
    """优先本机 PATH 的 node，其次 WorkBuddy 托管的 node；都没有返回 None。"""
    exe = shutil.which("node")
    if exe:
        return exe
    managed = Path.home() / ".workbuddy-ai" / "binaries" / "node" / "versions"
    for entry in sorted(managed.glob("*/node.exe"), reverse=True):
        if entry.exists():
            return str(entry)
    return None


def check_syntax(js_dir: Path) -> tuple[list[str], int]:
    """用 node --check 校验每个 .js 的语法；返回 (错误列表, 检查文件数)。"""
    node = find_node()
    if not node:
        print("!! node 不可用，跳过 JS 语法校验（仅导入解析作为硬门禁）", flush=True)
        return [], 0
    errors: list[str] = []
    count = 0
    for f in sorted(js_dir.rglob("*.js")):
        result = subprocess.run(
            [node, "--check", str(f)],
            capture_output=True,
            text=True,
            check=False,
        )
        count += 1
        if result.returncode != 0:
            errors.append(f"{f.relative_to(js_dir)}: {result.stderr.strip() or '语法错误'}")
    return errors, count


def main() -> int:
    problems: list[str] = []
    checked = 0
    for f in sorted(JS.rglob("*.js")):
        src = f.read_text(encoding="utf-8")
        for m in IMPORT_RE.finditer(src):
            spec = m.group(2)
            if not spec.startswith("."):
                continue
            target = (f.parent / spec).resolve()
            names = exports_of(target)
            checked += 1
            for tok in m.group(1).split(","):
                tok = tok.strip()
                if not tok:
                    continue
                name = tok.split(" as ")[-1].strip()
                if name not in names:
                    problems.append(f"{f.relative_to(JS)}: `{name}` not exported by {spec}")
        for m in DYN_IMPORT_RE.finditer(src):
            spec = m.group(1)
            if spec.startswith(".") and not (f.parent / spec).resolve().exists():
                problems.append(f"{f.relative_to(JS)}: dynamic import target missing: {spec}")
            checked += 1

    syntax_errors, syntax_count = check_syntax(JS)

    if problems or syntax_errors:
        if problems:
            print(f"前端导入校验失败（{len(problems)} 处）：")
            for p in problems:
                print(f"  - {p}")
        if syntax_errors:
            print(f"前端 JS 语法校验失败（{len(syntax_errors)} 处）：")
            for p in syntax_errors:
                print(f"  - {p}")
        return 1
    print(f"OK: {checked} 条导入语句全部解析，符号/路径一致；{syntax_count} 个 .js 语法校验通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
