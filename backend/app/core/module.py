"""模块清单（ModuleSpec）与自动发现。

引入清单前，新增一个模块要同时改四处：

1. ``app/main.py`` 的 router 导入与 include 列表
2. ``app/bootstrap/permissions.py`` 的权限码表
3. ``app/bootstrap/menus.py`` 的菜单树
4. ``app/web/router.py`` 的页面注册表

任何一处漏改都是静默故障：路由 404、权限码校验失败、菜单不出现。

现在每个模块在自己的 ``module.py`` 里声明能力，``main.py`` 遍历发现并注册：

    # app/modules/crm/module.py
    from app.core.module import ModuleSpec
    from app.modules.crm.router import router

    MODULE = ModuleSpec(
        name="crm",
        order=200,
        routers=(router,),
        permissions=(
            {"code": "crm:customer:list", "name": "查看客户", "module": "crm"},
        ),
    )

设计取舍（刻意保留的集中点）：

- **路由**由清单发现，顺序按 ``order`` 再按名字，保证注册确定性。
- **权限码**：存量 97 条留在 ``bootstrap/permissions.py`` 基线表。它们是既有对外
  契约，逐条搬进各模块只会制造 review 噪音；清单里的 ``permissions`` 是**增量**，
  供新模块声明自己的码。种子合并两者。
- **菜单**与**页面**不强制拆到模块：菜单树是「信息架构」而不是「代码归属」，
  「系统管理」一个目录就横跨 system / config / theme 三个模块，硬拆会让菜单
  碎片化。清单支持声明，但存量仍走集中表，新模块可自行声明。
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import app.codegen
import app.modules


class ModuleSpecError(RuntimeError):
    """模块清单缺失或声明非法。"""


# 页面条目必填字段：缺任何一个，「声明了但没渲染出来」都会静默发生。
PAGE_REQUIRED_KEYS = frozenset({"path", "template", "title", "page"})

# 页面条目允许的全部字段，与 web/router.py 的 PAGES 条目等价（A-1）。
# 此前清单通道只「能写」path/template/title/page，声明 public / subtitle 等字段
# 虽然 dict 能装下，但没有校验——拼错一个键（如 subttile）副标题就静默丢失。
# 这里显式列出等价字段集，把「能装下」变成「有契约」。
PAGE_ALLOWED_KEYS = PAGE_REQUIRED_KEYS | frozenset(
    {"subtitle", "public", "status", "code", "message", "detail", "permission"}
)


@dataclass(frozen=True, slots=True)
class ModuleSpec:
    """一个业务模块对外声明的能力集合。"""

    name: str
    # 注册顺序：数值小的先注册。沿用引入清单前的显式顺序，避免路由表无谓漂移。
    order: int = 100
    description: str = ""
    # API 路由（FastAPI APIRouter），按声明顺序 include
    routers: tuple[Any, ...] = ()
    # 增量权限码：{"code": ..., "name": ..., "module": ...}
    permissions: tuple[dict[str, str], ...] = ()
    # 增量菜单节点（可选）
    menus: tuple[dict[str, Any], ...] = ()
    # 增量页面注册（可选）：字段与 web/router.py 的 PAGES 条目等价，见 PAGE_ALLOWED_KEYS
    pages: tuple[dict[str, Any], ...] = ()
    # 操作日志写入归属（B-1 / Phase 1-1）："middleware"=由 OperationLogMiddleware 兜底写
    # （默认，模块无需关心）；"service"=模块 service 层自己调 log_operation 写，
    # 中间件据此跳过该模块前缀，避免双写。
    audit: Literal["middleware", "service"] = "middleware"
    # 便于诊断：模块源码目录
    extra: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.name or not self.name.isidentifier():
            raise ModuleSpecError(f"模块名非法：{self.name!r}（需为合法标识符）")
        for item in self.permissions:
            code = item.get("code", "")
            if not code or code.count(":") < 1:
                raise ModuleSpecError(f"{self.name}: 权限码格式非法 {code!r}，应为 <域>:<资源>:<动作>")
            if not item.get("name"):
                raise ModuleSpecError(f"{self.name}: 权限码 {code} 缺少中文名")
        for router in self.routers:
            if not hasattr(router, "routes"):
                raise ModuleSpecError(f"{self.name}: routers 里的对象不像 APIRouter：{router!r}")
        for item in self.pages:
            if not isinstance(item, dict):
                raise ModuleSpecError(f"{self.name}: pages 条目必须是 dict，得到 {item!r}")
            missing = PAGE_REQUIRED_KEYS - item.keys()
            if missing:
                raise ModuleSpecError(f"{self.name}: 页面声明缺少字段 {sorted(missing)}：{item}")
            unknown = set(item) - PAGE_ALLOWED_KEYS
            if unknown:
                raise ModuleSpecError(
                    f"{self.name}: 页面声明含未知字段 {sorted(unknown)}"
                    f"（允许：{sorted(PAGE_ALLOWED_KEYS)}）；"
                    f"拼写错误会让 subtitle/public 等字段静默失效"
                )


def _iter_module_packages() -> list[tuple[str, str]]:
    """列出所有带 ``module.py`` 的清单，返回 ``(包名, 导入路径)``。

    扫描两处：
    - ``app.modules`` 下的子包（支持域分组的一层嵌套），包名如 ``sys.system`` /
      ``ops.audit`` / ``content``；
    - ``app.codegen``（平台能力，A-2 / Phase 2-1 收编进清单）。

    下划线开头的内部包跳过。
    """
    results: list[tuple[str, str]] = []
    root = Path(app.modules.__file__).parent
    for manifest in sorted(root.rglob("module.py")):
        rel = manifest.parent.relative_to(root)
        if any(part.startswith("_") for part in rel.parts):
            continue
        package = ".".join(rel.parts)
        results.append((package, f"app.modules.{package}.module"))
    codegen_manifest = Path(app.codegen.__file__).parent / "module.py"
    if codegen_manifest.exists():
        results.append(("codegen", "app.codegen.module"))
    return results


@lru_cache(maxsize=1)
def discover_modules() -> tuple[ModuleSpec, ...]:
    """导入并返回所有模块清单，按 ``order`` + ``name`` 排序。

    没有 ``module.py`` 的子包不会出现在这里：运行期不因为一个漏写而启动失败，
    由 ``scripts/check_modules.py`` 在门禁层提示。
    """
    specs: list[ModuleSpec] = []
    for package, import_path in _iter_module_packages():
        manifest = importlib.import_module(import_path)
        spec = getattr(manifest, "MODULE", None)
        if spec is None:
            raise ModuleSpecError(f"app/modules/{package}/module.py 缺少 MODULE 声明")
        if not isinstance(spec, ModuleSpec):
            raise ModuleSpecError(f"app/modules/{package}/module.py 的 MODULE 不是 ModuleSpec")
        spec.validate()
        specs.append(spec)

    names = [spec.name for spec in specs]
    duplicated = {name for name in names if names.count(name) > 1}
    if duplicated:
        raise ModuleSpecError(f"模块名重复：{sorted(duplicated)}")
    return tuple(sorted(specs, key=lambda spec: (spec.order, spec.name)))


def module_routers() -> list[Any]:
    """按注册顺序展开所有模块的路由。"""
    return [router for spec in discover_modules() for router in spec.routers]


def module_permissions() -> list[dict[str, str]]:
    """收集模块声明的增量权限码。"""
    return [item for spec in discover_modules() for item in spec.permissions]


def module_menus() -> list[dict[str, Any]]:
    return [item for spec in discover_modules() for item in spec.menus]


def module_pages() -> list[dict[str, Any]]:
    return [item for spec in discover_modules() for item in spec.pages]


def module_audit_modes() -> dict[str, str]:
    """模块名 -> 操作日志写入归属（"middleware" | "service"）。

    供 ``OperationLogMiddleware`` 决定是否跳过该模块前缀：声明了 ``audit="service"``
    的模块由 service 层自写操作日志，中间件不再兜底（B-1 单写修复）。
    """
    return {spec.name: spec.audit for spec in discover_modules()}
