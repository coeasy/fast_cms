# Fast CMS Admin

Fast CMS 是一个可直接启动、低资源占用的后台管理基座：后端 FastAPI + SQLAlchemy 2 + APScheduler，前端由后端用 Jinja2 直接渲染 HTML + 原生 ES Module。

**没有 Node.js、没有包管理器、没有前端构建步骤、没有独立的前端进程**：页面模板、CSS 与原生 JS 都是后端源码的一部分，与 API 同源同端口一起发布。

默认仅依赖 Python 与 SQLite，**不会连接 MySQL 或 Redis**。MySQL 8 与 Redis 7 均为显式开启的可选能力。

## 功能范围

- 认证：JWT access token + 轮换 refresh token（HttpOnly Cookie）+ 数据库会话、登录锁定、强制首次改密、会话撤销、在线心跳
- 登录防护：统一凭证错误响应、未知用户也执行哈希校验、锁定仅在密码正确后暴露、按 IP/登录用户的敏感接口限流（429 + `Retry-After`）
- RBAC：用户、角色、菜单、部门、权限注册表、数据范围（全部/本部门/本部门及以下/仅自己/指定部门）
- 数据范围边界：用户、部门、在线会话、登录/操作日志、文件按范围裁剪；角色、菜单、字典、参数、公告等配置类资源全局可见；`last_login_ip` 等字段按权限最小化返回（详见 [docs/认证与权限.md](docs/认证与权限.md) 第 6 节）
- 管理后台准入：`system:admin:access` 作为入口权限，非管理员仅可使用个人中心与公告用户端接口
- 系统基础：字典、系统参数、公告、文件管理、个人资料、头像、修改密码、会话管理、系统时区（`system.timezone`，默认北京时间；页面展示、日志时间戳、CSV 导出、通知正文与任务默认时区统一跟随，数据库仍按 UTC 存储）
- 主题模板：7 套内置主题（含金融深色/浅色紧凑版）、自定义主题 CRUD、背景色与排版密度设置、全局启用、个人偏好切换、公开主题接口；主题以 CSS 变量形式落到页面上，首屏由内联脚本套用上次缓存，刷新不闪
- 审计与监控：登录日志、操作日志、CSV 导出、在线用户、强制下线、服务器监控、仪表盘
- 定时任务：注册表任务与 Python 脚本任务、可复用任务模板、Cron/间隔/单次触发、暂停/恢复/立即执行、执行日志、数据库租约去重、重启恢复、独立调度进程
- 消息通知：钉钉/飞书/企业微信/通用 Webhook 渠道配置、任务失败与公告发布两类触发点、入队去重、指数退避重试、发送记录与手工重试（详见 [docs/消息通知.md](docs/消息通知.md)）
- 代码生成：单表 CRUD 预览、zip 下载、写入 `backend/generated/`（不覆盖已有文件），生成物包含 Python 后端与原生 JS 页面
- 工程能力：OpenAPI 契约、权限码注册表、接口连通性体检、Docker、Compose profiles、CI

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.12、FastAPI 0.150.x、SQLAlchemy 2 async、Alembic、APScheduler 3.11 |
| 数据库 | SQLite（默认，aiosqlite）、MySQL 8（可选，aiomysql） |
| 缓存/锁 | 进程内实现（默认）、Redis 7（可选，默认关闭） |
| 前端 | Jinja2 服务端渲染 + 原生 ES Module + 手写 CSS（无框架、无构建、无 CDN） |
| 契约 | OpenAPI 3.1，`scripts/check_contract.py` 与 `check_api_coverage.py` 双向校验 |
| 测试 | pytest（单元 + 集成，含页面渲染与脚本静态检查） |

## 目录结构

```text
fast_cms/
├── backend/
│   ├── app/               # 应用代码（core/db/modules/jobs/bootstrap/codegen）
│   │   └── web/           # HTML 前端：页面路由 + 组件 + 静态资源
│   │       ├── router.py  # 页面清单（PAGES）与后台/前台路由
│   │       ├── templates/ # base + components + layouts + pages/**，Jinja2
│   │       └── static/    # css/{base,admin,portal,public}.css、js/{app,core,ui,icons}.js、js/shell/**、js/pages/**
│   ├── migrations/        # 迁移脚本
│   ├── tests/             # pytest 单元与集成测试
│   ├── data/              # SQLite、上传文件、自动生成的密钥（运行时）
│   ├── requirements.txt   # 运行时依赖，与 pyproject 逐项一致（无 venv 部署入口）
│   ├── requirements-dev.txt
│   ├── pyproject.toml
│   └── alembic.ini
├── docker/                # Dockerfile、entrypoint、compose.yml
├── scripts/               # 安装/启动/发布、统一命令行、环境探测、契约与连通性检查、MySQL 冒烟
├── docs/                  # 专题文档
├── openapi.json           # 由后端导出
├── fastcms                # 统一命令行入口（Linux / macOS）
├── fastcms.cmd            # 统一命令行入口（Windows）
├── .env.example
└── Makefile
```

