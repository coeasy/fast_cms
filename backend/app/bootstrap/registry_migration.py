"""注册表存量条目的「清单化」分组（A-1 双轨收敛路径）。

权限码、页面、菜单目前是「存量集中 + 增量走清单」双轨制：存量留在
``bootstrap/permissions.py`` / ``web/router.py`` 的 ``PAGES`` / ``bootstrap/menus.py``，
新模块在自己的 ``module.py`` 里声明。双轨不是错，但缺一条**存量→清单**的收敛路径：
新人拿到一份增量语法，却不知道存量能不能、以及怎么搬进清单。

本模块把存量条目按模块分组，导出成 ``module.py`` 里 ``permissions=(...)`` /
``pages=(...)`` / ``menus=(...)`` 的**候选片段**。``fastcms migrate-registry``
干跑打印这些片段，人工确认后落地——本模块只做纯分组，不写任何文件。
"""

from __future__ import annotations

from typing import Any


def _page_module(page: dict[str, Any]) -> str:
    """页面归属模块：``page`` 形如 ``system/user``，取第一段。"""
    raw = str(page.get("page", ""))
    return raw.split("/")[0] or "?"


def _menu_module(node: dict[str, Any]) -> str:
    """菜单归属模块：优先 ``component_key``（形如 ``system/user``），目录节点
    没有 ``component_key`` 时回退到 ``path`` 首段（形如 ``/system``）。"""
    raw = str(node.get("component_key", "")) or str(node.get("path", ""))
    raw = raw.strip("/")
    return raw.split("/")[0] or "?"


def build_registry_candidates() -> dict[str, dict[str, list[dict[str, Any]]]]:
    """把三张注册表的存量条目按模块分组。

    返回 ``{模块名: {"permissions": [...], "pages": [...], "menus": [...]}}``。
    分组键取自各注册表自身的归属字段：权限用 ``module``，页面用 ``page``
    首段，菜单用 ``component_key`` 首段。菜单是信息架构（一个目录横跨多模块），
    分组结果仅供人工参考，不保证与模块清单一一对应。
    """
    from app.bootstrap.menus import MENU_TREE, iter_menus
    from app.bootstrap.permissions import PERMISSIONS
    from app.web.router import PAGES

    candidates: dict[str, dict[str, list[dict[str, Any]]]] = {}

    def bucket(module: str) -> dict[str, list[dict[str, Any]]]:
        return candidates.setdefault(module, {"permissions": [], "pages": [], "menus": []})

    for perm in PERMISSIONS:
        bucket(perm["module"])["permissions"].append(dict(perm))

    for page in PAGES:
        bucket(_page_module(page))["pages"].append(dict(page))

    for node, _parent in iter_menus(MENU_TREE):
        bucket(_menu_module(node))["menus"].append(dict(node))

    return candidates
