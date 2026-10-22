# syntax=docker/dockerfile:1.7
# 前后台页面都由 FastAPI 用 Jinja2 直接渲染，随 Python 源码一起进镜像，
# 因此没有 Node 构建阶段，也没有需要单独拷贝的 dist 产物。

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# slim 镜像不带时区库，而系统时区（Asia/Shanghai）在启动时就要校验；装上后 zoneinfo 才查得到。
# TZ 只影响容器里 C 库的本地时间，应用展示与调度一律按 system.timezone 参数走。
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
ENV TZ=Asia/Shanghai

RUN groupadd --system fastcms && useradd --system --gid fastcms --home-dir /app fastcms

WORKDIR /app
COPY backend/ /app/backend/
RUN pip install -e /app/backend \
    && mkdir -p /app/backend/data/uploads \
    && chown -R fastcms:fastcms /app/backend/data /app/backend

COPY docker/entrypoint.sh /usr/local/bin/fastcms-entrypoint
RUN chmod +x /usr/local/bin/fastcms-entrypoint

ENV APP_ENV=production \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    ADMIN_BASE=/admin \
    PORTAL_BASE=/front \
    DB_URL=sqlite+aiosqlite:////app/backend/data/fastcms.db \
    STORAGE_LOCAL_DIR=/app/backend/data/uploads \
    AUTO_MIGRATE=true

USER fastcms
WORKDIR /app/backend
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"

ENTRYPOINT ["/usr/local/bin/fastcms-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