前端分三层：**组件库**（所有界面共用）、**外壳**（后台 / 前台 / 公共页各一套）、**页面**（只写内容）。

模板：

| 文件 | 职责 |
|---|---|
| `templates/base.html` | 文档骨架：`<head>`、首屏主题、`window.__FASTCMS__` 注入、加载 `app.js` |
| `templates/components/ui.html` | 组件宏：`page_head`、`card`、`filter_form`、`data_table`、`modal`、`field`/`select_field`/`switch_field`、`stat`、`icon` 等 |
| `templates/layouts/admin.html` | **后台外壳**：侧边栏、顶栏、面包屑、公告弹窗、启动遮罩 |
| `templates/layouts/portal.html` | **前台外壳**：站点导航与页脚，页面只填 `portal_content` |
| `templates/layouts/blank.html` | **公共页外壳**：登录页与错误页，无导航无守卫 |
| `templates/partials/**` | 可复用片段：首屏主题脚本、启动遮罩、公告弹窗 |
| `templates/pages/**` | 后台页面模板，`{% import "components/ui.html" as ui %}` 后用宏拼装 |

样式（按层拆分，页面只加载自己需要的）：

| 文件 | 职责 |
|---|---|
| `static/css/base.css` | 设计令牌（CSS 变量）、重置、通用组件：按钮 / 表单 / 表格 / 分页 / 标签 / 弹层 / 提示 |
| `static/css/admin.css` | 后台骨架：侧边栏、顶栏、页头、卡片、筛选栏、统计卡、树、图表、响应式 |
| `static/css/portal.css` | 前台站点：导航、Hero、卡片、页脚 |
| `static/css/public.css` | 公共页：登录页与错误页 |

脚本：

| 文件 | 职责 |
|---|---|
| `static/js/app.js` | 入口：按 `body[data-page]` 决定加载哪套外壳与页面脚本 |
| `static/js/core.js` | 请求层（401 自动刷新重放）、会话、表单、弹窗、时间与时区、主题引擎 |
| `static/js/ui.js` | `DataTable`（取数/渲染/分页）、下拉、树、标签 |
| `static/js/shell/admin.js` | 后台外壳：登录守卫、导航与面包屑、主题菜单、公告铃铛、心跳 |
| `static/js/shell/auth.js` | 登录页：主题预置、版本号、登录提交与跳转 |
| `static/js/shell/portal.js` | 前台外壳：主题与站点文案渲染 |
| `static/js/pages/**` | 后台页面逻辑，一个页面一个 ES Module |

前后台互不越界：前台页面只加载 `base.css + portal.css` 与 `shell/portal.js`，
后台页面只加载 `base.css + admin.css` 与 `shell/admin.js`；`scripts/check_contract.py`
会校验页面继承关系、页面脚本引用的 `#id` 是否真的存在于渲染结果里，串了就报错。

## 环境要求

- Python 3.12+（可由 Miniconda/Anaconda 提供）
- Miniconda / Anaconda：**可选**，只想用系统 Python 时无需安装
- GNU Make（可选，仅 Make 方式需要）

不需要 Node.js、pnpm 或任何前端工具链。

## 一键安装（新机器）

统一入口是 `fastcms`（从任何目录可跑，不依赖 make / 当前目录）；下面两个「一键安装 / 一键启动」小节列的是它背后的细节，可直接用：

```bash
./fastcms doctor          # 只探测：Python / Miniconda / 数据库 / 端口 / 页面资源 / 依赖项
./fastcms init            # 探测 → 创建 backend/.venv → 装后端依赖 → 初始化数据库
./fastcms server          # 服务端一键部署：生产配置 + 0.0.0.0 + 打印对外地址（--http 用于无 HTTPS）
```

不需要事先知道服务器装了什么，脚本会自己探测（等价脚本形式）：

```bash
python scripts/env_check.py          # 只探测：Python / Miniconda(Anaconda) / 数据库 / 端口 / 页面资源 / 依赖项
python scripts/install.py            # 探测 → 创建 backend/.venv → 装后端依赖 → 初始化数据库
python scripts/install.py --prod     # 发布安装：不装 dev 依赖
python scripts/install.py --use-conda --conda-env fastcms   # 用 conda 建 3.12 环境承载后端，不创建 venv
python scripts/install.py --recreate # 删掉并重建 backend/.venv
```

### 不建虚拟环境，直接装进目标环境

服务器上已经有统一维护的 Python / conda 环境时，不必再套一层 venv：

```bash
python scripts/install.py --system --deps-only   # 依赖装进当前解释器，代码从源码目录运行
python scripts/install.py --system --user        # 没有 root 权限时装进 ~/.local
python scripts/start.py --no-venv                # 启动器不再创建 backend/.venv
```

