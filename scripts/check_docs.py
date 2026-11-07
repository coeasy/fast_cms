"""校验文档里写的命令与引用确实存在。

文档最容易在没人提醒的情况下写「看起来合理但根本不存在」的命令——照抄就会报错，
而且是使用者第一次接触项目时立刻遇到的错误，代价最高。

本脚本检查六件事：

1. ``make <target>`` —— 目标必须真实存在于 ``Makefile``
2. ``fastcms <command>`` —— 子命令必须真实存在于 ``scripts/fastcms.py``
3. ``app.cli <subcommand>`` —— 子命令必须真实存在于 ``backend/app/cli.py``
4. ``scripts/<name>.py`` —— 引用的脚本文件必须真实存在
5. 反引号里的文件路径（Phase 12-3）—— ``kernel/db.py`` 这类引用必须能在仓库里解析到
6. 反引号里的 ``app.*`` 模块路径（Phase 12-3）—— 必须对应真实存在的模块/包文件
7. Markdown 相对链接 —— ``[文字](docs/xxx.md)`` 指向的 ``.md`` 文件必须存在

豁免白名单（P2-7）必须逐条带：原因 + 负责人 + 到期时间。CI 命中某条豁免时会
打印人工复核警告（不再静默），超过到期时间的豁免直接判失败——长期漂移不能静默通过。

用法：
    python scripts/check_docs.py          只检查文档，有漂移就返回非 0
    python scripts/check_docs.py --list   打印解析到的全部命令集合

退出码非 0 即表示需要修正文档，因此可以并入 CI（见 ``make docs-check``）。
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"
README = ROOT / "README.md"
MAKEFILE = ROOT / "Makefile"
CLI_FILE = ROOT / "backend" / "app" / "cli.py"
FASTCMS_FILE = ROOT / "scripts" / "fastcms.py"


@dataclass(frozen=True)
class KnownException:
    """白名单豁免条目：必须可审计（原因、负责人、到期时间）。"""

    reason: str
    owner: str
    expires: date


# 文档中出现、但故意不校验的命令。每条豁免都必须写明原因/负责人/到期时间，
# review 时逐条确认——这份清单本身就是要被质疑的；到期未续期即判失败。
KNOWN_EXCEPTIONS: dict[str, KnownException] = {
    # 方案文档里以「已修复的文档漂移」作为证据引用它，不是给用户照抄的命令
    "dev-all": KnownException(
        reason="docs/架构梳理与优化方案.md 中作为历史证据引用，非可执行命令",
        owner="@arch",
        expires=date(2027, 6, 30),
    ),
    # 统一 CLI 规划中 P1-2 的模块脚手架，尚未实现（见架构方案 §4.4）；实现后移除本条豁免
    "new-module": KnownException(
        reason="P1-2 规划中的模块脚手架命令，尚未实现（见架构方案 §4.4）",
        owner="@platform",
        expires=date(2027, 6, 30),
    ),
    # 分层规范里的示意占位符（讲解「不要点太多层」用的假模块名），非真实引用
    "app.modules.a.b.c.services.x": KnownException(
        reason="分层规范中的示意占位符（分层示例），非真实模块",
        owner="@arch",
        expires=date(2027, 6, 30),
    ),
    # ---- 文件引用豁免（Phase 12-3）----
    "openapi.json": KnownException(
        reason="构建产物：gate contract 组的 export-openapi 生成，不进仓库",
        owner="@platform",
        expires=date(2027, 6, 30),
    ),
    "openapi.baseline.json": KnownException(
        reason="契约基线：check_contract 首次校验时自动初始化，不进仓库",
        owner="@platform",
        expires=date(2027, 6, 30),
    ),
    "manifest.json": KnownException(
        reason="代码生成器的产物清单（生成到用户项目时才存在），见 docs/代码生成器.md",
        owner="@platform",
        expires=date(2027, 6, 30),
    ),
    "frontend/index.js": KnownException(
        reason="代码生成器的产物文件（生成到用户项目时才存在），见 docs/代码生成器.md",
        owner="@platform",
        expires=date(2027, 6, 30),
    ),
    "package.json": KnownException(
        reason="开发计划中规划的 npm 工具链，按四条硬约束暂未引入仓库",
        owner="@arch",
        expires=date(2027, 6, 30),
    ),
    "kernel/models.py": KnownException(
        reason="历史评审文档（架构评审 2026-10-29）记录拆分前的事实，现为 kernel/models/ 包",
        owner="@arch",
        expires=date(2027, 6, 30),
    ),
    "app/modules/schedule/service.py": KnownException(
        reason="历史审计文档引用旧路径（正文已注明现为 ops/schedule/service.py）",
        owner="@arch",
        expires=date(2027, 6, 30),
    ),
}

MAKE_REF = re.compile(r"\bmake\s+([a-zA-Z][a-zA-Z0-9-]*)")
CLI_REF = re.compile(r"\bapp\.cli\s+([a-zA-Z][a-zA-Z0-9-]*)")
SCRIPT_REF = re.compile(r"\bscripts/([A-Za-z0-9_.-]+\.py)")
MAKE_TARGET = re.compile(r"^([a-zA-Z][a-zA-Z0-9-]*):", re.M)
CLI_PARSER = re.compile(r"add_parser\(\s*[\"']([a-zA-Z][a-zA-Z0-9-]*)[\"']")
# fastcms 子命令全小写开头；要求首字符是小写字母，以免「统一 fastcms CLI」这类
# 说明性文字被误当成命令引用
FASTCMS_REF = re.compile(r"\bfastcms\s+([a-z][a-zA-Z0-9-]*)")
FASTCMS_CMD_KEY = re.compile(r"^\s*([\"'])([a-zA-Z][a-zA-Z0-9-]*)\1\s*:", re.M)

# ---- Phase 12-3：文件路径与模块路径的存在性 --------------------------------

# 反引号里的文件引用：必须带常见源码/配置扩展名，路径里不含空格
FILE_REF = re.compile(
    r"`([\w][\w/.-]*\.(?:py|md|json|toml|ya?ml|m?js|css))`"
)
# 反引号里的模块引用：``app.kernel.db``；以 .py/.js 等结尾的交给 FILE_REF（负向
# 环视要求定长，这里换成「匹配后按扩展名过滤」）
QUALIFIED_REF = re.compile(r"`(app\.[A-Za-z_][A-Za-z0-9_.]*)`")
_FILE_SUFFIXES = (".py", ".md", ".json", ".toml", ".yml", ".yaml", ".js", ".mjs", ".css")
# Markdown 相对链接：只校验指向 .md 的目标；外链/锚点/图片不做存在性校验。
MD_LINK_REF = re.compile(r"\[[^\]]*\]\((?P<target>[^)\s]+)")


def _is_file_ref(ref: str) -> bool:
    return ref.endswith(_FILE_SUFFIXES)

# 路径引用的解析根：文档里的引用习惯写「相对 backend/app」「相对 scripts」等
SEARCH_ROOTS = (
    ROOT,
    ROOT / "backend",
    ROOT / "backend" / "app",
    ROOT / "backend" / "app" / "modules",
    ROOT / "backend" / "app" / "modules" / "ops",
    ROOT / "backend" / "app" / "modules" / "sys",
    ROOT / "backend" / "app" / "web",
    ROOT / "backend" / "app" / "web" / "static" / "js",
    ROOT / "backend" / "app" / "web" / "static" / "js" / "pages",
    ROOT / "backend" / "app" / "web" / "static" / "css",
    ROOT / "backend" / "tests",
    ROOT / "backend" / "app" / "web" / "static",
    ROOT / "scripts",
    ROOT / "docs",
    ROOT / "docker",
)
# 裸文件名（不含目录）在全仓库的搜索范围
BARE_SEARCH_ROOTS = (ROOT / "backend", ROOT / "scripts", ROOT / "docs", ROOT / "docker")

_file_ref_cache: dict[str, bool] = {}


def file_ref_exists(ref: str) -> bool:
    """文件引用是否能解析到真实文件。

    带目录的引用按 SEARCH_ROOTS 逐根尝试；裸文件名（如 ``module.py``——每个
    叶子模块都有一个，写成全路径反而不真实）在整个 backend/scripts/docs 下
    找同名文件即可。结果缓存：同一引用在几十篇文档里重复出现。
    """
    if ref in _file_ref_cache:
        return _file_ref_cache[ref]
    found = False
    if "/" in ref:
        found = any((root / ref).exists() for root in SEARCH_ROOTS)
    else:
        for root in BARE_SEARCH_ROOTS:
            if any(root.rglob(ref)):
                found = True
                break
    _file_ref_cache[ref] = found
    return found


def qualified_ref_exists(ref: str) -> bool:
    """``app.x.y.z`` 是否对应真实的模块文件 / 包目录。

    纯文件系统解析（不 import，避免文档校验产生引擎创建等副作用）：
    ``app.kernel.db`` → ``backend/app/kernel/db.py``；
    ``app.web`` → ``backend/app/web/__init__.py`` 或同名目录。
    """
    if not ref.startswith("app."):
        return False
    rel = ref[len("app.") :].replace(".", "/")
    base = ROOT / "backend" / "app"
    return (
        (base / f"{rel}.py").exists()
        or (base / rel / "__init__.py").exists()
        or (base / rel).is_dir()
    )


def md_link_target(target: str) -> str | None:
    """把 Markdown 链接目标解析成相对 .md 路径；外链/锚点/图片返回 None。

    只处理 `[文字](相对路径.md)` 这类文件链接，跳过 URL、mailto、锚点与查询串，
    避免把外链或“跳到本页某节”误判成文件缺失。
    """
    raw = target.strip()
    if raw.startswith("<") and raw.endswith(">"):
        raw = raw[1:-1]
    if raw.startswith(("http://", "https://", "mailto:", "tel:", "//", "#")):
        return None
    # 去掉可选标题（"file.md \"title\""）与锚点/查询
    path_part = raw.split()[0].split("#", 1)[0].split("?", 1)[0]
    if not path_part.endswith(".md"):
        return None
    return path_part


def doc_files() -> list[Path]:
    return sorted(DOCS_DIR.glob("*.md")) + ([README] if README.exists() else [])


def make_targets() -> set[str]:
    """Makefile 中的真实目标：行首小写标识符后跟冒号。"""
    if not MAKEFILE.exists():
        return set()
    return set(MAKE_TARGET.findall(MAKEFILE.read_text(encoding="utf-8")))


def cli_commands() -> set[str]:
    """cli.py 中的真实子命令：add_parser("name")，含嵌套子命令。"""
    if not CLI_FILE.exists():
        return set()
    return set(CLI_PARSER.findall(CLI_FILE.read_text(encoding="utf-8")))


def fastcms_commands() -> set[str]:
    """fastcms.py 中的真实子命令：COMMANDS 字典的键。

    只读字典字面量里的键，不 import 脚本本身，避免依赖探测等重逻辑。
    """
    if not FASTCMS_FILE.exists():
        return set()
    text = FASTCMS_FILE.read_text(encoding="utf-8")
    match = re.search(r"^COMMANDS:.*?=\s*\{(?P<body>.*?)^\}", text, re.M | re.S)
    if not match:
        return set()
    return {m.group(2) for m in FASTCMS_CMD_KEY.finditer(match.group("body"))}


def scan() -> tuple[list[tuple[str, str, str]], dict[str, list[str]], dict[str, str], dict[str, str], dict[str, str], dict[str, str]]:
    """返回 (问题列表, 豁免命中位置, make 目标, cli 子命令, fastcms 子命令, 文件/模块引用)。"""
    targets = make_targets()
    commands = cli_commands()
    fc_commands = fastcms_commands()
    problems: list[tuple[str, str, str]] = []
    hits: dict[str, list[str]] = {}
    seen_make: dict[str, str] = {}
    seen_cli: dict[str, str] = {}
    seen_fastcms: dict[str, str] = {}
    seen_files: dict[str, str] = {}

    def _miss(where: str, ref: str, label: str, why: str) -> None:
        if ref in KNOWN_EXCEPTIONS:
            hits.setdefault(ref, []).append(where)
        else:
            problems.append((where, label, why))

    for path in doc_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            where = f"{path.relative_to(ROOT)}:{lineno}"
            for ref in MAKE_REF.findall(line):
                seen_make.setdefault(ref, where)
                if ref not in targets:
                    _miss(where, ref, f"make {ref}", "Makefile 中不存在该目标")
            for ref in CLI_REF.findall(line):
                seen_cli.setdefault(ref, where)
                if ref not in commands:
                    _miss(where, ref, f"app.cli {ref}", "cli.py 中不存在该子命令")
            for ref in FASTCMS_REF.findall(line):
                seen_fastcms.setdefault(ref, where)
                if ref not in fc_commands:
                    _miss(where, ref, f"fastcms {ref}", "fastcms.py 中不存在该子命令")
            for ref in SCRIPT_REF.findall(line):
                if not (ROOT / "scripts" / ref).exists():
                    _miss(where, ref, f"scripts/{ref}", "脚本文件不存在")
            for ref in FILE_REF.findall(line):
                seen_files.setdefault(ref, where)
                if not file_ref_exists(ref):
                    _miss(where, ref, ref, "文档引用的文件不存在")
            for ref in QUALIFIED_REF.findall(line):
                if _is_file_ref(ref):
                    continue  # ``app/web/router.py`` 这类按文件引用处理
                seen_files.setdefault(ref, where)
                if not qualified_ref_exists(ref):
                    _miss(where, ref, ref, "文档引用的模块不存在")
            for target in MD_LINK_REF.findall(line):
                rel = md_link_target(target)
                if rel is None:
                    continue
                if not (path.parent / rel).exists():
                    _miss(where, rel, rel, "文档链接指向不存在的文件")

    return problems, hits, seen_make, seen_cli, seen_fastcms, seen_files


def expired_entries(today: date | None = None) -> dict[str, KnownException]:
    """返回已超过到期时间的豁免（长期漂移不能静默通过）。"""
    today = today or date.today()
    return {name: entry for name, entry in KNOWN_EXCEPTIONS.items() if entry.expires < today}


def render(
    problems: list[tuple[str, str, str]],
    hits: dict[str, list[str]],
    seen_make: dict[str, str],
    seen_cli: dict[str, str],
    seen_fastcms: dict[str, str],
    expired: dict[str, KnownException],
    seen_files: dict[str, str] | None = None,
) -> str:
    lines: list[str] = []
    lines.append("文档命令校验")
    lines.append(f"  Makefile 目标：{len(make_targets())} 个")
    lines.append(f"  fastcms.py 子命令：{len(fastcms_commands())} 个")
    lines.append(f"  cli.py 子命令：{len(cli_commands())} 个")
    lines.append(
        f"  文档引用 make 目标：{len(seen_make)} 个，fastcms 子命令：{len(seen_fastcms)} 个，"
        f"cli 子命令：{len(seen_cli)} 个，文件/模块路径：{len(seen_files)} 个"
    )

    if KNOWN_EXCEPTIONS:
        lines.append("  白名单豁免（需逐条确认）：")
        for name, entry in KNOWN_EXCEPTIONS.items():
            lines.append(f"    - {name}：{entry.reason} [owner={entry.owner}, expires={entry.expires}]")

    if hits:
        lines.append("  ⚠ 白名单命中（CI 需人工复核，不应长期静默）：")
        for name, locations in hits.items():
            entry = KNOWN_EXCEPTIONS[name]
            lines.append(
                f"    - {name}（{len(locations)} 处）：{', '.join(locations)} —— {entry.reason}"
            )

    if expired:
        lines.append("  ⛔ 白名单已到期（必须复核后移除或续期）：")
        for name, entry in sorted(expired.items()):
            lines.append(f"    - {name}：到期 {entry.expires}，owner={entry.owner}，{entry.reason}")

    if not problems:
        lines.append("  结果：全部通过，文档中出现的命令都真实存在")
        return "\n".join(lines)

    lines.append("")
    lines.append(f"  结果：发现 {len(problems)} 处漂移")
    for where, cmd, why in problems:
        lines.append(f"    {where}  引用 `{cmd}` —— {why}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验文档中出现的命令是否真实存在")
    parser.add_argument("--list", action="store_true", help="打印解析到的命令集合后退出")
    parser.add_argument("--today", help="覆盖今日日期（YYYY-MM-DD），用于测试到期判定")
    args = parser.parse_args(argv)

    if args.list:
        print("Makefile 目标：", sorted(make_targets()))
        print("fastcms.py 子命令：", sorted(fastcms_commands()))
        print("cli.py 子命令：", sorted(cli_commands()))
        return 0

    today = date.fromisoformat(args.today) if args.today else None
    problems, hits, seen_make, seen_cli, seen_fastcms, seen_files = scan()
    expired = expired_entries(today)
    print(render(problems, hits, seen_make, seen_cli, seen_fastcms, expired, seen_files))    # 命中豁免只是警告（不判失败）；到期豁免与真实漂移一样判失败
    return 1 if problems or expired else 0


if __name__ == "__main__":
    sys.exit(main())
