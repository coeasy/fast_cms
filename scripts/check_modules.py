#!/usr/bin/env python3
"""模块清单完整性校验。

引入 ``app/core/module.py`` 的清单机制后，``main.py`` 不再显式 import 各模块路由，
而是遍历 ``app/modules/**/module.py``。这带来一个新的静默故障面：**漏写清单的
模块不会报错，只会悄悄没有路由**（页面 404、接口 404）。

本检查把这类漏配挡在门禁层：

1. ``app/modules`` 下每个叶子包都必须有 ``module.py``，且 ``MODULE`` 是 ModuleSpec；
2. 清单里的 ``name`` 必须与目录名一致（否则 ``order`` 排序与日志会误导）；
3. 清单声明的每个 router 必须真的出现在 OpenAPI 里（防止 order/重复注册导致丢失）；
4. 清单声明的权限码必须形如 ``<域>:<资源>:<动作>``，且不与基线表重复；
5. 底座分层：``core``/``kernel`` 不得依赖业务模块，模块间依赖必须在白名单内；
6. **底座方向：``core/`` 不得 import ``app.kernel``**（白名单 shim 除外）。

目录结构约定（一层域分组，可选）：

    app/modules/sys/system/module.py     # 域分组
    app/modules/content/module.py        # 顶层叶子

用法：
    python scripts/check_modules.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
MODULES_DIR = BACKEND / "app" / "modules"
sys.path.insert(0, str(BACKEND))

from app.bootstrap.permissions import PERMISSIONS  # noqa: E402
from app.core.module import ModuleSpecError, discover_modules  # noqa: E402

BASELINE_CODES = {item["code"] for item in PERMISSIONS}


def _is_package(path: Path) -> bool:
    return path.is_dir() and (path / "__init__.py").exists()


def iter_package_dirs() -> list[Path]:
    """列出参与清单约定的包目录（顶层叶子 + 一层域分组下的叶子）。"""
    found: list[Path] = []
    for top in sorted(MODULES_DIR.iterdir()):
        if not _is_package(top) or top.name.startswith("_"):
            continue
        children = [
            child
            for child in sorted(top.iterdir())
            if _is_package(child) and not child.name.startswith("_")
        ]
        if (top / "module.py").exists():
            found.append(top)
        elif children:
            found.extend(children)
    return found


# 允许存在的模块间依赖：值是「为什么必须存在」。
# 新增一条要写清理由——这份清单本身就是架构决策记录，不是豁免垃圾场。
ALLOWED_MODULE_EDGES: dict[tuple[str, str], str] = {
    ("auth", "config"): "登录/改密要读密码策略（password_min_length）",
    ("auth", "security"): "登录失败自动封禁与 API Key 服务账号前缀",
    ("auth", "system"): "登录要查用户、写登录日志、更新最后登录信息",
    ("dashboard", "content"): "工作台统计公告数量需要读公告表",
    ("dashboard", "schedule"): "工作台统计任务数量与最近执行日志",
    ("export", "audit"): "导出动作要写导出审计",
    ("export", "config"): "导出字典项数据（字典模型归属 config 模块）",
    ("export", "content"): "导出公告数据（公告模型归属 content 模块）",
    ("export", "schedule"): "导出任务执行日志（日志模型归属 schedule 模块）",
    ("monitor", "system"): "强制下线在线会话需要系统模块的 kick_session",
    ("system", "config"): "用户管理复用密码策略与系统参数",
    ("theme", "config"): "主题渲染读取公开配置键与系统参数",
}

# core / kernel 是被所有模块依赖的底座，不允许反向依赖业务模块
BASE_LAYERS = ("core", "kernel")

# 底座内部的方向是 **kernel → core 单向**：core 是无状态的地基（配置、错误、分页、
# 缓存、序列化），kernel 是在地基上加 ORM 的那一层（模型、鉴权、事件、文件、守卫）。
# core 一旦反向依赖 kernel，两者就又变成一团「都是底座」，方向无从校验。
#
# 下面是 Phase 8 搬家时留下的兼容 shim，精确到文件、必须写明理由；
# 下一轮调用点改完导入路径后应清空本表并删除对应文件。
CORE_KERNEL_SHIMS: dict[str, str] = {
    "app/core/authz.py": "鉴权上下文上移到 kernel/authz，38 个文件 142 处 Depends 待迁移",
    "app/core/events.py": "事件总线上移到 kernel/events，8 处 `from app.core import events` 待迁移",
    "app/core/ipguard.py": "IP 守卫上移到 kernel/ipguard，5 个调用点待迁移",
    "app/core/middleware.py": "请求上下文/操作日志中间件上移到 kernel/middleware，2 个调用点待迁移",
}


def _imported_modules(path: Path) -> list[str]:
    """用 AST 取真实的 ``from app.modules.x.y import ...`` 目标（不会被 docstring 示例误伤）。"""
    import ast

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app.modules."):
            found.append(node.module)
    return found


def _function_scoped_module_imports(path: Path) -> list[tuple[str, int]]:
    """找出写在函数体里的跨模块 import（循环依赖的典型遮蔽手法）。

    模块顶层 import 会立刻暴露循环依赖，写进函数体则要等调用时才炸——
    这类代码通常意味着依赖方向反了（调用方不该认识被调用方），应当改成
    被依赖方注册回调、调用方只发事件。这里把它挡在门禁层。

    返回 ``(目标模块, 行号)`` 列表；只统计 ``app.modules.*`` 的绝对导入。
    """
    import ast

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    hits: list[tuple[str, int]] = []

    def scan(nodes: list[ast.stmt]) -> None:
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for inner in ast.walk(node):
                    if (
                        isinstance(inner, ast.ImportFrom)
                        and inner.module
                        and inner.module.startswith("app.modules.")
                    ):
                        hits.append((inner.module, inner.lineno))
                # 嵌套函数已由 ast.walk 覆盖，不再递归
            elif isinstance(node, (ast.ClassDef, ast.If, ast.Try, ast.With, ast.For, ast.While)):
                scan(node.body)

    scan(tree.body)
    return hits


def check_layering(problems: list[str]) -> None:
    """分层守卫：底座不得依赖模块，模块间依赖必须在白名单内。"""
    app_dir = BACKEND / "app"
    for layer in BASE_LAYERS:
        for py in (app_dir / layer).rglob("*.py"):
            for imported in _imported_modules(py):
                rel = py.relative_to(app_dir).as_posix()
                problems.append(
                    f"{rel}: {layer} 层不得依赖业务模块（{imported}），需要共享就下沉到 app/kernel"
                )

    # 模块间依赖
    owners: dict[str, str] = {}
    for manifest in MODULES_DIR.rglob("module.py"):
        pkg = "app.modules." + ".".join(manifest.parent.relative_to(MODULES_DIR).parts)
        owners[pkg] = manifest.parent.name

    def owner_of(imported: str) -> str | None:
        best: tuple[str, str] | None = None
        for pkg, name in owners.items():
            if (imported == pkg or imported.startswith(pkg + ".")) and (
                best is None or len(pkg) > len(best[0])
            ):
                best = (pkg, name)
        return best[1] if best else None

    # 记录真实存在的依赖边，用于白名单的「反向校验」：
    # 登记了但已不存在的边（幽灵条目）说明架构已漂移，白名单必须同步删除。
    real_edges: set[tuple[str, str]] = set()

    for py in MODULES_DIR.rglob("*.py"):
        me = owner_of("app.modules." + ".".join(py.relative_to(MODULES_DIR).parts[:-1]))
        if me is None:
            continue
        for imported in _imported_modules(py):
            other = owner_of(imported)
            if other is None or other == me:
                continue
            real_edges.add((me, other))
            if (me, other) not in ALLOWED_MODULE_EDGES:
                rel = py.relative_to(MODULES_DIR).as_posix()
                problems.append(
                    f"{rel}: 新增了未登记的模块依赖 {me} -> {other}；"
                    f"优先下沉到 app/kernel，确有必要再登记到 ALLOWED_MODULE_EDGES 并写明理由"
                )
        # 函数体内的跨模块 import：即便目标在白名单里也一律禁止，
        # 它遮蔽的是循环依赖，而不是表达真实依赖。
        for target, lineno in _function_scoped_module_imports(py):
            other = owner_of(target)
            if other is None or other == me:
                continue
            rel = py.relative_to(MODULES_DIR).as_posix()
            problems.append(
                f"{rel}:{lineno}: 函数体内 import 了其它模块（{me} -> {other}，{target}）；"
                f"这是循环依赖的遮蔽手法，请改成事件/回调或把共享逻辑下沉到 app/kernel"
            )

    # 反向校验：白名单里每条边必须有真实 import 支撑，否则是「登记了但已不存在的
    # 幽灵条目」。单向校验只能发现「新增未登记」，发现不了「已消灭未同步」——
    # 架构漂移就是这么积累起来的（事件总线重构后就留下了 2 条幽灵边）。
    for edge, reason in ALLOWED_MODULE_EDGES.items():
        if edge not in real_edges:
            problems.append(
                f"白名单登记了但实际不存在的模块依赖 {edge[0]} -> {edge[1]}（{reason}）；"
                f"该边已被消灭，请从 ALLOWED_MODULE_EDGES 中删除"
            )


def _imported_names(path: Path) -> list[tuple[str, int]]:
    """用 AST 取文件里所有绝对导入的目标模块名（含 ``import x`` 形式）。

    ``_imported_modules`` 只关心 ``app.modules.*``；这里要判「core 有没有 import
    app.kernel」，所以连 ``import app.kernel.foo`` 这种写法也要覆盖，否则改个写法
    就能绕过门禁。相对导入（``from ..kernel import x``）同样解析成绝对名。
    """
    import ast

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    pkg_parts = path.relative_to(BACKEND).as_posix()[: -len(".py")].split("/")
    # 当前文件所在包：app/core/authz.py -> app.core
    package = pkg_parts[:-1]

    hits: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)] if node.level > 1 else package
                target = ".".join([*base, *(node.module.split(".") if node.module else [])])
            else:
                target = node.module or ""
            if target:
                hits.append((target, node.lineno))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                hits.append((alias.name, node.lineno))
    return hits


def check_core_direction(problems: list[str]) -> None:
    """底座方向守卫：``core/`` 不得 import ``app.kernel``（白名单 shim 除外）。

    这条规则把「kernel → core 单向」从一句文档约定变成可执行断言。此前
    ``core/authz.py`` 为了查 ``User`` 表 import 了 ``app.kernel.models``，同时
    ``kernel/db.py`` 又 import 了 ``core/authz``——两个方向同时存在，等于没有方向，
    任何人新增跨层引用都无法判断对错。
    """
    app_dir = BACKEND / "app"
    core_dir = app_dir / "core"

    seen: set[str] = set()
    for py in core_dir.rglob("*.py"):
        rel = py.relative_to(BACKEND).as_posix()
        for target, lineno in _imported_names(py):
            if target != "app.kernel" and not target.startswith("app.kernel."):
                continue
            if rel in CORE_KERNEL_SHIMS:
                seen.add(rel)
                continue
            problems.append(
                f"{rel}:{lineno}: core 层不得依赖 app.kernel（{target}）；"
                f"kernel 才是「core + ORM」的那一层，方向只能是 kernel -> core。"
                f"需要共享的纯逻辑请下沉到 app/core，确需过渡请登记到 CORE_KERNEL_SHIMS 并写明理由"
            )

    # 反向校验：登记了 shim 但文件已不再 import kernel（或已删除）说明该条目是幽灵，
    # 留着会让白名单慢慢变成豁免垃圾场。
    for rel, reason in CORE_KERNEL_SHIMS.items():
        if not (BACKEND / rel).exists():
            problems.append(f"CORE_KERNEL_SHIMS 登记的 {rel} 已不存在，请从清单删除（{reason}）")
        elif rel not in seen:
            problems.append(
                f"CORE_KERNEL_SHIMS 登记的 {rel} 已不再 import app.kernel（{reason}）；"
                f"该 shim 已被消灭，请删除文件并同步清理清单"
            )


def check_commit_boundary(problems: list[str]) -> None:
    """事务边界守卫：``app/modules`` 下不得直接调用会话的 ``commit()``。

    裸 ``await db.commit()`` 有两个问题，且都只在生产上暴露：

    1. 唯一约束冲突会以原始 ``IntegrityError`` 冒到全局处理器，退化成一个没有
       上下文的 500；走 ``kernel.db.commit`` 才会翻译成语义化的 409。
    2. 提交时机散落在业务代码里，「哪些写属于同一个事务」只能靠读代码推断。

    允许 ``savepoint.commit()``——那是释放 SAVEPOINT，不是提交事务，批量导入的
    行级原子性正是靠它实现的。判定按接收者名字来，见 ``_SESSION_RECEIVERS``。
    """
    import ast

    for py in MODULES_DIR.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "commit":
                continue
            receiver = ast.unparse(node.func.value)
            if receiver not in _SESSION_RECEIVERS:
                continue
            rel = py.relative_to(MODULES_DIR).as_posix()
            problems.append(
                f"{rel}:{node.lineno}: 不得直接 {receiver}.commit()；"
                f"请改用 kernel.db.commit({receiver})（冲突语义统一）"
                f"或 @transactional（把提交时机交给内核）"
            )


# 会被当成「数据库会话」的变量名。写成显式集合而不是「任何 .commit()」：
# savepoint.commit() 释放的是 SAVEPOINT，属于合法的行级事务控制。
_SESSION_RECEIVERS = {"db", "session", "sess", "self.db", "self.session"}


def check_model_collection(problems: list[str]) -> None:
    """每个模块的 ``models.py`` 都必须被 ``db/models.py`` 自动收集进 ``Base.metadata``。

    此前 ``db/models.py`` 是手工 re-export：新模块加了模型但忘了登记，会**静默失效**
    （``alembic revision --autogenerate`` 看不到新表，``test_schema_drift`` 要到运行期才发现）。
    改成文件系统自动发现后，本检查保证发现逻辑没有漏掉任何 ``models.py``，
    并把「漏改即静默失效」变成「漏改即门禁失败」。
    """
    import importlib

    from app.db.base import Base
    from app.db.models import iter_module_model_import_paths

    # 磁盘上真实存在的模块模型文件（相对 modules 根的路径）
    on_disk = {p.relative_to(MODULES_DIR).as_posix() for p in MODULES_DIR.rglob("models.py")}
    # 自动收集器声称会导入的（相对 modules 根的路径）
    discovered = {rel for _imp, rel in iter_module_model_import_paths()}

    for rel in sorted(on_disk - discovered):
        problems.append(f"模块模型文件未被 db/models.py 自动收集：{rel}；检查 iter_module_model_import_paths 的发现逻辑")

    for import_path, rel in iter_module_model_import_paths():
        mod = importlib.import_module(import_path)
        for _name, obj in vars(mod).items():
            tablename = getattr(obj, "__tablename__", None)
            if isinstance(obj, type) and tablename and tablename not in Base.metadata.tables:
                problems.append(
                    f"{rel}: 表 {tablename} 未注册进 Base.metadata；db/models.py 自动收集失效"
                )


def check_event_publishers(problems: list[str]) -> None:
    """发布的事件名必须是 ``KNOWN_EVENTS`` 里的登记项。

    事件总线是「按名字」连接的：`publish("notice.publish")` 少个 ed 不会报错，
    只会安静地没有订阅者。运行期不好为拼写错误抛异常（发布方多在请求路径上），
    所以在门禁层用 AST 把字符串字面量对一遍。
    """
    import ast

    from app.core.events import KNOWN_EVENTS

    for py in (BACKEND / "app").rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "publish"
                and isinstance(func.value, ast.Name)
                and func.value.id == "events"
            ):
                continue
            if not node.args:
                continue
            first = node.args[0]
            if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                continue
            if first.value not in KNOWN_EVENTS:
                rel = py.relative_to(BACKEND).as_posix()
                problems.append(
                    f"{rel}:{node.lineno}: 发布了未登记的事件 {first.value!r}，"
                    f"请先补进 app/core/events.py 的 KNOWN_EVENTS 并注册订阅方"
                )


def main() -> int:
    problems: list[str] = []

    for package_dir in iter_package_dirs():
        if not (package_dir / "module.py").exists():
            rel = package_dir.relative_to(MODULES_DIR).as_posix()
            problems.append(f"{rel}: 缺少 module.py（main.py 不会注册它的路由）")

    try:
        specs = discover_modules()
    except ModuleSpecError as exc:
        print(f"模块清单校验失败：{exc}")
        return 1

    by_name = {spec.name: spec for spec in specs}

    for package_dir in iter_package_dirs():
        leaf = package_dir.name
        spec = by_name.get(leaf)
        if spec is not None and spec.name != leaf:
            problems.append(f"{leaf}: 清单 name={spec.name!r} 与目录名不一致")

    for spec in specs:
        for item in spec.permissions:
            code = item["code"]
            if code.count(":") < 2:
                problems.append(f"{spec.name}: 权限码 {code} 段数不足，应为 <域>:<资源>:<动作>")
            if code in BASELINE_CODES:
                problems.append(f"{spec.name}: 权限码 {code} 与基线表重复，请从 module.py 移除")

    check_layering(problems)
    check_core_direction(problems)
    check_commit_boundary(problems)
    check_model_collection(problems)

    # 事件清单里的每个事件都必须有订阅方：发布出去没人接等于静默丢弃。
    # 订阅关系在模块清单加载时建立，所以这一步必须放在 discover_modules() 之后。
    from app.core.events import KNOWN_EVENTS, subscribers

    for event in sorted(KNOWN_EVENTS):
        if not subscribers(event):
            problems.append(
                f"事件 {event} 没有订阅方：发布出去会被静默丢弃（订阅方在 module.py 里 subscribe）"
            )

    check_event_publishers(problems)

    # 清单声明的页面/菜单是否真的被注册表消费（避免「声明了但没人读」的死字段）
    from app.bootstrap.menus import all_menu_tree, iter_menus
    from app.web.router import all_pages

    page_paths = {page["path"] for page in all_pages()}
    menu_paths = {node["path"] for node, _parent in iter_menus(all_menu_tree())}
    for spec in specs:
        for page in spec.pages:
            path = page.get("path")
            if path and path not in page_paths:
                problems.append(f"{spec.name}: 声明的页面 {path} 未被 all_pages() 合并")
        for menu in spec.menus:
            path = menu.get("path")
            if path and path not in menu_paths:
                problems.append(f"{spec.name}: 声明的菜单 {path} 未被 all_menu_tree() 合并")

    # 清单声明的 router 是否真的挂上了 app。
    # 不遍历 app.routes：不同 FastAPI 版本对 include_router 的表示不同
    # （复制 route / _IncludedRouter 包装），直接比对 OpenAPI 里的最终路径最稳。
    from app.core.config import api_path
    from app.main import create_app

    documented = set(create_app().openapi().get("paths", {}))

    def _normalize(path: str) -> str:
        # OpenAPI 把 {ip:path} 这类 Starlette 转换器渲染成 {ip}
        return re.sub(r"\{(\w+):\w+\}", r"{\1}", path)

    for spec in specs:
        for index, router in enumerate(spec.routers):
            missing = [
                route.path
                for route in router.routes
                if _normalize(api_path(route.path)) not in documented
            ]
            if missing:
                problems.append(
                    f"{spec.name}: routers[{index}] 有 {len(missing)} 条路由未出现在 OpenAPI，"
                    f"例如 {missing[0]}"
                )

    if problems:
        print(f"模块清单校验失败（{len(problems)} 处）：")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    groups = sorted({d.parent.name for d in iter_package_dirs() if d.parent != MODULES_DIR})
    total_routers = sum(len(spec.routers) for spec in specs)
    total_perms = sum(len(spec.permissions) for spec in specs)
    group_desc = f"域分组 {len(groups)} 个（{', '.join(groups)}）" if groups else "无域分组"
    print(
        f"OK: {len(specs)} 个模块清单，{group_desc}，{total_routers} 个路由已注册，"
        f"{total_perms} 条增量权限码，基线 {len(BASELINE_CODES)} 条。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