`--deps-only` 走的是 `backend/requirements.txt`，**不做 editable 安装**：代码始终从源码目录读取，
模板与静态资源不依赖打包规则，升级就是覆盖源码。依赖装完后可以直接手动拉起：

```bash
cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

两点需要注意：

- Debian 12 / Ubuntu 23.04+ 的系统 Python 受 PEP 668 保护，`pip install` 会被拒绝。脚本会拦下并给出三个选项
  （建 venv / `--user` / `--break-system-packages`），不会静默改写系统环境；
- `requirements.txt` 与 `pyproject.toml` 的依赖逐项一致，由 `backend/tests/unit/test_requirements_sync.py` 校验，
  不会出现"两边装出来的包不一样"。

Windows 双击 `install.cmd`，Linux / macOS 执行 `./install.sh`（Make 环境可用 `make install`）。两个包装脚本会按
「已有 venv → Miniconda/Anaconda/Miniforge 的 base 或指定环境 → 系统 Python」的顺序挑选解释器；即使系统 Python 低于
3.12，只要机器上装了 conda，`install.py` 也会自动改用 conda 里满足版本的环境。探测结果里的 `✗` 是阻塞项（Python
缺失、页面资源缺失），`!` 是提醒（未装 conda、端口被占用、启用了 Redis 但连不上等）。

## 一键启动（推荐）

无需 Docker、MySQL、Redis 或 GNU Make。启动器会自动创建后端虚拟环境、安装缺失依赖、生成 `.env`、执行数据库初始化，并在默认端口被占用时自动后移选择空闲端口。**只拉起一个进程**，后台页面、前台站点与 API 都在同一端口上。

```bash
./fastcms dev        # 开发：单进程 + 热重载，只监听 127.0.0.1
./fastcms server     # 服务端：监听 0.0.0.0，打印其他机器该访问的地址
./fastcms test       # 独立测试库 + 固定测试口令，启动即可测试
```

Windows（cmd / PowerShell）：

```bat
.\start.cmd
.\start.cmd preview
.\start.cmd test --check
```

Linux / macOS：

```bash
chmod +x start.sh   # 首次
./start.sh
./start.sh preview
./start.sh test --check
```

| 模式 | 说明 |
|---|---|
| `dev`（默认） | 单进程 `uvicorn --reload`，只监听 `127.0.0.1`，默认端口 8000，被占用时依次后移 |
| `server` | 关闭热重载，固定监听 `0.0.0.0`，启动后打印其他机器该访问的地址（服务端部署用） |
| `preview` | 关闭热重载，按 `.env` 的 `APP_HOST` / `APP_PORT` 监听，适合对内演示或容器外试跑 |
| `test` | 独立测试库 `backend/data/quickstart-test.db`，不触碰开发库，启动即可测试 |

`test` 模式每次启动都会重建测试库与上传目录（`--keep-db` 可保留），管理员为 `admin / fast_cms_123`，可用环境变量 `FASTCMS_TEST_ADMIN_PASSWORD` 覆盖。该模式为非生产环境，登录后不强制改密。

常用参数：

```bash
--backend-port 8030   # 指定端口（--port 同义；被占用则直接报错）
--check               # 就绪后执行自检（test 模式额外验证登录），然后自动退出
--open                # 就绪后打开浏览器
--keep-db             # test 模式保留并复用已有测试库
```

运行日志写入 `backend/data/logs/`，数据库、测试上传目录和日志均已被 `.gitignore` 忽略。按 `Ctrl+C` 会停止服务及其子进程。完整参数见 `.\start.cmd --help` / `./start.sh --help`。

## 快速开始（Make）

```bash
make install   # 等价于 python scripts/install.py：探测环境、装依赖、生成 .env、迁移并写入基线数据
make env-check # 只做环境探测
make dev       # http://localhost:8000/admin （热重载）
make preview   # 按 .env 的 APP_HOST/APP_PORT 启动，关闭热重载
make server    # 服务端一键部署：无 .env 自动生成 production 配置（HTTP=1 用于无 HTTPS）
```

首次初始化会创建 `admin` 账号，口令固定、不再生成随机密码：

- 默认口令 `fast_cms_123`，可用 `INITIAL_ADMIN_PASSWORD` 覆盖；`.env` 里留空等同于使用默认口令；
- 本地开发/测试（`APP_ENV=dev|test`）拿到账号即可直接登录，不强制改密；
- 生产环境（`APP_ENV=prod|production`）禁止沿用默认口令：`init`/启动会直接失败并提示设置 `INITIAL_ADMIN_PASSWORD`，同时创建的 `admin` 带 `must_change_password`，首次登录必须改密（未改密前除个人中心相关接口外的业务接口返回 `AUTH_PASSWORD_CHANGE_REQUIRED`（403））；
- 初始口令不会再写入日志；只有非生产环境的初始化输出会打印一次，方便本地使用。

如果机器没有 Make，可手动执行：

```bash
python3 -m venv backend/.venv
backend/.venv/bin/pip install -e "backend[dev]"
cp .env.example .env
cd backend && .venv/bin/python -m app.cli init
.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

