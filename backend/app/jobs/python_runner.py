from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
from contextlib import suppress

from app.core.errors import AppError

OUTPUT_LIMIT = 32 * 1024
SCRIPT_LIMIT = 200_000
# 进程被杀后孙进程可能仍握着管道句柄，收输出必须限时，否则任务永不返回、租约会一直续
PIPE_DRAIN_TIMEOUT = 10
KILL_WAIT_TIMEOUT = 5


class PythonJobError(RuntimeError):
    pass


def validate_python_script(source: str | None) -> None:
    if not source or not source.strip():
        raise AppError("SCHEDULE_SCRIPT_REQUIRED", "Python 脚本不能为空", 422)
    if len(source) > SCRIPT_LIMIT:
        raise AppError("SCHEDULE_SCRIPT_TOO_LARGE", "Python 脚本超过大小限制", 422)
    try:
        compile(source, "<fastcms-job>", "exec", dont_inherit=True)
    except SyntaxError as exc:
        line = f"第 {exc.lineno} 行" if exc.lineno else "未知行"
        raise AppError(
            "SCHEDULE_SCRIPT_INVALID",
            f"Python 脚本语法错误（{line}）：{exc.msg}",
            422,
        ) from exc


def _job_environment(temp_dir: str, code: str, args: dict) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": temp_dir,
        "USERPROFILE": temp_dir,
        "TMPDIR": temp_dir,
        "TEMP": temp_dir,
        "TMP": temp_dir,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "FASTCMS_JOB_ARGS": json.dumps(args or {}, ensure_ascii=False),
        "FASTCMS_JOB_CODE": code,
    }
    if os.name == "nt":
        for key in ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "PATHEXT", "COMSPEC"):
            value = os.environ.get(key)
            if value:
                env[key] = value
    return env


async def _read_limited(stream: asyncio.StreamReader | None, limit: int) -> tuple[str, bool]:
    if stream is None:
        return "", False
    buffer = bytearray()
    truncated = False
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            break
        remaining = limit - len(buffer)
        if remaining > 0:
            buffer.extend(chunk[:remaining])
        if len(chunk) > remaining:
            truncated = True
    return buffer.decode("utf-8", "replace"), truncated


async def _drain_output(task: asyncio.Task[tuple[str, bool]]) -> tuple[str, bool]:
    try:
        return await asyncio.wait_for(task, timeout=PIPE_DRAIN_TIMEOUT)
    except TimeoutError:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        # 返回 truncated=True，让日志明确标注输出被丢弃，而不是看起来像任务没产生输出
        return "", True


async def _kill_process_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        if sys.platform == "win32":
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/F",
                "/T",
                "/PID",
                str(process.pid),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(killer.wait(), timeout=5)
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, FileNotFoundError, OSError):
        try:
            process.kill()
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(process.wait(), timeout=KILL_WAIT_TIMEOUT)
    except TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        # 仍收不到退出码就放弃等待：租约与调度状态不能被子进程拖死
        with suppress(TimeoutError):
            await asyncio.wait_for(process.wait(), timeout=KILL_WAIT_TIMEOUT)


def _format_output(stdout: str, stderr: str, stdout_truncated: bool, stderr_truncated: bool) -> str | None:
    parts = []
    if stdout.strip():
        parts.append(stdout.rstrip())
    if stderr.strip():
        parts.append("[stderr]\n" + stderr.rstrip())
    if stdout_truncated or stderr_truncated:
        parts.append("[输出已截断]")
    text = "\n".join(parts).strip()
    return text[:OUTPUT_LIMIT] if text else None


async def execute_python_script(source: str, args: dict, timeout: int, code: str) -> str | None:
    validate_python_script(source)
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    with tempfile.TemporaryDirectory(prefix="fastcms-job-") as temp_dir:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-X",
            "utf8",
            "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=temp_dir,
            env=_job_environment(temp_dir, code, args),
            **kwargs,
        )
        assert process.stdin is not None
        try:
            process.stdin.write(source.encode("utf-8"))
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()

        stdout_task = asyncio.create_task(_read_limited(process.stdout, OUTPUT_LIMIT))
        stderr_task = asyncio.create_task(_read_limited(process.stderr, OUTPUT_LIMIT))
        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=max(1, timeout))
        except TimeoutError:
            timed_out = True
            await _kill_process_tree(process)
        except asyncio.CancelledError:
            await _kill_process_tree(process)
            raise
        finally:
            stdout, stdout_truncated = await _drain_output(stdout_task)
            stderr, stderr_truncated = await _drain_output(stderr_task)

    if timed_out:
        raise PythonJobError(f"Python 脚本执行超时（{timeout} 秒）")
    if process.returncode != 0:
        detail = (stderr or stdout).strip()
        if len(detail) > OUTPUT_LIMIT:
            detail = detail[:OUTPUT_LIMIT] + "\n[输出已截断]"
        raise PythonJobError(f"Python 脚本执行失败（退出码 {process.returncode}）：{detail or '无错误输出'}")
    return _format_output(stdout, stderr, stdout_truncated, stderr_truncated)
