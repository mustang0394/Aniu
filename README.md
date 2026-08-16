<div align="center">

# Aniu——科技牛牛带你狠狠干A股

<img width="120" alt="Aniu icon" src="./frontend/public/aniu.ico" />

**面向 A 股的智能分析与模拟交易系统**

[![Stars][stars-shield]][repo-link]
[![Forks][forks-shield]][repo-link]
[![Issues][issues-shield]][issues-link]
[![License][license-shield]][license-link]

</div>

<!-- banner image removed: docs/banner.png was deleted from the repository -->

---

### 核心特性

- **AI 分析** — 任务执行与结果可视化展示
- **AI 聊天** — 与系统进行自然语言对话
- **账户总览** — 持仓 / 委托 / 交易实时展示
- **定时调度** — 自动任务配置与执行
- **UZI 深度报告** — 独立 Worker 生成的个股深度研究报告
- **一键部署** — Docker Compose 发布，开箱即用

### 技术栈

- **前端** — Vue 3 + Vite + Pinia
- **后端** — FastAPI + SQLAlchemy + SQLite
- **发布** — Docker 多阶段构建，单容器同时提供前端资源与后端 API

---

### 前提条件

下载东方财富 APP，首页搜索「妙想 Skills」立即领取。点击 APP 下方交易 → 上方模拟，领取 20 万元模拟资金。回到妙想 Skills 界面，下滑找到「妙想模拟组合管理」skill，绑定模拟组合，将 API Key 保存到程序设置界面。

> 妙想相关技能使用有限额。

---

### 快速部署（Docker）

#### 1. 准备环境模板

```bash
cp .env.docker.example .env.docker
```

#### 2. 设置登录密码

编辑 `.env.docker`：

```text
APP_LOGIN_PASSWORD=your-password
```

#### 3. 启动服务

**方式一：docker compose（推荐，含 UZI Worker）**

```bash
docker compose pull && docker compose up -d
```

> 若使用 UZI 深度报告，请同时确保 `UZI_WORKER_SHARED_SECRET` 已配置（见下方「UZI 深度报告」小节），并保持 `docker compose up -d aniu aniu-uzi-worker` 同时拉起两个服务。

**方式二：docker run（仅主服务，不带 UZI Worker）**

```bash
docker pull ghcr.io/anacondakc/aniu:latest

docker run -d \
  --name aniu \
  -p 8000:8000 \
  --env-file .env.docker \
  -v "$(pwd)/data:/app/data" \
  ghcr.io/anacondakc/aniu:latest
```

#### 4. 登录并配置

访问 `http://<主机IP>:8000`，使用密码登录后，在「功能设置」中填写：

- `OpenAI API Key`
- `OpenAI Base URL`
- `OpenAI Model`
- `妙想密钥`

保存后即可使用 AI 分析与妙想工具。

---

### 本地开发

#### 环境要求

- Node.js 20+
- Python 3.12 / 3.13+

#### 后端启动

```bash
cd backend
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
cp .env.example .env
./.venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

默认地址：`http://127.0.0.1:8000`

#### 前端启动

```bash
cd frontend
npm install
npm run dev
```

默认地址：`http://127.0.0.1:3003`

> Vite 开发时会自动将 `/api` 和 `/health` 代理到后端 `8000` 端口。

---

### 项目结构

```text
Aniu/
├── backend/              # FastAPI 后端
│   ├── app/
│   ├── tests/
│   └── requirements.txt
├── frontend/             # Vue 3 前端
│   ├── public/
│   └── src/
├── docs/                 # 文档与展示素材
├── Dockerfile
├── docker-compose.yml
└── .env.docker.example
```

---

### 接口说明

- API 前缀：`/api/aniu`
- 健康检查：`GET /health`

常用端点：

```text
POST /api/aniu/login
GET  /api/aniu/settings
GET  /api/aniu/runs
GET  /api/aniu/runtime-overview
```

---

### 配置说明

#### 关键环境变量

| 变量 | 说明 |
|------|------|
| `APP_LOGIN_PASSWORD` | 登录密码（必填） |
| `ANIU_IMAGE_TAG` | 镜像标签，默认 `latest` |
| `JWT_SECRET` | 未设置时自动生成，建议固定以保持登录态稳定 |
| `CORS_ALLOW_ORIGINS` | 默认 `*`，正式环境建议设为具体域名 |

> OpenAI 与妙想相关配置无需写入环境变量，推荐首次登录后在「功能设置」页面中保存，减少部署维护成本。

#### 数据持久化

- 默认数据库：`/app/data/aniu.sqlite3`
- 宿主机挂载：`./data:/app/data`
- 兼容旧版本 `aniu.db` 文件，自动识别并继续使用
- 镜像内置交易日历缓存 `backend/app/data/trading_calendar.json`，降低首次启动因远程接口异常导致的失败风险

> 使用 `docker run` 时请务必挂载数据卷，否则容器重建后数据丢失。

---

### UZI 深度报告

UZI 深度报告是 AniU 的独立业务模块：主服务负责任务状态、数据库、LLM 深度评审与历史报告管理；独立 Worker 负责数据采集（Stage 1）、报告渲染（Stage 2）与 Chromium 运行。Worker 与主服务通过共享数据卷 `./data:/app/data` 交换产物，通过内部 HTTP 接口（带共享密钥）通信。

#### 架构边界