Windows（Git Bash）使用 `backend/.venv/Scripts/python.exe`。启动后直接访问：

- 后台：<http://localhost:8000/admin>（`ADMIN_BASE`）
- 前台：<http://localhost:8000/front>（`PORTAL_BASE`）
- 接口文档：<http://localhost:8000/docs>
- OpenAPI JSON：<http://localhost:8000/openapi.json>
- 健康检查：`/healthz`；就绪检查（数据库 + 迁移版本）：`/readyz`
- 版本信息：`${API_PREFIX}/meta/version`（默认 `/api/v1/meta/version`）

修改页面模板、CSS 或 JS 后刷新浏览器即可；`dev` 模式带热重载，`preview` 模式需要重启进程。

## 配置

所有配置从环境变量读取，其次读取仓库根目录 `.env`，最后使用代码默认值。完整示例见 [.env.example](.env.example)。

**`.env` 只需要写你要改的项。** `.env.example` 里只有 7 项是生效的（核心配置），其余 47 项全部注释掉，注释里的值就是代码默认值——不写和取消注释的运行结果完全一样。所以一份 `.env` 通常就是十来行。

| 核心项 | 默认 | 说明 |
|---|---|---|
| `APP_ENV` | `dev` | `dev` / `production`；对外开放请用 `production` |
| `APP_HOST` | `127.0.0.1` | **要让其他机器访问必须是 `0.0.0.0`** |
| `APP_PORT` | `8000` | 页面、前台与 API 共用这一个端口 |
| `APP_SECRET_KEY` | 自动生成到 `<数据根>/.secret` | 生产建议显式注入 |
| `INITIAL_ADMIN_PASSWORD` | `fast_cms_123` | `production` 下留空会拒绝启动 |
| `APP_TIMEZONE` | `Asia/Shanghai` | 系统时区初值，首次种子后由系统参数接管 |
| `AUTH_COOKIE_SECURE` | 按环境自动 | 用 `http://` 访问时必须设 `false`，否则登录 15 分钟后掉线 |

其余高级项（数据库、Redis、调度、限流、通知、IP 归属地等）按需取消注释即可，取值含义见 `.env.example` 内的行内注释。

### 服务端一条命令部署

新机器 clone 下来，一条命令就能对外提供服务（依赖、`.env`、数据库初始化都会自动补齐）：

```bash
fastcms server                        # 服务端一键部署（推荐）
python scripts/start.py server        # 等价：监听 0.0.0.0，打印其他机器该访问的地址
make server                           # 等价写法
```

没有 `.env` 时会自动生成「对外提供服务」的配置，并在终端打印一次初始口令：

| 写入项 | 值 | 原因 |
|---|---|---|
| `APP_ENV` | `production` | 强制强口令、Cookie 按 HTTPS 规则下发 |
| `APP_HOST` | `0.0.0.0` | 否则只有本机能访问 |
| `INITIAL_ADMIN_PASSWORD` | 随机 16 位（打印一次） | 生产不允许默认口令 |
| `APP_DOCS_ENABLED` | `false` | 对外服务时收起 `/docs` 与 `/openapi.json` |

`.env` 已存在时**一律尊重现有配置、绝不覆盖**，只在 `APP_ENV` 不是 `production` 时打印提醒；重复执行不会更换口令或密钥（幂等）。

没有 HTTPS、直接用 `http://<内网IP>:8000` 访问时，加 `--http`（额外写入 `AUTH_COOKIE_SECURE=false`）：

```bash
fastcms server --http                 # 或 python scripts/start.py server --http / make server HTTP=1
```

不加 `--http` 而实际走明文 HTTP 的话，浏览器不会保存刷新令牌，表现为"能登录、15 分钟后掉线"。`start.py server` 启动时会检测这个组合并直接提示。

启动后终端会打印形如 `http://10.213.25.186:8000/admin` 的地址，把它发给使用者即可；管理员账号是 `admin`，口令在首次启动输出里（只显示一次）或 `.env` 的 `INITIAL_ADMIN_PASSWORD`。

`python scripts/install.py --server` 做的是同一件事（写配置 + 装依赖 + 初始化库），适合想先准备好环境、再单独控制启动的场景；日常直接用 `start.py server` 即可。

