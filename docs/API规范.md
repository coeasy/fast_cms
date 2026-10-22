# API 规范

本文约定 Fast CMS 后端接口的统一格式、认证方式、错误码与契约维护流程。具体字段以运行时 OpenAPI 文档为准：开发环境 `http://localhost:8000/docs`，机器可读定义 `openapi.json`（`make openapi` 重新导出）。

## 1. 基础约定

- 基础路径：默认 `/api/v1`（认证、系统、内容、监控、任务、代码生成均在此前缀下），由环境变量 `API_PREFIX` 配置，本文示例一律沿用默认值；
- 健康检查例外：`/healthz`、`/readyz` 固定不带前缀；
- 请求与响应主体：`application/json; charset=utf-8`；文件上传为 `multipart/form-data`，下载/导出为二进制流；
- JSON 字段统一 `snake_case`，与 Python 模型一致；
- 时间字段为 UTC，ISO 8601 字符串，通常精确到秒并以 `Z` 结尾，例如 `2026-01-09T01:30:00Z`；接口不做时区换算，展示时区由系统参数 `system.timezone` 决定（见下）；
- 查询时间参数同样使用 ISO 8601；带时区的值会被正确转换，未带时区按 UTC 理解；
- 时区约定：数据库与传输层固定 UTC，只有面向人的输出按 `system.timezone` 换算——页面展示、日志时间戳、CSV 导出、通知正文。匿名可读的 `GET /api/v1/meta/version` 返回 `timezone` 字段（当前系统时区）和 UTC 的 `server_time`，前端据此渲染；
- 写入接口的例外是任务时间：任务的 `run_at` 由前端按该任务的 `timezone` 把墙上时间换算成 UTC ISO 后提交，后端只接收 UTC；
- 主键为整数（`int`），路径参数如 `/system/users/{user_id}`；
- 成功创建资源返回 `200`，不单独使用 `201`；
- 受保护接口支持并回写 `X-Request-ID`；客户端可传入自定义值（最长 64 字符），否则服务端生成 UUID hex。

## 2. 统一响应

### 2.1 成功

```json
{
  "code": "OK",
  "message": "success",
  "data": { "id": 1, "username": "admin" },
  "request_id": "0f3c6f0e8f2a4d7c9b1e5a6c7d8e9f01"
}
```

前端请求层只把 `code === "OK"` 视为成功并返回 `data`；其余情况抛出 `ApiError`。

### 2.2 分页

列表接口的 `data` 固定为：

```json
{
  "items": [],
  "page": 1,
  "page_size": 20,
  "total": 0
}
```

### 2.3 错误

```json
{
  "code": "SYSTEM_USER_NOT_FOUND",
  "message": "用户不存在",
  "details": null,
  "request_id": "0f3c6f0e8f2a4d7c9b1e5a6c7d8e9f01"
}
```

错误响应**没有 `data` 字段**。`details` 为可选结构化补充信息，参数校验错误时是数组：

```json
{
  "code": "VALIDATION_ERROR",
  "message": "请求参数校验失败",
  "details": [
    { "field": "username", "message": "String should have at least 3 characters", "type": "string_too_short" }
  ],
  "request_id": "..."
}
```

排查线上问题时优先记录 `request_id`，可直接关联服务端日志与操作日志。

## 3. HTTP 状态码

| 状态码 | 含义 | 典型场景 |
|---|---|---|
| 200 | 成功 | 查询、创建、更新、删除、操作提交 |
| 400 | 请求不合法 | 非法路径、无法处理的业务输入 |
| 401 | 未认证 / 凭证失效 | 缺少 token、token 过期、会话撤销、refresh 复用 |
| 403 | 已认证但无权限 | 缺少权限码、数据范围拒绝、首次登录未改密 |
| 404 | 资源不存在 | 用户、角色、公告、任务、文件不存在 |
| 409 | 冲突 | 用户名/角色编码重复、目标目录已存在、内建资源受保护 |
| 422 | 参数校验失败 | Pydantic 校验、业务语义校验（如系统表不允许生成代码） |
| 429 | 限流 | 同一 IP 登录尝试过于频繁 |
| 500 | 未处理异常 | 服务端错误，响应带 `INTERNAL_ERROR` 与 `request_id` |
| 503 | 未就绪 | `/readyz` 数据库不可达或迁移版本不一致 |

## 4. 认证与会话

### 4.1 登录

```http
POST /api/v1/auth/login
Content-Type: application/json

{ "username": "admin", "password": "********" }
```

响应 `data`：

```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 900,
  "must_change_password": false,
  "user": { "id": 1, "username": "admin" }
}
```

refresh token 不放在响应体中，而是写入 HttpOnly Cookie（默认名 `fastcms_refresh`，`Path=/api/v1/auth`，`SameSite=Lax`）。生产开启 HTTPS 时应设置 `AUTH_COOKIE_SECURE=true`。

