from __future__ import annotations

import re
import secrets
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
DEFAULT_API_PREFIX = "/api/v1"
# 后台 SPA 默认挂 /admin：保留老部署习惯，也避免和前台、API 抢路径
DEFAULT_ADMIN_BASE = "/admin"
# 前台 SPA 默认挂 /front；设为 / 可恢复「前台占根路径」的老形态
DEFAULT_PORTAL_BASE = "/front"
# 这些路径由后端自己提供（文档、健康检查），不能拿来挂 SPA
RESERVED_SITE_BASES = frozenset({"/docs", "/redoc", "/openapi.json", "/healthz", "/readyz"})
DEFAULT_ADMIN_USERNAME = "admin"
# 本地开发与测试的固定初始口令；生产环境必须显式覆盖 INITIAL_ADMIN_PASSWORD
DEFAULT_ADMIN_PASSWORD = "fast_cms_123"
PRODUCTION_ENVS = frozenset({"prod", "production"})
# 这些环境按本地/自动化场景处理：允许明文 HTTP，因此 Cookie 不强制 Secure
LOCAL_ENVS = frozenset({"dev", "development", "local", "test", "pytest"})
# embedded=随 Web 进程启动；standalone=由 `python -m app.cli scheduler` 单独跑；disabled=不调度
SCHEDULER_MODES = frozenset({"embedded", "standalone", "disabled"})
# ip2region 查询缓存策略：file / vectorIndex / content，语义见 app/core/ipgeo.py
IP2REGION_POLICIES = frozenset({"file", "vectorIndex", "content"})
# 内置 xdb 数据目录：随发布包分发，换库只需替换文件
IP_DATA_DIR = BASE_DIR / "app" / "ip_data"
# 系统默认时区（北京时间）；运行期可被系统参数 system.timezone 覆盖
DEFAULT_TIMEZONE = "Asia/Shanghai"