其他常用项（默认值即下表，需要时在 `.env` 里取消注释）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `SCHEDULER_TIMEZONE` | 留空 | 留空 = 任务默认时区跟随系统时区；需要分离时填 IANA 时区名 |
| `API_PREFIX` | `/api/v1` | 后端业务路由统一前缀；`/healthz`、`/readyz`、`/docs` 不受影响 |
| `DATA_DIR` | `backend/data` | **运行时数据根目录**：库、上传、签名密钥、迁移备份、启动锁、日志都放这里；相对路径以仓库根为基准 |
| `DB_PATH` | `<数据根>/fastcms.db` | SQLite 库文件路径（比 `DB_URL` 直观）；相对路径以数据根为基准；优先级 `DB_URL` > `DB_PATH` > 默认 |
| `DB_URL` | SQLite `<数据根>/fastcms.db` | 显式数据库连接串，留空按 `DB_PATH` / 默认推导 |
| `REDIS_ENABLED` | `false` | 关闭时不会导入或连接 Redis |
| `SCHEDULER_MODE` | `embedded` | `embedded` / `standalone` / `disabled` |
| `SCHEDULER_SYNC_SECONDS` | `30` | standalone 同步任务定义的间隔 |
| `SCHEDULER_MISFIRE_GRACE_SECONDS` | `300` | 到点后允许补跑的宽限秒数，超出则记 `skipped`（错过执行时间） |
| `SCHEDULER_PYTHON_ENABLED` | `true` | 是否允许 Python 脚本任务；关闭后禁止创建/恢复/触发 |
| `AUTH_ACCESS_TTL_MIN` | `15` | access token 分钟 |
| `AUTH_REFRESH_TTL_DAYS` | `7` | refresh token 天数 |
| `AUTH_MAX_FAILED` / `AUTH_LOCK_MINUTES` | `5` / `15` | 同一用户名连续失败次数与锁定时长 |
| `LOGIN_RATE_LIMIT` / `LOGIN_RATE_WINDOW` | `30` / `60` | 单 IP 登录尝试上限（超限 429） |
| `RATE_LIMIT_ENABLED` | `true` | 敏感接口窗口限流总开关 |
| `TRUST_PROXY` | `false` | 仅在可信反向代理后开启，采用 `X-Forwarded-For` 首跳作为客户端 IP |
| `CACHE_MAX_ITEMS` | `4096` | 无 Redis 时进程内缓存条目上限，防止限流计数无限增长 |
| `UPLOAD_MAX_MB` | `20` | 上传大小上限 |
| `AUTO_MIGRATE` | `false` | 生产默认关闭，由 CLI 显式迁移 |
| `NOTIFY_ENABLED` | `true` | IM 推送总开关，关闭后不再入队也不再投递 |
| `NOTIFY_TIMEOUT_SECONDS` | `10` | 单次推送的 HTTP 超时（收敛到 1-60） |
| `NOTIFY_MAX_ATTEMPTS` | `5` | 自动重试次数上限，达到后置为 `failed` |
| `NOTIFY_BATCH_SIZE` | `20` | 单轮「通知消息投递」取出的记录数（收敛到 1-60） |
| `NOTIFY_ALLOW_PRIVATE_TARGET` | `false` | 放开内网/回环地址与非默认端口，仅供本地联调或自建内网网关 |

敏感配置（数据库连接、密钥、Cookie 域名）只允许来自环境变量，不写入数据库。

### 数据与代码分离（升级不碰数据）

所有运行时数据都集中在**数据根目录** `DATA_DIR`（默认 `backend/data`）：SQLite 库、上传文件、签名密钥、迁移备份、启动锁、日志。代码只放在 `backend/app/` 与 `backend/migrations/`，两者互不依赖——**升级代码就是替换源码，数据原地不动**。

| 数据项 | 位置 | 说明 |
|---|---|---|
| SQLite 库 | `<数据根>/fastcms.db` | 用 `DB_PATH` 改库文件名/位置 |
| 上传文件 | `<数据根>/uploads` | 用 `STORAGE_LOCAL_DIR` 改 |
| 签名密钥 | `<数据根>/.secret` | 自动生成，重启不变 |
| 迁移备份 | `<数据根>/backups` | 每次 SQLite 迁移前自动备份 |
| 启动锁 | `<数据根>/bootstrap.lock` | 多 worker 串行引导 |
| 运行日志 | `<数据根>/logs` | 仅 `start.py` 启动器写入 |

要把整份数据搬到别的磁盘（数据盘、NAS 挂载点），只需三步：

```bash
# 1. 停服（Ctrl+C，或 systemd/docker 停止服务）
# 2. 迁移数据目录（保留权限/属主）
mv backend/data /mnt/fastcms-data

# 3. 在 .env 里指向新位置并重启
echo "DATA_DIR=/mnt/fastcms-data" >> .env
python scripts/start.py server
```

库文件单独换位置同理：设 `DB_PATH=/mnt/data/fastcms.db`（绝对路径）或 `DB_PATH=data/app.db`（相对数据根）。

### 自定义挂载路径与 API 前缀

站点根路径不直接挂站点：前台默认在 `/front`，后台默认在 `/admin`，访问 `/` 会 307 跳到前台。集成到已有网关或门户时只需改两个旋钮：