### 4.2 调用受保护接口

```http
GET /api/v1/system/users?page=1&page_size=20
Authorization: Bearer <access_token>
```

### 4.3 刷新

```http
POST /api/v1/auth/refresh
Cookie: fastcms_refresh=<token>
```

- 每次刷新都会轮换 refresh token，并在 `sys_session` 记录上一枚 token 的哈希；
- 旧 token 再次使用会被判定为复用：该会话立即撤销，返回 `AUTH_REFRESH_REUSED`；
- 响应返回新的 `access_token`，并通过 `Set-Cookie` 下发新的 refresh token；
- 刷新失败会清除 Cookie；`401` 时前端应跳转登录页。

### 4.4 退出与当前用户

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/auth/logout` | 撤销当前会话并清除 Cookie |
| GET | `/api/v1/auth/me` | 用户信息、权限码集合、是否超管、菜单树、当前会话 |
| POST | `/api/v1/auth/heartbeat` | 更新会话活跃时间 |
| GET | `/api/v1/auth/sessions` | 我的登录会话 |
| DELETE | `/api/v1/auth/sessions/{session_id}` | 撤销我的指定会话 |
| DELETE | `/api/v1/auth/sessions` | 撤销我的其他会话 |

### 4.5 首次登录强制改密

初始化管理员与重置密码后的用户，`must_change_password=true`。此时除以下白名单外，所有受保护接口返回 `403 AUTH_PASSWORD_CHANGE_REQUIRED`：

- `/api/v1/auth/me`
- `/api/v1/auth/logout`
- `/api/v1/auth/change-password`
- `/api/v1/auth/heartbeat`
- `/api/v1/auth/profile`（及头像相关接口）
- `/api/v1/auth/refresh`

改密成功会使 `token_version` 递增并撤销其他会话；当前会话保留，响应会返回新的 `access_token`，客户端应立即替换内存中的 token（旧 token 随即失效）。

### 4.6 401 与 403 的区别

- `401`：身份不成立（未带 token、token 无效/过期、会话撤销、账号禁用）；
- `403`：身份成立但操作被拒绝（缺权限码、数据范围不允许、首次改密限制、内建资源保护等）。

前端收到 `401` 时先尝试一次静默刷新，刷新失败再跳转登录；`403` 只提示无权限，不跳登录。

## 5. 权限模型

权限码格式：`{module}:{resource}:{action}`，例如 `system:user:create`、`schedule:job:trigger`、`tool:codegen:generate`。

- 每个受保护路由在 OpenAPI 中带有 `x-permission`，并由路由层自动注入权限依赖；
- 超级管理员绕过权限码判断；
- 普通用户权限来自启用角色绑定的权限码，禁用角色不计入；
- 数据范围（全部/本部门/本部门及以下/仅自己/指定部门）在服务层应用，权限码通过后仍可能因范围被拒绝或查询不到目标数据；
- 前端权限码来自登录后的 `/auth/me`（后端按角色实时计算），页面用 `can('system:user:create')` 判断、用 `data-perm` 属性批量隐藏按钮，不要在前端硬编码权限清单。

路由清单与权限映射可直接查看 OpenAPI 的 `x-permission` 字段，以及 [认证与权限.md](认证与权限.md)。

## 6. 分页、筛选与排序

通用分页参数：

| 参数 | 默认 | 约束 | 说明 |
|---|---|---|---|
| `page` | 1 | ≥ 1 | 页码 |
| `page_size` | 20 | 1–100 | 每页条数，超过 100 返回 422 |

筛选参数按资源定义，常见为 `keyword`、`status`、`job_id`、`start`、`end` 等，均有长度或枚举校验。当前版本不支持通用 `sort`/`order` 参数；列表由服务端固定排序：

- 用户、角色等按 `id` 升序；
- 登录日志、操作日志按 `id` 降序；
- 菜单、字典项按 `sort` 字段升序；
- 任务日志按 `id` 降序（近似开始时间倒序）。

需要其他排序时在业务查询中扩展，不要依赖未声明的参数。

## 7. 错误码

业务错误码按模块前缀分组，HTTP 状态由服务层显式指定。常见族：

| 前缀 | 范围 | 示例 |
|---|---|---|
| `AUTH_*` | 登录、令牌、会话、改密、限流 | `AUTH_INVALID_CREDENTIALS`、`AUTH_SESSION_EXPIRED`、`AUTH_PASSWORD_CHANGE_REQUIRED`、`AUTH_RATE_LIMITED`、`AUTH_REFRESH_REUSED` |
| `SYSTEM_*` | 用户、角色、菜单、部门、权限 | `SYSTEM_USER_NOT_FOUND`、`SYSTEM_USERNAME_EXISTS`、`SYSTEM_LAST_SUPERUSER`、`SYSTEM_ROLE_ESCALATION`、`SYSTEM_MENU_CYCLE` |
| `CONFIG_*` | 字典与系统参数 | `CONFIG_DICT_EXISTS`、`CONFIG_NOT_FOUND`、`CONFIG_BUILTIN` |
| `CONTENT_*` / `TOOL_FILE_*` | 公告与文件 | `CONTENT_NOTICE_NOT_FOUND`、`TOOL_FILE_EMPTY`、`TOOL_FILE_FORBIDDEN`、`TOOL_FILE_MISSING` |
| `AUDIT_*` | 登录/操作日志 | `AUDIT_LOG_NOT_FOUND`、`AUDIT_RETENTION_TOO_SHORT` |
| `SCHEDULE_*` | 任务、模板与执行日志 | `SCHEDULE_JOB_UNKNOWN`、`SCHEDULE_JOB_ARGS_INVALID`、`SCHEDULE_JOB_CODE_FORBIDDEN`、`SCHEDULE_PYTHON_DISABLED`、`SCHEDULE_SCRIPT_INVALID`、`SCHEDULE_TEMPLATE_BUILTIN` |
| `THEME_*` | 主题模板 | `THEME_NOT_FOUND`、`THEME_CODE_EXISTS`、`THEME_BUILTIN`、`THEME_ACTIVE` |
| `NOTIFY_*` | 通知渠道与发送记录 | `NOTIFY_URL_INVALID`、`NOTIFY_CHANNEL_NOT_FOUND`、`NOTIFY_CHANNEL_EXISTS`、`NOTIFY_RECORD_NOT_FOUND`、`NOTIFY_RECORD_SENT`、`NOTIFY_RECORD_SENDING`、`NOTIFY_RECORD_CONFLICT` |
| `CODEGEN_*` | 代码生成 | `CODEGEN_TABLE_SYSTEM`、`CODEGEN_COMPOSITE_KEY`、`CODEGEN_TARGET_EXISTS` |
| `VALIDATION_ERROR` | 请求参数 | `422`，`details` 为字段错误数组 |
| `HTTP_*` | 框架层 HTTP 异常 | 如 `HTTP_404`（未匹配路由）、`HTTP_405`；`message` 为中文文案，未在映射表内的状态码回退到框架 `detail` |
| `INTERNAL_ERROR` | 未处理异常 | `500`，不泄露堆栈 |

前端应根据 `code` 做分支，不要依赖 `message` 文案（文案可能调整或国际化）。字段级表单错误可通过 `details[].field` 映射；前端 `fieldErrors()` 已封装该逻辑。

## 8. 文件、下载与导出

### 8.1 上传

```http
POST /api/v1/tool/files/upload
Authorization: Bearer <access_token>
Content-Type: multipart/form-data

