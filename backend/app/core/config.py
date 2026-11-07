from __future__ import annotations

import contextlib
import re
import secrets
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]
# 仓库根目录：DATA_DIR 等相对路径以它为基准，而不是随当前工作目录漂移
REPO_ROOT = BASE_DIR.parent
# 默认数据根目录 backend/data：SQLite 库、上传文件、签名密钥、迁移备份、启动锁都在这里
DATA_DIR = BASE_DIR / "data"
DEFAULT_DB_FILENAME = "fastcms.db"
DEFAULT_UPLOAD_SUBDIR = "uploads"
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

# 未显式配置 APP_SECRET_KEY 时，按数据根目录缓存从 data/.secret 读到的密钥，
# 避免每次签发/校验 JWT 都做一次文件读取。键是目录字符串，多实例测试互不干扰。
_SECRET_CACHE: dict[str, str] = {}
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


def _resolve_relative(raw: str, base: Path) -> str:
    """相对路径统一以 ``base`` 为基准，避免随当前工作目录漂移。

    写入 .env 的 ``./my.db``、``uploads`` 这类路径，用户预期是相对某个固定位置，
    而不是依赖「启动时恰好在哪里执行」。
    """
    path = Path(raw)
    return path.as_posix() if path.is_absolute() else (base / path).as_posix()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # pydantic-settings 按顺序 update，后读的文件覆盖先读的：把仓库根 .env
        # 放最后，保证文档与全部脚本口径一致（根 .env > backend/.env）。
        # 反过来的话 install/start/release 读根 .env、运行时却用 backend/.env，
        # 同一份配置能得出两种结果。
        env_file=(str(BASE_DIR / ".env"), str(BASE_DIR.parent / ".env")),
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

    # 运行时数据根目录：SQLite 库、上传文件、签名密钥、迁移备份、启动锁都放在这里。
    # 代码升级只替换源码，数据留在 DATA_DIR 原地不动；相对路径以仓库根为基准
    data_dir: str = ""
    # SQLite 专属的库文件路径（比 DB_URL 更直观）。留空则用 <数据根>/fastcms.db。
    # 优先级：DB_URL > DB_PATH > 默认库路径
    db_path: str = ""
    db_url: str = ""
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
    # P1-12：Python 脚本任务以同权限子进程执行，属高危能力，默认关闭（生产与本地一致），
    # 需要时显式设 SCHEDULER_PYTHON_ENABLED=true 并理解「等价进程权限」风险
    scheduler_python_enabled: bool = False
    # A-9 / 3-6：嵌入式调度器 + 多 worker 默认硬失败（多进程各自持有调度器会让
    # next_run_at 抖动、任务重复执行）。显式开启表示「接受该风险并自担」，由租约
    # 与 max_instances=1 兜底；开启时启动打警告。
    scheduler_embedded_multiworker: bool = False
    # B-8 / 4-2：可选暴露 /metrics（Prometheus 文本格式）；关闭时端点不注册，零开销。
    metrics_enabled: bool = False
    # B-10 / 6-2：权限废弃宽限期。权限被标记 deprecated_at 后仍可访问，响应带
    # Deprecation/Sunset 头提示客户端迁移；超过该天数后拒绝（Sunset 到期）。
    permission_deprecation_grace_days: int = 180
    # 慢请求阈值（毫秒）：超过则打 WARNING 日志（含 request_id / 方法 / 路径 / 耗时）。
    slow_request_threshold_ms: int = 2000
    # 慢 SQL 阈值（毫秒）：单条语句执行超过则打 WARNING（含 request_id / 语句）。
    # SQL 慢是「请求慢」最常见的根因，单独设阈值并打在语句级，定位成本低一个量级。
    slow_sql_threshold_ms: int = 500
    # 审计日志批量落库（B-7 / Phase 11-3）：进程内队列攒批 + 定时刷盘。
    # 队列满（audit_queue_capacity）时降级为同步直写，宁可拖慢请求也不丢审计。
    audit_queue_capacity: int = 10000
    audit_batch_size: int = 200
    audit_flush_interval_seconds: float = 0.2
    # 同一批记录连续写入失败多少轮后丢弃（带指数退避）。没有上限时，一条坏数据
    # 会让后台刷盘每 200ms 重试同一批，形成永不终止的无效事务循环。
    audit_max_retries: int = 5

    # 上传文件目录；留空则用 <数据根>/uploads
    storage_local_dir: str = ""
    upload_max_mb: int = 20

    # 内置清理任务的统一保留天数（日志 / 会话 / 未引用文件共用），仅在种子初始化时
    # 写入任务参数；后续可按任务在「任务管理」里单独调整，互不影响
    retention_days: int = 30
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

    @field_validator("data_dir", mode="after")
    @classmethod
    def _normalize_data_dir(cls, value: str) -> str:
        # 数据根目录必须落成绝对路径：它是所有运行时数据的锚点，
        # 相对路径会以仓库根为基准解析，避免「从哪里启动」影响数据位置
        raw = value.strip()
        return _resolve_relative(raw, REPO_ROOT) if raw else DATA_DIR.as_posix()

    @model_validator(mode="after")
    def _resolve_data_defaults(self) -> Settings:
        """DB_URL / STORAGE_LOCAL_DIR 留空时由数据根目录推导。

        只改 DATA_DIR 就能把整套数据（库、上传、密钥、备份、锁）搬到别的磁盘，
        后续升级代码不需要碰数据目录。优先级：DB_URL > DB_PATH > <数据根>/fastcms.db。
        """
        if not self.db_url.strip():
            target = self.db_path.strip()
            if target and Path(target).is_absolute():
                path = Path(target)
            else:
                path = self.data_root / (target or DEFAULT_DB_FILENAME)
            self.db_url = f"sqlite+aiosqlite:///{path.as_posix()}"
        if self.storage_local_dir.strip():
            # 相对路径统一以数据根为基准，避免随 CWD 漂移
            self.storage_local_dir = _resolve_relative(self.storage_local_dir.strip(), self.data_root)
        else:
            self.storage_local_dir = (self.data_root / DEFAULT_UPLOAD_SUBDIR).as_posix()
        return self

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

    @model_validator(mode="after")
    def _check_cors_origins(self) -> Settings:
        # P0-2：CORS 开启凭据时禁止 `*` 通配来源。`*` 与 `allow_credentials=True` 同开
        # 会让浏览器把凭据发往任意站点，等价于把 refresh Cookie 暴露给攻击者。
        # 本应用始终以凭据模式挂载 CORS（Cookie 认证），因此 `*` 一律拒绝。
        if any(item.strip() == "*" for item in self.cors_origins):
            raise ValueError(
                "APP_CORS_ORIGINS 不允许配置 `*`：应用以 allow_credentials=True 挂载 CORS，"
                "通配来源会把凭据暴露给任意站点。请改为显式来源列表，或留空使用同源。"
            )
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
    def data_root(self) -> Path:
        """运行时数据根目录：库、上传、密钥、迁移备份、启动锁与日志都跟随它。"""
        return Path(self.data_dir)

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
        # 显式配置优先；未配置时读 data/.secret，首次访问生成并落盘。
        # 结果按数据根目录缓存：签发/校验每个 JWT 都要取一次密钥，不缓存等于
        # 每次请求都做一次文件 stat+read（密钥在进程生命周期内不会变）。
        root = self.data_root
        cached = _SECRET_CACHE.get(str(root))
        if cached:
            return cached
        # 密钥落在数据根目录下：数据目录随迁移/备份整体搬迁，密钥随之走，
        # 不会出现「代码更新了、密钥换了、所有会话失效」
        root.mkdir(parents=True, exist_ok=True)
        path = root / ".secret"
        if path.exists():
            key = path.read_text(encoding="utf-8").strip()
        else:
            key = secrets.token_urlsafe(48)
            path.write_text(key, encoding="utf-8")
            # Windows 不支持 POSIX 权限位；文件落在数据目录内，
            # 权限由目录 ACL 决定，这里只保证不因 chmod 失败而阻断启动。
            with contextlib.suppress(OSError):
                path.chmod(0o600)
        _SECRET_CACHE[str(root)] = key
        return key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def api_path(suffix: str) -> str:
    """Build a runtime API path so it follows the configured prefix."""
    rest = suffix if suffix.startswith("/") else f"/{suffix}"
    return f"{settings.api_prefix}{rest}"
