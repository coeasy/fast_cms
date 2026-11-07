"""依赖清单一致性校验。

`backend/requirements.txt` 是「无虚拟环境快速部署」的入口，`pyproject.toml` 是
`pip install -e backend` 的入口。两份清单一旦漂移，就会出现「venv 装得起来、
requirements 装不起来」或者反过来的诡异问题，而且通常要到部署当天才发现。

这里把三件事钉死：

1. requirements.txt 与 pyproject 的运行时依赖逐项相同（名字 + 版本约束）；
2. requirements-dev.txt 覆盖 pyproject 的 dev 可选依赖；
3. 环境探测列表覆盖代码里真实 import 到的所有第三方顶层模块——
   漏一个就会导致「探测说装好了，运行时却 ImportError」。
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
APP_DIR = BACKEND / "app"
REQUIREMENTS = BACKEND / "requirements.txt"
REQUIREMENTS_DEV = BACKEND / "requirements-dev.txt"
PYPROJECT = BACKEND / "pyproject.toml"

# 由其它依赖间接引入，不需要在探测列表里单列
TRANSITIVE_MODULES = frozenset({"starlette", "anyio", "greenlet", "typing_extensions"})
# 标准库与项目自身
NON_THIRD_PARTY = frozenset({"app"})

REQUIREMENT_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"\s*(?P<extras>\[[^\]]*\])?"
    r"\s*(?P<spec>[^;#]*?)"
    r"\s*(?:;(?P<marker>.*))?$"
)


def normalize(name: str) -> str:
    """按 PEP 503 归一化：Fast_CMS / fast-cms / fast.cms 视为同一个包。"""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def parse_requirement_line(line: str) -> tuple[str, str] | None:
    text = line.split(" #", 1)[0].strip()
    if not text or text.startswith(("#", "-")):
        return None
    match = REQUIREMENT_RE.match(text)
    if match is None:
        raise AssertionError(f"无法解析依赖声明：{line!r}")
    spec = match.group("spec").replace(" ", "")
    return normalize(match.group("name")), spec


def parse_requirements_file(path: Path) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        item = parse_requirement_line(line)
        if item is not None:
            parsed[item[0]] = item[1]
    return parsed


def pyproject_dependencies(group: str = "dependencies") -> dict[str, str]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    project = data["project"]
    raw = project["dependencies"] if group == "dependencies" else project["optional-dependencies"][group]
    parsed: dict[str, str] = {}
    for spec in raw:
        item = parse_requirement_line(spec)
        assert item is not None, spec
        parsed[item[0]] = item[1]
    return parsed


def probe_module_names() -> set[str]:
    """从 scripts/env_check.py 读取探测列表，避免测试里再抄一份。"""
    sys.path.insert(0, str(BACKEND.parent / "scripts"))
    try:
        import env_check

        return set(env_check.RUNTIME_MODULES) | set(env_check.OPTIONAL_MODULES)
    finally:
        sys.path.pop(0)


def third_party_imports() -> set[str]:
    found: set[str] = set()
    for path in APP_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return {
        name
        for name in found
        if name not in sys.stdlib_module_names and name not in NON_THIRD_PARTY
    }


def test_runtime_requirements_exist() -> None:
    assert REQUIREMENTS.exists(), "缺少 backend/requirements.txt（无虚拟环境部署的入口）"
    assert REQUIREMENTS_DEV.exists(), "缺少 backend/requirements-dev.txt"


def test_runtime_requirements_match_pyproject() -> None:
    declared = pyproject_dependencies()
    listed = parse_requirements_file(REQUIREMENTS)
    assert set(listed) == set(declared), (
        f"requirements.txt 与 pyproject.toml 不一致；"
        f"只在 pyproject：{sorted(set(declared) - set(listed))}；"
        f"只在 requirements：{sorted(set(listed) - set(declared))}"
    )
    for name, spec in declared.items():
        assert listed[name] == spec, f"{name} 版本约束不一致：pyproject={spec!r} requirements={listed[name]!r}"


def test_dev_requirements_cover_pyproject_dev_extra() -> None:
    text = REQUIREMENTS_DEV.read_text(encoding="utf-8")
    assert "-r requirements.txt" in text, "requirements-dev.txt 必须继承 requirements.txt"

    declared = pyproject_dependencies("dev")
    listed = parse_requirements_file(REQUIREMENTS_DEV)
    missing = set(declared) - set(listed)
    assert not missing, f"requirements-dev.txt 缺少 pyproject dev 依赖：{sorted(missing)}"


def test_probe_covers_every_third_party_import() -> None:
    covered = probe_module_names() | TRANSITIVE_MODULES
    missing = third_party_imports() - covered
    assert not missing, (
        f"这些第三方模块被代码 import 了，但不在 env_check 的探测列表里：{sorted(missing)}；"
        "漏检会导致「依赖探测通过、运行时 ImportError」"
    )


def test_release_package_ships_requirements() -> None:
    """发布包缺 requirements.txt，目标机上 `install.py --deps-only` 会直接失败。"""
    source = (BACKEND.parent / "scripts" / "release.py").read_text(encoding="utf-8")
    for name in ("backend/requirements.txt", "backend/requirements-dev.txt"):
        assert f'"{name}"' in source, f"发布清单缺少 {name}"