- UZI 重依赖（AkShare、Pandas、Playwright、Chromium）**只存在于 Worker 镜像**，主镜像不安装（文档 §18.2）。
- Worker 不访问 AniU SQLite，不持有 LLM API Key；LLM 深度评审由主服务使用「功能设置」中的当前模型配置执行（文档 §13.1）。
- Worker 端口不映射到宿主机，只允许 Docker 内部访问。

#### 1. 配置共享密钥

编辑 `.env.docker`，设置一个足够随机的共享密钥（必填，无默认弱值）：

```text
UZI_WORKER_SHARED_SECRET=replace-with-a-long-random-secret
```

由于 `docker-compose.yml` 通过变量插值把该值注入 Worker 的 `UZI_WORKER_TOKEN`，还需要把同一值写入项目根目录 `.env` 文件（或 shell `export`）：

```bash
# 项目根目录 .env（docker compose 变量插值读取该文件）
UZI_WORKER_SHARED_SECRET=replace-with-a-long-random-secret
```

> 未配置密钥时 Worker 除健康检查外拒绝一切任务，主服务正常启动但 UZI 生成按钮不可用——这是安全默认，不会使用弱值。

#### 2. 启动双服务

```bash
docker compose up -d aniu aniu-uzi-worker
```

验证：

```bash
curl http://127.0.0.1:8000/api/aniu/uzi/status   # 需要登录后调用，返回 worker_available=true
```

#### 3. 旧单容器部署兼容（文档 §18.3）

不带 Worker 部署时：

- 主服务照常启动，不影响现有分析与交易功能；
- `UZI_ENABLED=false` 或 Worker 不可达时，`/api/aniu/uzi/status` 返回不可用状态；
- 前端「UZI报告」菜单保留，生成按钮禁用并提示「UZI Worker 未配置」。

#### 4. 开发隔离（文档 §19）

- 主后端使用 `backend/.venv`：`cd backend && python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt`；
- Worker 使用**独立**虚拟环境 `backend/uzi_worker/.venv`，与主后端不得互相复用：

  ```bash
  cd backend/uzi_worker
  python3 -m venv .venv
  ./.venv/bin/python -m pip install -r requirements.lock
  PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright" \
    ./.venv/bin/python -m playwright install chromium
  ```

- 宿主机不执行 `playwright install --with-deps`；缺少系统动态库时使用 Worker Docker 镜像调试；
- 禁止 `sudo pip`、`pip install --user`、全局 `npm install -g`；锁文件生成工具只装在临时或 Worker 虚拟环境；
- 真实联调优先 `docker compose build aniu-uzi-worker && docker compose up aniu aniu-uzi-worker`，测试数据目录使用项目 `data/` 或 `mktemp -d`。

#### 5. 真实烟雾测试

按 `docs/uzi-smoke-test-checklist.md` 执行：使用固定股票代码（如 `600519.SH`）完成 Stage 1 → LLM 评审 → Stage 2 全流程，校验 HTML/PNG/summary 产物、分析与交易模式下的 `uzi_get_report_context` 工具行为，最后删除报告确认文件与记录消失。

---

### 验证命令

```bash
# 前端构建
cd frontend && npm run build

# 后端测试
cd backend && ./.venv/bin/pytest

# 健康检查
curl http://127.0.0.1:8000/health

# 登录接口
curl -X POST http://127.0.0.1:8000/api/aniu/login \
  -H "Content-Type: application/json" \
  -d '{"password":"your-password"}'
```

---

### 镜像发布

仓库包含 GitHub Actions 工作流 `.github/workflows/publish-image.yml`：

- 推送 `main` 分支 → 发布主镜像 `ghcr.io/anacondakc/aniu:latest` 及 SHA 标签；
- 推送 `v1.0.0` 格式 tag → 发布对应版本镜像并自动创建 Release；
- **同时构建并发布 UZI Worker 镜像** `ghcr.io/Mustang0394/aniu-uzi-worker`，标签与主镜像保持一致（同一 Git SHA 或版本号）；
- `docker-compose.yml` 默认拉取 `ghcr.io/anacondakc/aniu:${ANIU_IMAGE_TAG:-latest}` 与 `ghcr.io/Mustang0394/aniu-uzi-worker:${ANIU_IMAGE_TAG:-latest}`。

> 注意：仓库 CI 实际发布路径为 `Mustang0394/aniu`，README 示例中的 `anacondakc/aniu` 为历史示例；若使用镜像发布后的双服务部署，请以 CI 实际路径为准。

---

### License

[MIT](./LICENSE)

---

### 致谢

本项目使用了东方财富的妙想接口，感谢 [东方财富](https://www.eastmoney.com/)。

本项目开发使用了公益站，感谢 [LINUX DO](https://linux.do/t/topic/1987329) 社区的支持。

---

### Star History

[![Star History Chart](https://api.star-history.com/svg?repos=AnacondaKC/Aniu&type=Date)](https://www.star-history.com/#AnacondaKC/Aniu&Date)

<!-- LINK GROUP -->

[repo-link]: https://github.com/AnacondaKC/Aniu
[issues-link]: https://github.com/AnacondaKC/Aniu/issues
[license-link]: ./LICENSE
[stars-shield]: https://img.shields.io/github/stars/AnacondaKC/Aniu?color=ffcb47&labelColor=black&style=flat-square
[forks-shield]: https://img.shields.io/github/forks/AnacondaKC/Aniu?color=8ae8ff&labelColor=black&style=flat-square
[issues-shield]: https://img.shields.io/github/issues/AnacondaKC/Aniu?color=ff80eb&labelColor=black&style=flat-square
[license-shield]: https://img.shields.io/github/license/AnacondaKC/Aniu?color=c4f042&labelColor=black&style=flat-square