| 变量 | 默认 | 说明 |
|---|---|---|
| `ADMIN_BASE` | `/admin` | 后台页面与静态资源的挂载路径；设为 `/` 则后台占用根路径，此时前台不挂载（启动时会打警告） |
| `PORTAL_BASE` | `/front` | 前台站点挂载路径；设为 `/` 可恢复「前台占根路径」的老形态 |
| `API_PREFIX` | `/api/v1` | 挂载所有业务路由，同时决定 refresh cookie 的 `Path`、审计日志里的请求路径与响应中的下载地址 |

三者的归一化规则只在 `backend/app/core/config.py` 一处定义（补斜杠、拒绝非法路径、拒绝两者相同或压到 API/文档路径上）。页面渲染时后端把 `base`、`apiBase`、`portalBase` 直接注入到 `window.__FASTCMS__`，静态资源地址也由模板拼出，**所以改完 `.env` 重启即可，不存在需要重新构建的产物**。

改动 API 前缀后的顺序固定为：

1. 设 `API_PREFIX` 并重启后端；
2. `cd backend && python -m app.cli export-openapi --output ../openapi.json`；
3. `python scripts/check_contract.py` 校验 OpenAPI 路径是否都在当前前缀下，以及菜单路径、页面模板、页面脚本三者是否成对；
4. `python scripts/check_api_coverage.py --strict` 逐个比对 openapi 端点与前端真实调用点（页面脚本里的 `api.get/post/...` 与 `static/js/shell/*.js` 的外壳调用），后端有接口没人调、前端调了不存在的路径都会直接列出。

日常开发只需 `make contract-check`，它按 `openapi → permission-check → check_contract.py → check_api_coverage.py --strict` 的顺序跑完。

### 启用 MySQL

1. 创建空数据库与账号（Compose 会自动完成）：
   ```sql
   CREATE DATABASE fastcms CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   ```
2. 设置 `DB_URL=mysql+aiomysql://fastcms:fastcms@127.0.0.1:3306/fastcms?charset=utf8mb4`；
3. 执行 `cd backend && python -m app.cli init`（或 `make db-upgrade`）。

### 启用 Redis

设置 `REDIS_ENABLED=true` 与 `REDIS_URL=redis://127.0.0.1:6379/0`。显式启用但无法连接时应用会启动失败并输出连接信息，不会静默降级到内存实现。

多 API worker 或独立调度器场景建议同时启用 MySQL；SQLite 只适合单 worker。

## 常用命令

统一入口是 **`fastcms`**（`./fastcms` 或 `fastcms.cmd`，亦可 `python scripts/fastcms.py`）——不依赖 make，从任何目录都能跑。它把原来的安装/启动/探测/门禁收敛成一条命令，业务命令（`db` / `seed` / `backup` 等）直接转发 `app.cli`。

| 命令 | 说明 |
|---|---|
| `fastcms doctor` | 环境探测（Python / conda / 端口 / 数据目录） |
| `fastcms init` | 一键安装：依赖 + `.env` + 数据库（`--server` 为服务端形态） |
| `fastcms server` | 服务端一键部署：生产配置 + `0.0.0.0` + 打印对外地址（`--http` 用于无 HTTPS） |
| `fastcms dev` / `preview` / `test` | 本地开发（热重载）/ 预览 / 独立测试库 |
| `fastcms upgrade` | 拉取最新代码 + 更新依赖 + 迁移 + 基线数据 |
| `fastcms check` | 全部门禁：lint + test + contract + docs（等价 `make check`） |
| `fastcms env [--self-test]` | 打印配置解析结果（单一事实源） |
| `fastcms db` / `seed` / `sync-rbac` / `backup` / `restore` / `scheduler` … | 转发 `app.cli` 业务命令 |
| `fastcms gen` / `export-openapi` / `check-permissions` | 代码生成与契约 |

传统入口仍然可用（等价，内部都走同一套逻辑）：

| 命令 | 说明 |
|---|---|
| `start.cmd` / `./start.sh` | 跨平台一键启动（自动安装依赖、迁移、选择空闲端口） |
| `install.cmd` / `./install.sh` | 跨平台一键安装（环境探测 + 依赖 + `.env` + 数据库初始化） |
| `make env-check` | 只探测环境（Python / Miniconda / 数据库 / 端口 / 页面资源） |
| `make install` | 一键安装环境与数据库 |
| `make dev` / `make preview` | 启动开发服务（热重载 / 关闭热重载） |
| `make server` | 服务端一键部署（无 `.env` 自动生成 production 配置；`HTTP=1` 用于无 HTTPS） |
| `make check` | `gate.py all`：lint（含 tsc）+ test（含前端行为测试）+ contract + docs + security（与 CI / release 同源） |
| `make test` | pytest |
| `make lint` | ruff + mypy |
| `make openapi` | 导出 `openapi.json` |
| `make contract-check` | 重新导出并检查契约、权限覆盖与前后端页面连通性 |
| `make release` | 跑完整 `gate.py all` 门禁后生成 `dist-release/` 发布包 |
| `make backup` | 备份 SQLite |
| `make restore FILE=…` | 恢复 SQLite（覆盖当前数据库） |
| `make db-upgrade` | 执行 Alembic 迁移 |
| `make scheduler` | 以独立进程运行调度器 |
| `make docker-build` / `make docker-up` / `make docker-down` | 容器构建与运行 |

