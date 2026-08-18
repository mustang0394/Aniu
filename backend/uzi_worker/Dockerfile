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

# 不再锁定固定 commit：每次构建都从 GitHub 拉取 main 分支最新提交。
# 同时不再做 SHA256 校验（最新 commit 持续滚动，无法预置固定摘要）。
# 代价：上游被篡改/误推坏代码会直接进入镜像；换取"始终最新"。
# 如需回到可复现的固定版本构建，改回 ARG UZI_COMMIT=<sha> 并恢复
# uzi-source.lock 的 sha256 校验逻辑即可。
ARG UZI_REPO=wbh604/UZI-Skill
ARG UZI_LATEST_API=https://api.github.com/repos/${UZI_REPO}/commits?per_page=1
ARG UZI_ARCHIVE_URL=https://github.com/${UZI_REPO}/archive

WORKDIR /opt

# 需要 curl 与校验工具（构建阶段临时安装）。
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates tar \
    && rm -rf /var/lib/apt/lists/*

COPY uzi-source.lock /opt/uzi-source.lock

# 动态拉取上游最新 commit 并下载归档（不校验 SHA256）。
# 把实际 commit 与归档 sha256 写回 /opt/uzi-source.lock，供运行时
# ensure_initialized 写入 .aniu-uzi-source.json，使 Worker 显示的
# 版本与镜像内源码保持一致。
RUN set -eux; \
    UZI_COMMIT="$(curl -fsSL -H 'Accept: application/vnd.github+json' -H 'User-Agent: Aniu-UZI-Worker' "${UZI_LATEST_API}" | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["sha"])')"; \
    echo "UZI latest commit: ${UZI_COMMIT}"; \
    curl -fsSL "${UZI_ARCHIVE_URL}/${UZI_COMMIT}.tar.gz" -o /opt/uzi.tar.gz; \
    ARCHIVE_SHA256="$(sha256sum /opt/uzi.tar.gz | awk '{print $1}')"; \
    export UZI_COMMIT ARCHIVE_SHA256 UZI_ARCHIVE_URL; \
    python3 -c 'import json,os; p="/opt/uzi-source.lock"; d=json.load(open(p)); c=os.environ["UZI_COMMIT"]; s=os.environ["ARCHIVE_SHA256"]; u=os.environ["UZI_ARCHIVE_URL"]; d["commit"]=c; d["sha256"]=s; d["archive_url"]=u+"/"+c+".tar.gz"; json.dump(d, open(p,"w"), ensure_ascii=False, indent=2)'; \
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
    UZI_SOURCE_ROOT=/app/data/uzi_source \
    UZI_BUNDLED_SOURCE_ROOT=/opt/uzi \
    UZI_REPORT_ROOT=/app/data/uzi_reports \
    UZI_WORKER_PORT=9001 \
    UZI_DEPTH=deep \
    UZI_LITE=0 \
    UZI_NO_AUTO_OPEN=1 \
    UZI_PLAYWRIGHT_ENABLE=1 \
    UZI_STAGE2_TIMEOUT_SECONDS=600 \
    UZI_RENDER_TIMEOUT_SECONDS=90 \
    UZI_QUANT_MAX_FUNDS=12 \
    UZI_QUANT_TIMEOUT_SECONDS=45

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
# 复制阶段 1 动态写回实际 commit 的 lock（不要 COPY 仓库原始空 lock，
# 否则 current_commit fallback 会返回 unknown）。
COPY --from=uzi-source /opt/uzi-source.lock ./uzi-source.lock

# 固定上游源码（含 LICENSE）。
COPY --from=uzi-source /opt/uzi /opt/uzi

RUN mkdir -p /app/data/uzi_reports /app/data \
    && test -d /opt/uzi

EXPOSE 9001

VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python3 -c "import json, urllib.request; \
    data = json.load(urllib.request.urlopen('http://127.0.0.1:9001/internal/health', timeout=4)); \
    assert data['status'] == 'ok'" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9001"]
