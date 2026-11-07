"""`scripts/fastcms.py` 统一 CLI 的注册表完整性回归测试。

防止三类漂移：

1. COMMANDS 与帮助分组 GROUPS 不同步（命令加了但 help 不显示，或反之）；
2. 转发命令（handler 是 `_forward`）指向的 app.cli 子命令不存在——
   转发名写错或 app.cli 改名后，`fastcms db ...` 会 500 或直接报错；
3. check_docs 解析出的 fastcms 命令集合与真实 COMMANDS 脱节——
   check_docs 靠正则读 COMMANDS 字典，改了结构会漏检。
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_docs  # noqa: E402
import fastcms  # noqa: E402


def _app_cli_commands() -> set[str]:
    return check_docs.cli_commands()


def test_every_command_listed_in_exactly_one_group() -> None:
    """每个命令必须出现在 GROUPS 里恰好一次；GROUPS 也不能有未知命令。"""
    names = set(fastcms.COMMANDS)
    grouped = [name for _, members in fastcms.GROUPS for name in members]

    assert len(grouped) == len(set(grouped)), "GROUPS 里有重复命令"
    assert names <= set(grouped), f"COMMANDS 有未分组的命令：{sorted(names - set(grouped))}"
    assert set(grouped) <= names, f"GROUPS 引用了不存在的命令：{sorted(set(grouped) - names)}"


def test_forwarded_commands_exist_in_app_cli() -> None:
    """handler 是 _forward 的命令，其名字必须是 app.cli 的真实子命令。"""
    forwarded = {
        name for name, (_, handler) in fastcms.COMMANDS.items() if handler is fastcms._forward
    }
    missing = forwarded - _app_cli_commands()
    assert not missing, (
        f"fastcms 转发命令在 app.cli 中不存在：{sorted(missing)}；"
        "app.cli 改名了或转发名写错，运行时会 500"
    )


def test_check_steps_reference_real_commands_and_scripts() -> None:
    """统一门禁清单里引用的 app.cli 子命令与 scripts/*.py 都必须真实存在。

    ``fastcms check`` / ``make check`` / CI / release 现在共用 gate.GROUPS，
    清单只有一份，这里直接校验那一份。
    """
    import gate

    cli = _app_cli_commands()
    root = SCRIPTS.parent
    for _group, steps in gate.GROUPS.items():
        for _label, cmd, _cwd in steps:
            args = [str(item) for item in cmd[1:]]  # cmd[0] 是解释器路径
            if args[:2] == ["-m", "app.cli"]:
                assert args[2] in cli, f"gate 引用不存在的 app.cli 子命令：{args[2]}"
            for item in args:
                if item.endswith(".py") and "scripts" in item:
                    path = Path(item)
                    if not path.is_absolute():
                        path = root / path
                    assert path.exists(), f"gate 引用不存在脚本：{item}"


def test_check_delegates_to_unified_gate(monkeypatch) -> None:
    """fastcms check 不能再自己抄一份清单：必须把全部分组交给 gate.run。"""
    monkeypatch.setattr(fastcms, "_ensure_python", lambda: "python")
    monkeypatch.setattr(fastcms, "_ensure_dev_tools", lambda _python: None)
    captured: dict = {}

    def fake_run(groups, *, stop_on_error=False):
        captured["groups"] = list(groups)
        captured["stop_on_error"] = stop_on_error
        return 0

    monkeypatch.setattr(fastcms.gate, "run", fake_run)
    assert fastcms._check("check", ["--stop-on-error"]) == 0
    assert captured["groups"] == list(fastcms.gate.ORDER)
    assert captured["stop_on_error"] is True


def test_check_docs_parses_same_command_set() -> None:
    """check_docs 靠正则解析 COMMANDS 字典，必须与真实 COMMANDS 完全一致。"""
    assert check_docs.fastcms_commands() == set(fastcms.COMMANDS)


def test_help_mentions_every_command() -> None:
    """help 文本必须覆盖全部命令，且每个命令带描述。"""
    help_text = fastcms._help()
    for name, (desc, _handler) in fastcms.COMMANDS.items():
        assert f"fastcms {name}" in help_text, f"help 未列出命令 {name}"
        assert desc, f"命令 {name} 缺少描述"


def test_unknown_command_returns_usage_error() -> None:
    """未知命令返回退出码 2（argparse 用法错误约定），并提示可用命令。"""
    assert fastcms.main(["definitely-not-a-command"]) == 2


def test_version_flag() -> None:
    assert fastcms.main(["--version"]) == 0
