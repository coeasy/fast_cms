"""数据库模型收集面（单一入口，供 Alembic 与运行期使用）。

不再手工 re-export 每个模块的模型——那会引入「新模块忘登记即静默失效」：
``alembic revision --autogenerate`` 看不到新表，``test_schema_drift`` 也要到运行期才发现。
改为自动发现：先导入平台模型（``app.kernel.models`` 包 re-export + bootstrap），
再扫描 ``app.modules`` 下所有 ``models.py`` 并逐个导入，让它们的表注册进 ``Base.metadata``。
若某个模块的 ``models.py`` 导入失败（语法 / 依赖错误），会在此处立即抛出，
而不是等迁移或 schema drift 检查才发现缺表。

门禁侧（``scripts/check_modules.py`` 的 ``check_model_collection``）负责确认：
磁盘上每个 ``models.py`` 都被本收集器发现并导入——漏一个即门禁失败。
"""

from __future__ import annotations

import importlib
from pathlib import Path

# 平台模型：kernel/models 已按职责拆成 identity/credential/audit/storage，
# 经包 __init__ re-export，导入即注册全部平台表。
import app.bootstrap.models
import app.kernel.models  # noqa: F401
from app.db.base import Base

_MODULES_ROOT = Path(__file__).resolve().parents[1] / "modules"


def iter_module_model_import_paths() -> list[tuple[str, str]]:
    """枚举 ``app.modules`` 下所有模型的 ``(导入路径, 相对 modules 根路径)``。

    导入路径形如 ``app.modules.sys.config.models``；
    相对路径形如 ``sys/config/models.py``，供门禁比对「磁盘上有、但没被收集」的缺口。
    """
    found: list[tuple[str, str]] = []
    for model_path in sorted(_MODULES_ROOT.rglob("models.py")):
        rel = model_path.relative_to(_MODULES_ROOT)
        package_parts = list(rel.parts)[:-1]  # 去掉末尾的 models.py
        # 导入目标是 <模块包>.models，不是 <模块包> 本身
        found.append((".".join(["app", "modules", *package_parts, "models"]), rel.as_posix()))
    return found


def _collect_module_models() -> None:
    """自动收集所有业务模块的模型，使其表进入 ``Base.metadata``。"""
    for import_path, _rel in iter_module_model_import_paths():
        importlib.import_module(import_path)


_collect_module_models()

metadata = Base.metadata
