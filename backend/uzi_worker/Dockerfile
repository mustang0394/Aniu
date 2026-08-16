# syntax=docker/dockerfile:1
# =============================================================================
# aniu-uzi-worker 独立镜像
#
# 设计约束（文档 §12.1 / §18）：
# - Chromium 与 Playwright 只在镜像构建阶段安装，运行时禁止下载浏览器。
# - UZI 上游源码以固定 commit 归档下载并校验 SHA256（构建阶段）。
# - 依赖只来自 requirements.lock；不依赖上游未锁定 requirements。
# - 主镜像（项目根 Dockerfile）不安装 UZI 依赖，两个镜像互不混装。
# =============================================================================

# ── 阶段 1：获取固定 commit 的 UZI 源码 ─────────────────────
FROM python:3.12-slim AS uzi-source

ARG UZI_COMMIT=7bc779dd15ca4a741fcda20319a431f283232366
ARG UZI_ARCHIVE_URL=https://github.com/wbh604/UZI-Skill/archive/${UZI_COMMIT}.tar.gz

WORKDIR /opt

# 需要 curl 与校验工具（构建阶段临时安装）。
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates tar \
    && rm -rf /var/lib/apt/lists/*

COPY uzi-source.lock /opt/uzi-source.lock

# 下载固定 commit 归档并校验 SHA256（文档 §12.1）。
# 当 uzi-source.lock 中 sha256 为空时：打印实际计算值供补全，
# 仅在构建参数 UZI_ALLOW_MISSING_SHA256=1 时放行（联调用），否则构建失败。
ARG UZI_ALLOW_MISSING_SHA256=0
RUN set -eux; \
    curl -fsSL "${UZI_ARCHIVE_URL}" -o /opt/uzi.tar.gz; \
    SHA256_EXPECTED="$(python3 - <<'PY'
import json
payload = json.load(open('/opt/uzi-source.lock'))
print(payload.get('sha256') or '')
PY
)"; \
    ACTUAL_SHA256="$(sha256sum /opt/uzi.tar.gz | awk '{print $1}')"; \
    if [ -n "${SHA256_EXPECTED}" ]; then \
        echo "${SHA256_EXPECTED}  /opt/uzi.tar.gz" | sha256sum -c -; \
    else \
        echo "警告: uzi-source.lock 未配置 sha256。实际归档 sha256=${ACTUAL_SHA256}"; \
        echo "请在 uzi-source.lock 中补全该值后重新构建。"; \
        if [ "${UZI_ALLOW_MISSING_SHA256}" != "1" ]; then \
            echo "错误: sha256 缺失且未设置 UZI_ALLOW_MISSING_SHA256=1，构建中止。"; \
            exit 1; \
        fi; \
        echo "UZI_ALLOW_MISSING_SHA256=1，跳过校验继续（仅限联调，禁止用于发布）。"; \
    fi; \
    mkdir -p /opt/uzi; \
    tar -xzf /opt/uzi.tar.gz --strip-components=1 -C /opt/uzi; \
    rm -f /opt/uzi.tar.gz

# 保留上游 LICENSE 与版本元数据（文档 §12.1）。
RUN test -f /opt/uzi/LICENSE || echo "UZI 源码中未找到 LICENSE 文件" >&2

# ── 阶段 2：运行镜像 ────────────────────────────────────────
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.source="https://github.com/AnacondaKC/Aniu"
LABEL org.opencontainers.image.description="UZI deep research worker for AniU (isolated runtime)"
LABEL org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MALLOC_ARENA_MAX=2 \
    APP_ENV=docker \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    UZI_SOURCE_ROOT=/opt/uzi \
    UZI_REPORT_ROOT=/app/data/uzi_reports \
    UZI_WORKER_PORT=9001 \
    UZI_DEPTH=deep \
    UZI_LITE=0 \
    UZI_NO_AUTO_OPEN=1 \
    UZI_PLAYWRIGHT_ENABLE=1

WORKDIR /app/worker

# Chromium 运行时系统依赖（构建阶段安装，不允许运行时 `playwright install`）。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl ca-certificates \
        libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
        libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
        libgbm1 libasound2 libpango-1.0-0 libcairo2 libatspi2.0-0 \
        libx11-xcb1 libxcb1 libxcb-dri3-0 libxshmfence1 \
        fonts-liberation fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# 锁定依赖（禁止直接使用上游未锁定 requirements）。
COPY requirements.lock ./requirements.lock
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.lock \
    && python -m playwright install chromium \
    && rm -rf /root/.cache/pip

# Worker 应用代码。
COPY app ./app
COPY uzi-source.lock ./uzi-source.lock

# 固定上游源码（含 LICENSE）。
COPY --from=uzi-source /opt/uzi /opt/uzi

RUN mkdir -p /app/data/uzi_reports \
    && test -d /opt/uzi

EXPOSE 9001

VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python3 -c "import json, urllib.request; \
    data = json.load(urllib.request.urlopen('http://127.0.0.1:9001/internal/health', timeout=4)); \
    assert data['status'] == 'ok'" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9001"]