### CLI

`fastcms` 转发到底层 `app.cli`（从任何目录可用，等价于在 `backend/` 下 `python -m app.cli …`）：

```bash
fastcms db upgrade           # 仅迁移（SQLite 自动备份）
fastcms db current           # 当前迁移版本
fastcms seed                 # 同步基线数据
fastcms sync-rbac            # 同步权限与内置角色
fastcms reset-password admin --random
fastcms backup [--output PATH]
fastcms restore PATH --yes
fastcms scheduler            # 独立调度器
fastcms export-openapi
fastcms check-permissions
fastcms gen --table crm_customer --module crm --resource customer --write
```

## 发布

```bash
python scripts/release.py check     # 只跑门禁（gate.py all 五组全部步骤）
python scripts/release.py package   # 门禁通过后打包（--format tar.gz 可换成 tar 包）
python scripts/release.py all       # 门禁 + 打包
make release                        # 等价 all
```

产物为 `dist-release/fastcms-<版本>-<时间>.zip`，内含后端源码（含 `app/web` 下的模板与静态资源）与迁移、`backend/pyproject.toml`、
`scripts/`、`docs/`、`docker/`、`openapi.json`、`.env.example`、`README.md` 以及 `install.*` / `start.*` / `fastcms*` 入口脚本；
不含 `backend/.venv`、数据库与上传文件。同目录的 `manifest.txt` 记录文件清单、SHA256 与发布配置提醒。
目标机器解包后直接 `./fastcms server`（或 `install.cmd` / `./install.sh --prod`，再 `start.cmd preview`）即可。

发布前 `.env` 至少要改这几项（`release.py` 会逐项提醒，但不会替你改）：

- `APP_ENV=prod`、`APP_SECRET_KEY` 设为随机长串；
- `INITIAL_ADMIN_PASSWORD` 设为强口令——生产环境沿用默认口令时 `init` 与启动都会直接失败；
- 多进程时 `APP_WORKERS>1` 必须同时 `SCHEDULER_MODE=standalone`（嵌入式调度器会在每个 worker 里重复注册任务，启动时直接拒绝）；
- 多 worker 或独立调度器请改用 MySQL，并开启 `REDIS_ENABLED`，否则字典/参数/限流计数只存在于各自进程内；
- `APP_HOST=0.0.0.0` 时务必配合 HTTPS 反向代理，`AUTH_COOKIE_SECURE` 保持默认（非本地环境自动下发 `Secure`）。

运维注意：

- 启动时迁移与基线数据由 `backend/data/bootstrap.lock` 跨进程串行，多 worker 首启不会互相踩 `database is locked`；
- 连续登录失败锁定的账号，列表页会显示 `已锁定`，管理员**重置密码即可同时解锁**，不必等锁定时窗走完；
- 内置「通知消息投递」任务默认是**暂停**状态：渠道未配置时自动跑只会攒失败记录，配好渠道后在任务管理里手动启用；
- 权限变更（角色、菜单、字典）按请求实时重建，不需要用户重新登录，也不需要吊销会话。

## 测试

```bash
make test            # pytest（SQLite，自动创建独立测试库）
make contract-check  # OpenAPI / 权限码 / 菜单路径 / 页面模板与脚本一致性
```

`backend/tests/integration/test_web_mount.py` 覆盖页面挂载：后台/前台 base 下的模板渲染、静态资源、根路径跳转、未知路径的 HTML 404、`ADMIN_BASE=/` 时前台不挂载，以及 API 404 不会被页面 catch-all 吞掉。
`backend/tests/unit/test_launch_scripts.py` 静态校验安装/启动脚本：`env_check` 属性存在性、发布清单完整、脚本中不再残留 pnpm/Node 依赖。

端到端手测用 `python scripts/start.py test` 起一个独立实例（独立测试库、固定口令 `admin / fast_cms_123`、单端口），
在浏览器里走一遍登录 → 各菜单 → 保存/删除即可，不会碰开发库。

## Docker 与 Compose

单容器（SQLite + 内置调度器，数据卷持久化）。镜像只有 Python 运行时一层，不再有 Node 构建阶段：

```bash
docker build -f docker/Dockerfile -t fastcms:local .
docker compose -f docker/compose.yml up -d --build
```

可选 profiles：