file=@report.pdf
```

- 字段名固定为 `file`；
- 大小上限 `UPLOAD_MAX_MB`（默认 20 MB），超限返回错误；
- 存储名为随机值，下载时使用原始文件名；元数据包含 MIME、大小、SHA-256；
- 头像上传为 `POST /api/v1/auth/avatar`，限制为图片类型。

### 8.2 下载

`GET /api/v1/tool/files/{file_id}/download` 返回二进制流，`Content-Disposition` 使用 RFC 5987 编码中文文件名。该响应**不是**统一 JSON 信封。

### 8.3 CSV 导出

`GET /api/v1/monitor/login-logs/export`、`/api/v1/monitor/operation-logs/export` 返回 CSV 流，同样不使用信封，且导出动作会写入操作审计。

前端使用 `getBlob()` / `download()` 处理二进制响应；当服务端因错误返回 JSON 时，这两个函数会解析信封并抛出 `ApiError`。

## 9. 主要资源与路径

| 模块 | 路径前缀 | 说明 |
|---|---|---|
| 认证 | `/api/v1/auth` | 登录、刷新、退出、当前用户、个人资料、头像、会话 |
| 系统 | `/api/v1/system` | 用户、角色、菜单、部门、权限查询 |
| 字典/参数 | `/api/v1/system` | 字典类型/字典项、系统参数、公开配置 |
| 内容 | `/api/v1/content` | 公告管理端（草稿/发布/下线）与用户端（已发布、未读数、已读） |
| 文件 | `/api/v1/tool/files` | 上传、列表、元数据、下载、删除 |
| 审计 | `/api/v1/monitor` | 登录日志、操作日志、在线用户、服务器信息 |
| 任务 | `/api/v1/schedule` | 任务 CRUD、暂停/恢复/触发、下次执行、执行日志 |
| 通知 | `/api/v1/notify` | 渠道 CRUD 与测试、发送记录与重试、记录清理（仅管理员，详见 docs/消息通知.md） |
| 代码生成 | `/api/v1/tool/codegen` | 表列表、元数据、预览、zip 下载、写入 |
| 仪表盘 | `/api/v1/dashboard` | 汇总统计与最近动态 |
| 元信息 | `/api/v1/meta` | 版本与运行环境（公开） |

任务触发类接口返回 `200` 表示“已提交执行”，不保证执行成功；执行结果查询 `/api/v1/schedule/job-logs`。

## 10. 契约与调用点校验

后端 FastAPI 是唯一契约源，前端直接消费同一份契约（页面由后端渲染，无需生成客户端）：

```bash
make openapi        # backend/app/cli.py export-openapi -> openapi.json
make contract-check # 重新导出并检查权限/菜单/页面/脚本一致性
```

检查内容：

- 每个操作都有唯一 `operationId`；
- 受保护路由都声明 `x-permission` 且权限码已在基线注册；
- 无未使用的注册权限码；
- 菜单引用的权限码与页面脚本均存在（`bootstrap/menus.py` ↔ `app/web/router.py` 的 `PAGES` ↔ `static/js/pages/**`）；
- `openapi.json` 的所有路径都落在当前 `API_PREFIX` 下（改前缀后漏做导出会在这一步失败）。

`make contract-check` 末尾还会跑 `scripts/check_api_coverage.py --strict`，逐端点核对前后端是否真的接通：

- 每个 openapi 端点都要有前端调用点（按页面脚本里的 `api.get/post/put/patch/del/upload/download` 与 `static/js/shell/*.js` 外壳调用识别，HTTP 方法必须一致，`${id}` 对应 `{param}`）；
- 前端调用但契约里不存在的路径 = 死链；
- 例外只有两类：`/healthz`、`/readyz` 由探针调用，`POST /auth/refresh` 由请求层直接拼接；
- 单条详情 GET（列表已回整行数据，页面不回查）列在 `REST_DETAIL_ENDPOINTS` 白名单里，脚本会同时校验这些端点仍存在于契约且被 `backend/tests` 请求过，白名单不能用来藏真正的孤儿接口。

页面统一通过 `static/js/core.js` 的 `api.*` 发起请求（自动带 access token、401 时用 HttpOnly refresh cookie 换新 token 并重放原请求），不要在各页面里手写 `fetch`。

## 11. 调用示例

```bash
# 登录并保存 token
# 登录并保存 token（本地默认口令 fast_cms_123，生产环境必须替换）
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"fast_cms_123"}' \
  | python -c 'import json,sys; print(json.load(sys.stdin)["data"]["access_token"])')

# 分页查询用户
curl -s 'http://localhost:8000/api/v1/system/users?page=1&page_size=10' \
  -H "Authorization: Bearer $TOKEN"

# 无权限时的响应（示例）
# HTTP/1.1 403
# {"code":"AUTH_FORBIDDEN","message":"缺少权限：system:user:create",...}
```

## 12. 版本与兼容

- 默认路径前缀 `/api/v1`，可用 `API_PREFIX` 换成其他前缀（不改变接口语义）；破坏性变更需新增 `/api/v2`；
- 前缀改动会影响 refresh cookie 的 `Path`、审计日志路径与响应里的下载地址，前端需按 README「自定义 API 前缀与子路径部署」同步重建；
- 允许的兼容变更：新增可选字段、新增接口、新增错误码、放宽校验；
- 需要同步前端的变更：字段重命名/删除、必填性变化、枚举值语义变化、权限码调整；
- 权限码属于公开契约，废弃时保留注册并标记 `deprecated_at`，不要直接复用旧码表示新语义；
- 发布前执行 `make contract-check`、后端测试、前端类型检查与 E2E。

## 13. 常见问题

| 现象 | 原因与处理 |
|---|---|
| 所有接口 401 | 未带 Bearer token、token 过期、会话被撤销或 `token_version` 变化；先刷新，再重新登录 |
| 登录返回 429 | 同一 IP 每分钟超过 30 次登录请求；等待后重试或排查代理 IP 配置 |
| 业务接口 403 且 code 为 `AUTH_PASSWORD_CHANGE_REQUIRED` | 首次登录/重置密码后未改密；仅改密与个人中心接口可用 |
| 403 `AUTH_FORBIDDEN` | 权限码未分配给角色、角色被禁用，或数据范围不包含目标资源 |
| 422 且 `details` 为数组 | 请求字段校验失败；按 `details[].field` 定位 |
| 下载得到 JSON | 服务端返回了错误信封；用 `getBlob()`/`download()` 解析并提示 |
| 需要按业务字段排序 | 当前未提供通用排序参数，扩展对应服务层查询 |
