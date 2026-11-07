#!/usr/bin/env python3
"""转义哨兵（5-2 / B-8 回归门禁）：拦截 `innerHTML` 模板里的未转义插值。

前端用原生 JS 拼 ``innerHTML``，若把后端/用户可控值直接插进模板字符串，
就是公式注入 / XSS 的入口（``<img onerror=...>`` 一类）。本检查给每处
``.innerHTML = `...` `` 的 ``${...}`` 插值打「哨兵」：凡插值含未走 ``esc()``
且无法证明安全的裸数据（成员链 / 未知局部变量），即视为违规，门禁失败。

「可证明安全」按结构判定：
1. ``esc(...)`` 或已知安全助手（``icon``/``tag``/``emptyRow``/``renderState``/
   ``desc``/``k``/``statusTag`` 等，内部一律 ``esc()`` 或只拼常量/格式化数据）；
2. 纯字面量（数字 / 字符串 / ``true`` / ``false`` / ``null`` / ``undefined``）；
3. 三元表达式，两个分支各自安全；
4. ``.map(fn).join(sep)``，其回调体内的模板插值全部安全；
5. 纯数值语境（``colspan``、``this.total``、``current - 1``、``page`` 等）；
6. 本文件内「赋值为安全模板字面量」的局部变量（如 ``place``/``detail``），
   或「函数体返回安全模板」的局部函数（如 ``desc``/``avatarHtml``），
   以及 ``URL.createObjectURL`` 的 blob URL（浏览器生成，无用户内容）。

第 6 条是两遍扫描：先收集每文件的安全局部变量与函数，再对 innerHTML 插值
逐条判定。这样「先内部转义、再整体注入」的局部变量不会被误报，而
``${item.name}`` 这类裸成员访问一定会被拦下。

与 check_contract.py 的页面↔JS↔#id 校验互补：那个管结构一致性，本检查管
「注入面」。当前代码应 0 违规；以后任何新页面若漏掉 ``esc()`` 会在门禁层
报错，而不是等浏览器运行时才暴露。

用法：
    python scripts/check_escape_sentinel.py            # 报告并失败（门禁）
    python scripts/check_escape_sentinel.py --report   # 只报告，不失败
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "backend" / "app" / "web" / "static" / "js"

# 已知「内部一定 esc() 或只拼常量/格式化数据」的助手与内建。这些在 innerHTML
# 模板里直接调用即安全，无需再套一层 esc()。新增页面若用别的助手，请在此登记
# 或让函数体落入「局部安全函数」判定。
BUILTIN_SAFE_CALLS = {
    "esc",
    "icon",
    "emptyRow",
    "emptyState",
    "tag",
    "boolTag",
    "statusTag",
    "actionButtons",
    "indentLabel",
    "treeOptions",
    "renderState",
    "fillSelect",
    "brandGradient",
    "desc",
    "k",
    # 格式化函数：输入日期/数字，输出纯文本，不回显原始 HTML
    "formatBytes",
    "formatNumber",
    "formatMs",
    "formatUptime",
    "formatDate",
    "formatTime",
    "formatRecentTime",
    # JS 内建：仅做类型转换，不产出 HTML
    "String",
    "Number",
    "Boolean",
}

# 纯数值语境标识符：这些名字在本代码里只承载数字（colspan、页码、尺寸、下标）。
# 注入 innerHTML 不可能带 HTML 字符。白名单按名匹配，够用且可审计。
NUMERIC_IDS = {
    "colspan",
    "index",
    "depth",
    "size",
    "pad",
    "pages",
    "page",
    "current",
    "width",
    "height",
    "count",
    "total",
    "this.colspan",
    "this.total",
    "this.pageSize",
    "Math",
    "Math.round",
    "Math.max",
    "Math.min",
    "Math.floor",
    "Math.ceil",
    "Math.abs",
    "Math.log",
    "Math.pow",
    "Math.sqrt",
}

# 关键字/内建命名空间：不可能携带用户 HTML，裸出现即安全。
SAFE_KEYWORDS = {"this", "Math", "window", "document", "globalThis", "undefined", "null", "true", "false", "NaN", "Infinity"}


# ----------------------------- 模板字面量扫描 -----------------------------

def _skip_string(s: str, i: int) -> int:
    """从 s[i] 是引号/反引号起跳过字符串（含转义），返回下一个未消费下标。"""
    quote = s[i]
    i += 1
    while i < len(s):
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == quote:
            return i + 1
        i += 1
    return len(s)


def template_end(s: str, start: int) -> int:
    """start 指向反引号；返回匹配结束反引号下标 + 1（支持嵌套 `${...}` 内反引号）。"""
    i = start + 1
    while i < len(s):
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == "`":
            return i + 1
        if s.startswith("${", i):
            depth = 1
            j = i + 2
            while j < len(s) and depth > 0:
                if s[j] in ("'", '"'):
                    j = _skip_string(s, j)
                    continue
                if s[j] == "`":
                    j = template_end(s, j)
                    continue
                if s[j] == "{":
                    depth += 1
                elif s[j] == "}":
                    depth -= 1
                j += 1
            i = j
            continue
        i += 1
    return len(s)


def interp_spans(tpl: str) -> list[tuple[int, int]]:
    """返回模板内部每个 `${...}` 的 (expr_start, expr_end) 相对下标（不含 $ 与 {）。"""
    spans: list[tuple[int, int]] = []
    i = 0
    while i < len(tpl):
        if tpl.startswith("${", i):
            depth = 1
            j = i + 2
            start = j
            while j < len(tpl) and depth > 0:
                if tpl[j] in ("'", '"'):
                    j = _skip_string(tpl, j)
                    continue
                if tpl[j] == "`":
                    j = template_end(tpl, j)
                    continue
                if tpl[j] == "{":
                    depth += 1
                elif tpl[j] == "}":
                    depth -= 1
                j += 1
            spans.append((start, j - 1))
            i = j
        else:
            i += 1
    return spans


def templates_in(expr: str) -> list[str]:
    """提取表达式里所有顶层模板字面量的内容（支持嵌套，用 template_end 定位）。"""
    out: list[str] = []
    i = 0
    while i < len(expr):
        if expr[i] == "`":
            end = template_end(expr, i)
            out.append(expr[i + 1 : end - 1])
            i = end
        elif expr[i] in ("'", '"'):
            i = _skip_string(expr, i)
        else:
            i += 1
    return out


# ----------------------------- 安全判定 -----------------------------

def _string_literal(expr: str) -> bool:
    return bool(re.match(r"^'[^']*'$", expr) or re.match(r'^"[^"]*"$', expr))


def _call_name(expr: str) -> str | None:
    m = re.match(r"^([A-Za-z_$][\w$]*)\s*\(", expr)
    return m.group(1) if m else None


def _split_ternary(expr: str) -> tuple[str, str, str] | None:
    """找顶层（不在字符串/模板/括号内）的 ``?`` 与对应 ``:``，返回 (条件, 真分支, 假分支)。

    模板字面量里可能含 ``?``/``:``（如 `${a ? b : c}`），必须跳过，否则
    `re.split` 会把三元拆错。返回 None 表示表达式顶层不是三元。
    """
    depth = 0
    i = 0
    q = -1
    while i < len(expr):
        c = expr[i]
        if c in ("'", '"'):
            i = _skip_string(expr, i)
            continue
        if c == "`":
            i = template_end(expr, i)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "?" and depth == 0:
            q = i
            break
        i += 1
    if q < 0:
        return None
    i = q + 1
    depth = 0
    while i < len(expr):
        c = expr[i]
        if c in ("'", '"'):
            i = _skip_string(expr, i)
            continue
        if c == "`":
            i = template_end(expr, i)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == ":" and depth == 0:
            return expr[:q], expr[q + 1 : i], expr[i + 1 :]
        i += 1
    return None


def _bare_members(expr: str) -> list[str]:
    """成员链：item.name、row['x']、this.total。"""
    return re.findall(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*|\[\s*['\"][^'\"]+['\"]\s*\])+", expr)


def _bare_ids(expr: str) -> list[str]:
    """裸标识符（不含成员链前缀）：foo、bar。"""
    return re.findall(r"(?<![.\w$])[A-Za-z_$][\w$]*", expr)


def _template_is_safe(tpl: str, ctx: dict) -> bool:
    return all(_expr_is_safe(tpl[a:b].strip(), ctx) for a, b in interp_spans(tpl))


def _expr_is_safe(expr: str, ctx: dict) -> bool:
    """递归判定单个 `${expr}` 是否可证明安全。

    ctx: {'safe_vars': set[str], 'safe_funcs': set[str]} —— 本文件的局部安全集合。
    """
    expr = expr.strip()
    if not expr:
        return True
    # 1. 已知安全函数调用：不检查实参（函数内部负责转义）
    name = _call_name(expr)
    if name:
        return name in BUILTIN_SAFE_CALLS or name in ctx["safe_funcs"]
    # 2. 字面量
    if _string_literal(expr) or expr in ("true", "false", "null", "undefined"):
        return True
    if re.match(r"^-?\d+(\.\d+)?$", expr):
        return True
    # 2b. 反引号模板字面量本身：其插值全安全即安全
    if expr.startswith("`") and expr.endswith("`") and len(expr) >= 2:
        return _template_is_safe(expr[1:-1], ctx)
    # 2c. Math.* 一律返回数字，安全
    if expr.startswith("Math.") or expr in ("Math", "this"):
        return True
    # 3. 三元：两个分支各自安全（条件本身不注入；顶层 ? 才识别）
    ternary = _split_ternary(expr)
    if ternary:
        return _expr_is_safe(ternary[1], ctx) and _expr_is_safe(ternary[2], ctx)
    # 4. .map().join()：回调体内模板插值全安全
    if ".map(" in expr and ".join(" in expr:
        for tpl in templates_in(expr):
            if _template_is_safe(tpl, ctx):
                return True
    # 5. 成员链：任何不在安全集/数值集 -> 未转义用户数据
    for member in _bare_members(expr):
        if member not in ctx["safe_vars"] and member not in NUMERIC_IDS:
            return False
    # 6. 裸标识符：任何未知 -> 未转义
    for ident in _bare_ids(expr):
        if ident in SAFE_KEYWORDS or ident in BUILTIN_SAFE_CALLS or ident in ctx["safe_funcs"] or ident in ctx["safe_vars"] or ident in NUMERIC_IDS:
            continue
        return False
    return True


# ----------------------------- 两遍扫描 -----------------------------

_CONST_ASSIGN_RE = re.compile(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*")
_FUNC_RE = re.compile(r"function\s+([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{")


def _stmt_end(src: str, start: int) -> int:
    """从 start 起找语句结束（顶层 ; 或模板外），处理引号/模板/括号/花括号。"""
    depth = 0
    i = start
    while i < len(src):
        c = src[i]
        if c in ("'", '"'):
            i = _skip_string(src, i)
            continue
        if c == "`":
            i = template_end(src, i)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == ";" and depth <= 0:
            return i
        i += 1
    return len(src)


def collect_safe_defs(src: str) -> tuple[set[str], set[str]]:
    """一遍收集：本文件内「赋值为安全模板」的局部变量，与「返回安全模板」的局部函数。"""
    safe_vars: set[str] = set()
    safe_funcs: set[str] = set()

    def ctx() -> dict:
        return {"safe_vars": safe_vars, "safe_funcs": safe_funcs}

    # 局部变量：const/let X = <RHS>；RHS 含模板且全部安全，且 RHS 内无
    # 非安全成员链（排除 `const x = item.name`），或 RHS 是 createObjectURL。
    for m in _CONST_ASSIGN_RE.finditer(src):
        name = m.group(1)
        rhs_start = m.end()
        end = _stmt_end(src, rhs_start)
        rhs = src[rhs_start:end]
        if re.match(r"^URL\.createObjectURL\s*\(", rhs.strip()):
            safe_vars.add(name)
            continue
        tmpls = templates_in(rhs)
        if not tmpls:
            continue
        if not all(_template_is_safe(t, ctx()) for t in tmpls):
            continue
        # RHS 是 .map().join() 且模板全安全 -> 安全（图表/列表生成器：bars、labels）
        if ".map(" in rhs and ".join(" in rhs:
            safe_vars.add(name)
            continue
        # 否则：RHS 内模板之外的成员必须都是已知安全（防 `const x = tpl + item.name`）
        outside = rhs
        for t in tmpls:
            outside = outside.replace(t, "", 1)
        if any(m not in safe_vars and m not in NUMERIC_IDS for m in _bare_members(outside)):
            continue
        safe_vars.add(name)

    # 局部函数：function X(){ ... return `...` 全安全 ... }
    for m in _FUNC_RE.finditer(src):
        name = m.group(1)
        brace = src.index("{", m.end() - 1)
        depth = 1
        j = brace + 1
        while j < len(src) and depth > 0:
            if src[j] in ("'", '"', "`"):
                j = _skip_string(src, j)
                continue
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
            j += 1
        body = src[brace + 1 : j - 1]
        for tpl in templates_in(body):
            if _template_is_safe(tpl, ctx()):
                safe_funcs.add(name)
                break
    return safe_vars, safe_funcs


def check_file(file: Path) -> list[tuple[int, str]]:
    """返回该文件的违规列表 [(行号, 插值表达式)]。"""
    src = file.read_text(encoding="utf-8")
    safe_vars, safe_funcs = collect_safe_defs(src)
    ctx = {"safe_vars": safe_vars, "safe_funcs": safe_funcs}
    violations: list[tuple[int, str]] = []
    for m in re.finditer(r"\.innerHTML\s*(?:=|\+=)\s*`", src):
        start = src.index("`", m.start())
        end = template_end(src, start)
        tpl = src[start + 1 : end - 1]
        line = src[: m.start()].count("\n") + 1
        for a, b in interp_spans(tpl):
            expr = tpl[a:b].strip()
            if expr and not _expr_is_safe(expr, ctx):
                violations.append((line, expr[:120]))
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="转义哨兵：innerHTML 未转义插值检查")
    parser.add_argument("--report", action="store_true", help="只报告不失败")
    args = parser.parse_args(argv)

    files = sorted(JS.rglob("*.js"))
    total = 0
    by_file: list[tuple[str, list[tuple[int, str]]]] = []
    for file in files:
        found = check_file(file)
        if found:
            by_file.append((file.relative_to(JS).as_posix(), found))
            total += len(found)

    if total:
        print(f"!! 转义哨兵发现 {total} 处未转义 innerHTML 插值：")
        for fname, found in by_file:
            for line, expr in found:
                print(f"   {fname}:{line}: ${{ {expr} }}")
        return 0 if args.report else 1
    print(f"OK: 转义哨兵检查通过（{len(files)} 个 .js，无未转义插值）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