```bash
# MySQL
DB_URL=mysql+aiomysql://fastcms:fastcms@mysql:3306/fastcms?charset=utf8mb4 \
  docker compose -f docker/compose.yml --profile mysql up -d --build

# Redis
REDIS_ENABLED=true docker compose -f docker/compose.yml --profile redis up -d --build

# 独立调度器（API 使用 SCHEDULER_MODE=standalone）
SCHEDULER_MODE=standalone \
  docker compose -f docker/compose.yml --profile scheduler up -d --build

# 完整扩展形态
docker compose -f docker/compose.yml --profile full up -d --build
```

容器以非 root 用户运行，根文件系统只读，数据根目录（默认 `/app/backend/data`）为持久卷，`/tmp` 为 tmpfs。入口脚本在 `AUTO_MIGRATE=true` 时执行迁移与幂等种子。搬迁数据只需把卷挂载到新目录并设 `DATA_DIR` 指向同一路径（`DB_URL` / `STORAGE_LOCAL_DIR` 留空时由它推导，卷挂载点默认跟随 `DATA_DIR`），容器内其余代码保持只读不动。

## 文档

- [架构梳理与优化方案](docs/history/架构梳理与优化方案.md) — 现状架构、问题诊断与分阶段改进计划
- [认证与权限](docs/认证与权限.md)
- [主题模板](docs/主题模板.md)
- [定时任务](docs/定时任务.md)
- [消息通知](docs/消息通知.md)
- [代码生成器](docs/代码生成器.md)
- [部署与升级](docs/部署与升级.md)
- [API 规范](docs/API规范.md)
- [开发计划](docs/开发计划.md)

## 安全与边界

- 默认管理员 `admin / fast_cms_123` 仅用于本地开发；生产环境（`APP_ENV=prod|production`）拒绝以默认口令初始化，且首次登录强制改密。密码使用 Argon2id；刷新令牌仅存哈希并轮换，重用会撤销会话。
- 口令强度统一由系统参数 `security.password_min_length`（默认与下限 8）驱动，另要求同时包含字母和数字、不包含用户名、不在弱口令黑名单内；创建用户、重置密码、自助改密与 CLI 共用同一校验，公开参数会把策略同步给前端表单，避免前后端规则分裂。
- 抗账号枚举：用户不存在时同样执行一次 Argon2 校验使响应时间一致；只有密码正确后才提示 423 锁定，用错误口令试探锁定账号得到的是 401。
- 敏感接口按窗口限流（登录、刷新、改密、重置密码、上传、头像、导出、日志清理、代码生成写库），返回 429 并带 `Retry-After`；已登录接口按 token `sub` 计数，共享出口 IP 的账号互不影响。
- 客户端 IP 默认只取直连来源，`TRUST_PROXY=true` 才采用 `X-Forwarded-For`，防止伪造头绕过限流、污染审计；refresh cookie 的 `Secure` 在非本地环境默认开启。
- 后端权限校验是最终判定，前端菜单/按钮隐藏只影响体验（页面上的 `data-perm` 由 `applyPermissions()` 隐藏）。
- 管理类接口统一受 `require_admin`（`system:admin:access`）保护，未开通该权限的角色即使拥有业务权限码也无法访问后台接口；升级到该版本前的自定义角色需在「角色管理 → 权限」中勾选「访问管理后台」。
- 数据范围在服务端生效：部门树、在线会话、强制下线、登录/操作日志与其导出都按当前角色范围过滤，范围外的写操作返回 403（`SYSTEM_DEPT_FORBIDDEN`、`MONITOR_KICK_FORBIDDEN`）；角色、菜单、字典、参数、公告属于配置类资源，保持全局可见，仅由业务权限码控制。
- 审计证据不可被越权销毁：`user_id` 为空的日志（爆破不存在用户名、系统写入）所有人可读，但只有超级管理员或 `data_scope=all` 的角色可清理；角色下拉只下发 `id/name/code`，他人最后登录 IP 需要 `monitor:login-log:list`。
- 调整角色、权限或部门无需撤销会话：每个请求都从数据库重新装载权限与数据范围，变更在下一次请求即时生效。
- 页面不引用任何外部 CDN、字体或图标库：CSS、SVG 图标与 JS 全部同源，内网/离线部署不会因为外链超时而白屏。
- 默认不连接外部服务；显式启用 Redis 后连接失败即启动失败。
- Python 脚本任务默认仅超级管理员可用（`schedule:job:code`），在隔离子进程中执行，可随时通过 `SCHEDULER_PYTHON_ENABLED=false` 全局关闭。
- 通知渠道的 webhook 地址自带 access_token，按凭证对待：接口与审计日志只回显脱敏结果，密钥只写不读；推送地址强制 HTTPS（本地环境可 HTTP）、禁止账号密码段与锚点、默认拒绝内网/回环/保留网段，且关闭重定向跟随，避免把后端变成内网探测器。
- SQLite 适合单实例、读多写少场景；多 worker 请使用 MySQL + `SCHEDULER_MODE=standalone`。
- 首版不包含多租户、工作流、插件热插拔、对象存储与邮件/短信。