def timezone_key(value: str, field: str, fallback: str = DEFAULT_TIMEZONE) -> str:
    """归一化时区名并拒绝拼写错误：时区配错会让日志、调度与展示各说各话。"""
    raw = value.strip() or fallback
    # zoneinfo 对 "utc" 的大小写不敏感，但其它时区名大小写敏感，统一成规范写法再入库
    if raw.lower() == "utc":
        raw = "UTC"
    try:
        ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"{field} 必须是合法的 IANA 时区名（如 Asia/Shanghai），收到：{value!r}") from exc
    return raw


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(BASE_DIR.parent / ".env"), str(BASE_DIR / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Fast CMS Admin"
    app_version: str = "0.1.0"
    app_env: str = "dev"
    # 默认只监听回环，对外发布时显式设置 APP_HOST=0.0.0.0
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    app_workers: int = 1
    app_secret_key: str = ""
    app_cors_origins: str = ""
    app_timezone: str = DEFAULT_TIMEZONE
    app_docs_enabled: bool = True
    # 仅在应用前面有可信反向代理时开启，否则客户端可伪造 X-Forwarded-For 绕过限流
    trust_proxy: bool = False

    api_prefix: str = DEFAULT_API_PREFIX
    admin_base: str = DEFAULT_ADMIN_BASE
    portal_base: str = DEFAULT_PORTAL_BASE

    db_url: str = f"sqlite+aiosqlite:///{(DATA_DIR / 'fastcms.db').as_posix()}"
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 5
    # SQLite 迁移前自动备份的最大保留份数，超出时按时间清理最旧的；0 表示不清理
    db_backup_keep: int = 20

    redis_enabled: bool = False
    redis_url: str = "redis://127.0.0.1:6379/0"

    auth_access_ttl_min: int = 15
    auth_refresh_ttl_days: int = 7
    auth_cookie_name: str = "fastcms_refresh"
    auth_cookie_secure: bool | None = None
    auth_max_failed: int = 5
    auth_lock_minutes: int = 15
    # 同一 IP 登录失败达到 auth_max_failed 次后自动封禁的时长；0 表示关闭自动封禁
    auth_ip_ban_minutes: int = 30

    rate_limit_enabled: bool = True
    login_rate_limit: int = 30
    login_rate_window: int = 60
    cache_max_items: int = 4096

    scheduler_mode: str = "embedded"
    # 默认跟随系统时区；只有需要让任务时区与展示时区分开时才设置
    scheduler_timezone: str = ""
    scheduler_job_timeout: int = 300
    scheduler_sync_seconds: int = 30
    scheduler_misfire_grace_seconds: int = 300
    scheduler_python_enabled: bool = True

    storage_local_dir: str = str(DATA_DIR / "uploads")
    upload_max_mb: int = 20

    # 内置清理任务的默认保留天数，仅在种子初始化时写入任务参数
    log_retention_days: int = 90
    session_retention_days: int = 30
    file_retention_days: int = 30
    auto_migrate: bool = False

    notify_enabled: bool = True
    notify_timeout_seconds: int = 10
    notify_max_attempts: int = 5
    notify_batch_size: int = 20
    # 自建网关或本地联调时才放开：默认拒绝指向内网、回环与保留网段的推送地址
    notify_allow_private_target: bool = False

    # 离线 IP 归属地（ip2region xdb）。数据目录默认 backend/app/ip_data，
    # xdb 随发布包一起分发；文件缺失时只影响归属地展示，不影响其它功能。
    ip2region_enabled: bool = True
    # file=每次查询读文件（最省内存最慢）；vectorIndex=只缓存向量索引（默认，
    # 单次约 0.2ms）；content=全量进内存（v4+v6 约 46MB，零 IO 且并发安全）
    ip2region_cache_policy: str = "vectorIndex"
    # 自定义数据目录；留空用内置目录。换新 xdb 后重启生效
    ip2region_data_dir: str = ""

    initial_admin_password: str = DEFAULT_ADMIN_PASSWORD

    @field_validator("api_prefix", mode="after")
    @classmethod
    def _normalize_api_prefix(cls, value: str) -> str:
        prefix = value.strip().rstrip("/")
        if not prefix.startswith("/"):
            prefix = f"/{prefix}" if prefix else ""
        prefix = prefix or DEFAULT_API_PREFIX
        if not re.fullmatch(r"(/[A-Za-z0-9._~-]+)*", prefix):
            raise ValueError(f"API_PREFIX 只能是 / 分隔的路径段，收到：{value!r}")
        return prefix

    @field_validator("admin_base", "portal_base", mode="after")
    @classmethod
    def _normalize_site_base(cls, value: str, info: ValidationInfo) -> str:
        # 归一化成 /admin、/front 这种不带尾斜杠的挂载路径；"/" 表示该站点仍占根路径
        field = "ADMIN_BASE" if info.field_name == "admin_base" else "PORTAL_BASE"
        default = DEFAULT_ADMIN_BASE if info.field_name == "admin_base" else DEFAULT_PORTAL_BASE
        raw = value.strip() or default
        if not raw.startswith("/"):
            raw = f"/{raw}"
        raw = raw.rstrip("/") or "/"
        if raw != "/" and not re.fullmatch(r"(/[A-Za-z0-9._~-]+)+", raw):
            raise ValueError(f"{field} 只能是 / 分隔的路径段，收到：{value!r}")
        return raw

    @model_validator(mode="after")
    def _check_site_bases(self) -> Settings:
        # 两个 SPA 挂载点撞车或压到 API/文档路径上时，运行时表现是「某个站点整页 404」，
        # 很难排查，直接在加载配置时拒绝
        if self.admin_base == self.portal_base:
            raise ValueError(f"ADMIN_BASE 与 PORTAL_BASE 不能相同：{self.admin_base}")
        for field, base in (("ADMIN_BASE", self.admin_base), ("PORTAL_BASE", self.portal_base)):
            if base == "/":
                continue
            if base in RESERVED_SITE_BASES:
                raise ValueError(f"{field}={base} 是后端保留路径，请换一个挂载前缀")
            if (
                base == self.api_prefix
                or self.api_prefix.startswith(f"{base}/")
                or base.startswith(f"{self.api_prefix}/")
            ):
                raise ValueError(f"{field}={base} 与 API_PREFIX={self.api_prefix} 冲突")
        return self

    @field_validator("initial_admin_password", mode="after")
    @classmethod
    def _default_admin_password(cls, value: str) -> str:
        return value.strip() or DEFAULT_ADMIN_PASSWORD

    @field_validator("auth_cookie_secure", mode="before")
    @classmethod
    def _blank_cookie_secure_is_unset(cls, value: object) -> object:
        # .env 里写 `AUTH_COOKIE_SECURE=`（留空表示自动）时不能让 bool 解析炸掉整个应用
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("scheduler_mode", mode="after")
    @classmethod
    def _validate_scheduler_mode(cls, value: str) -> str:
        # 拼错的 SCHEDULER_MODE 会让调度器静默不启动，必须在加载配置时就失败
        mode = value.strip().lower()
        if mode not in SCHEDULER_MODES:
            raise ValueError(
                f"SCHEDULER_MODE 只能是 {'/'.join(sorted(SCHEDULER_MODES))}，收到：{value!r}"
            )
        return mode

    @field_validator("ip2region_cache_policy", mode="after")
    @classmethod
    def _validate_ip2region_policy(cls, value: str) -> str:
        # 同上：拼错策略名会让归属地静默不可用，加载配置时就要炸出来
        policy = value.strip()
        if policy not in IP2REGION_POLICIES:
            raise ValueError(
                f"IP2REGION_CACHE_POLICY 只能是 {'/'.join(sorted(IP2REGION_POLICIES))}，收到：{value!r}"
            )
        return policy

    @field_validator("app_timezone", "scheduler_timezone", mode="after")
    @classmethod
    def _normalize_timezone(cls, value: str, info: ValidationInfo) -> str:
        # scheduler_timezone 留空 = 跟随系统时区（system.timezone / APP_TIMEZONE）
        if info.field_name == "scheduler_timezone" and not value.strip():
            return ""
        return timezone_key(value, info.field_name or "timezone")

    @field_validator("notify_timeout_seconds", "notify_max_attempts", "notify_batch_size", mode="after")
    @classmethod
    def _clamp_notify_numbers(cls, value: int) -> int:
        # 推送参数来自环境变量，越界时收敛到可用区间而不是让投递卡死。
        return min(max(int(value), 1), 60)

    @property
    def is_sqlite(self) -> bool:
        return self.db_url.startswith("sqlite")

    @property
    def ip2region_dir(self) -> Path:
        """xdb 数据目录：默认随包分发位置，可用 IP2REGION_DATA_DIR 覆盖。"""
        raw = self.ip2region_data_dir.strip()
        return Path(raw) if raw else IP_DATA_DIR

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in PRODUCTION_ENVS

    @property
    def is_local_env(self) -> bool:
        """本地/自动化环境：允许明文 HTTP，因此不下发 HSTS。"""
        return self.app_env.strip().lower() in LOCAL_ENVS

    @property
    def cookie_secure(self) -> bool:
        """刷新令牌 Cookie 的 Secure 标记：非本地环境默认开启，可用 AUTH_COOKIE_SECURE 显式覆盖。"""
        if self.auth_cookie_secure is not None:
            return self.auth_cookie_secure
        return self.app_env.strip().lower() not in LOCAL_ENVS

    @property
    def admin_password(self) -> str:
        """初始管理员口令；生产环境禁止沿用内置默认口令。"""
        if self.is_production and self.initial_admin_password == DEFAULT_ADMIN_PASSWORD:
            raise ValueError(
                "生产环境（APP_ENV=prod/production）不允许使用默认管理员口令，"
                "请设置 INITIAL_ADMIN_PASSWORD 为独立强口令后重试。"
            )
        return self.initial_admin_password

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.app_cors_origins.split(",") if item.strip()]

    @property
    def secret_key(self) -> str:
        if self.app_secret_key:
            return self.app_secret_key
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / ".secret"
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        key = secrets.token_urlsafe(48)
        path.write_text(key, encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def api_path(suffix: str) -> str:
    """Build a runtime API path so it follows the configured prefix."""
    rest = suffix if suffix.startswith("/") else f"/{suffix}"
    return f"{settings.api_prefix}{rest}"
