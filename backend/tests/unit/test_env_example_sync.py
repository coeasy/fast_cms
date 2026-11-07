"""配置面一致性回归测试。

两处历史上反复漂移的地方：

1. ``Settings`` 的字段与 ``.env.example`` 的键集合必须一致。
   缺一项，使用者就不知道这个开关存在；多一项，示例文件里会写一个不生效的配置。
2. 脚本侧的归一化实现（``scripts/config_probe.py`` 的回退路径）必须与
   后端 ``Settings`` 的校验器给出同一答案。不一致的后果是「脚本算出 /admin/、
   后端挂到别处」这类只有上线后才暴露的问题。

这两项都可以在本地一条命令内验证，不需要起服务或连数据库。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
SCRIPTS = ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS))


def _settings_fields() -> set[str]:
    """Settings 字段名统一成大写，便于与 .env 键对照。"""
    from app.core.config import Settings

    return {field.upper() for field in Settings.model_fields}


def _env_example_keys(path: Path) -> tuple[set[str], set[str]]:
    """返回 (生效键, 注释键)。注释里的键视为已文档化。"""
    text = path.read_text(encoding="utf-8")
    active = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", text, re.M))
    commented = set(re.findall(r"^#\s*([A-Z][A-Z0-9_]*)=", text, re.M))
    return active, commented


def test_env_example_covers_every_settings_field() -> None:
    """新增 Settings 字段必须同步进 .env.example，否则使用者不知道它存在。"""
    fields = _settings_fields()
    active, commented = _env_example_keys(ROOT / ".env.example")
    documented = active | commented
    missing = sorted(fields - documented)
    assert not missing, (
        f"这些 Settings 字段没有出现在 .env.example 里（注释掉也算）：{missing}；"
        "不文档化等于不存在的配置"
    )


def test_env_example_has_no_unknown_key() -> None:
    """.env.example 里写的每个键都必须真的对应一个 Settings 字段。"""
    fields = _settings_fields()
    active, commented = _env_example_keys(ROOT / ".env.example")
    unknown = sorted((active | commented) - fields)
    assert not unknown, (
        f".env.example 里有这些键，但 Settings 中不存在（改了不生效）：{unknown}"
    )


def test_core_section_is_small() -> None:
    """.env.example 的生效项应当很少，避免第一次部署被几十个配置吓到。

    服务端一键部署只关心少数几个值；其余都应注释掉（值即默认值）。
    """
    active, _ = _env_example_keys(ROOT / ".env.example")
    assert len(active) <= 12, (
        f"生效配置项有 {len(active)} 个，太多了：{sorted(active)}；"
        "请只保留部署必须决定的项，其余注释掉"
    )


def test_config_probe_matches_settings() -> None:
    """脚本侧回退归一化必须与 Settings 完全一致（P0-4 的核心验收点）。"""
    import config_probe

    if not config_probe.using_settings():
        pytest.skip("后端依赖未装，Settings 不可用")
    result = config_probe.self_test()
    assert result is not None, "config_probe 无法比对（Settings 导入失败）"
    assert not result, "脚本侧回退实现与 Settings 不一致：\n  " + "\n  ".join(result)


def test_admin_base_without_leading_slash() -> None:
    """P0-4 明确要求的场景：ADMIN_BASE=admin（漏了斜杠）脚本与后端必须算出同一挂载路径。"""
    import os

    import config_probe

    if not config_probe.using_settings():
        pytest.skip("后端依赖未装，Settings 不可用")

    old = os.environ.get("ADMIN_BASE")
    os.environ["ADMIN_BASE"] = "admin"
    try:
        config_probe._settings.cache_clear()
        mount = config_probe.resolve_site_base("ADMIN_BASE", config_probe.DEFAULT_ADMIN_BASE)
        assert mount == "/admin", f"漏斜杠时脚本归一化成 {mount!r}，期望 '/admin'"
    finally:
        if old is None:
            os.environ.pop("ADMIN_BASE", None)
        else:
            os.environ["ADMIN_BASE"] = old
        config_probe._settings.cache_clear()
