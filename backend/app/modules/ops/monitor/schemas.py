"""监控模块响应模型：与 ``ops/monitor/service.server_info_async()`` 的嵌套结构一一对应。"""

from __future__ import annotations

from pydantic import BaseModel

from app.core.envelope import StrictModel


class OsInfo(BaseModel):
    system: str
    release: str
    version: str
    machine: str
    hostname: str
    python: str
    boot_time: str | None = None
    uptime_seconds: int


class CpuInfo(BaseModel):
    cores: int | None = None
    physical_cores: int | None = None
    percent: float
    # 三项 load average；Windows 无 getloadavg 时为 None
    load_avg: list[float | None]


class MemoryInfo(BaseModel):
    total: int
    available: int
    used: int
    percent: float


class DiskInfo(BaseModel):
    total: int
    used: int
    free: int
    percent: float


class ProcessInfo(BaseModel):
    pid: int
    memory_rss: int
    threads: int
    created_at: str | None = None


class RuntimeInfo(BaseModel):
    app_name: str
    app_version: str
    git_sha: str
    database: str
    scheduler_mode: str
    timezone: str
    scheduler_timezone: str


class ServerInfoOut(StrictModel):
    os: OsInfo
    cpu: CpuInfo
    memory: MemoryInfo
    disk: DiskInfo
    process: ProcessInfo
    runtime: RuntimeInfo
