# AniU 集成 UZI 深度报告：详细实施规格

> 文档状态：待实施
>
> 面向对象：负责后端、Worker、LLM 编排、内置 Skill、前端和测试的工程模型
>
> 最终复核：由主审模型统一进行代码 Review、集成验证和安全检查
>
> 上游项目：[wbh604/UZI-Skill](https://github.com/wbh604/UZI-Skill)
>
> 锁定基线：`7bc779dd15ca4a741fcda20319a431f283232366`
>
> 最后更新：2026-08-16

## 1. 文档目的

本文档是可直接执行的工程实施规格，不是概念提案。实现模型应按本文档的架构边界、接口契约、状态机、安全约束和验收标准工作，不应自行改变以下核心决策：

1. UZI 作为 AniU 的独立业务模块和一级菜单接入，不作为用户上传的自定义 Skill 运行。
2. UZI 重依赖和 Chromium 放入独立 Worker，不能加入 AniU 主服务 Python 环境。
3. 只实现完整深度分析，不实现 lite、medium、远程隧道、交互式 CLI 等其他 UZI 模式。
4. UZI Stage 1 负责采集与机械评分，AniU 使用当前大模型配置完成深度评审，UZI Stage 2 负责报告生成。
5. 历史报告永久保留，只有用户手动删除时才删除记录和文件。
6. 新增内置只读工具 `uzi_get_report_context`，供 AniU 分析、交易和聊天模式引用历史报告。
7. UZI 报告只能作为带时间戳的研究参考，不能代替本轮实时行情、持仓、资金和委托证据，也不能直接执行交易。
8. 所有本地调试必须使用项目级虚拟环境或容器，严禁污染系统 Python、全局 Node 或全局 Playwright 环境。

## 2. 范围与非目标

### 2.1 本期范围

- 新增 UZI 报告生成任务。
- 新增任务进度、失败原因、取消和重启恢复。
- 新增历史报告列表、详情、HTML 预览和文件下载。
- 新增手动删除历史报告。
- 新增 AniU 内置 Skill `uzi_report_context`，提供工具 `uzi_get_report_context`。
- 新增独立 `aniu-uzi-worker` Docker 服务。
- 新增 UZI 专用大模型编排流程，并复用 AniU 当前 LLM 配置。
- 新增自动化测试、容器烟雾测试和运维文档。

### 2.2 明确不做

- 不把完整 UZI 仓库导入 `data/skill_workspace/skills/`。
- 不通过 `builtin_utils.exec` 执行完整 UZI 分析。
- 不允许 UZI 运行时自动 `pip install`、安装浏览器或下载 Cloudflare Tunnel。
- 不提供深度级别选择，公开 API 和 UI 中都没有 `lite`、`medium`、`deep` 参数。
- 不提供定时自动生成 UZI 报告。
- 不提供报告公开分享链接。
- 不让 Worker 直接访问 AniU SQLite。
- 不让 Worker 持有 AniU 大模型 API Key。
- 不根据报告结论自动下单。
- 不在本期引入 Alembic、Redis、Celery、RabbitMQ 或新的前端 UI 框架。

## 3. 当前项目约束

实现前必须理解并保持以下既有约束：

- 主后端为 FastAPI + SQLAlchemy + SQLite，入口是 `backend/app/main.py`。
- 所有业务 API 当前统一挂载在 `/api/aniu`。
- 登录鉴权使用 JWT Bearer；新增 UZI 公共 API 必须沿用现有鉴权依赖。
- 数据库通过 `Base.metadata.create_all` 和内联迁移初始化，目前没有 Alembic。
- Skill 由 `backend/app/skills/` 运行时加载，内置 Skill 位于 `backend/skills/`。
- 现有运行类型为 `analysis`、`trade`、`chat`；交易工具的物理隔离依赖工具列表过滤。
- 前端为 Vue 3 + Vite，导航定义在 `frontend/src/config/navigation.ts`。
- 前端 API 封装集中在 `frontend/src/services/api.ts`，JWT 存储和请求约定不得另起一套。
- 当前 Docker 镜像较轻，不包含 AkShare、Pandas、Playwright 或 Chromium。
- UZI 完整流程通常超过现有通用 Skill `exec` 的 60 秒限制，并且会产生大量文件，因此不能直接套用自定义 Skill 导入路径。

## 4. 总体架构

```text
浏览器
  │
  │ JWT / REST / SSE
  ▼
AniU FastAPI
  ├── UZI 公共 API
  ├── UziReportService
  ├── UziLlmOrchestrator
  ├── SQLite: UziReportJob
  ├── UZI Event Bus
  └── uzi_get_report_context Skill
          │
          │ 内部 HTTP + 共享密钥
          ▼
aniu-uzi-worker
  ├── UZI Stage 1
  ├── 独立任务工作目录
  ├── UZI Stage 2
  └── Playwright / Chromium

共享数据卷
  /app/data/uzi_reports/{report_id}/
```

职责边界：

- AniU 主服务是任务状态、数据库、鉴权、LLM 调用和历史报告的唯一业务所有者。
- Worker 是无业务数据库的执行节点，只接受受控任务、运行 UZI、写入共享目录并返回状态。
- 前端只访问 AniU 主服务，绝不直接访问 Worker。
- 内置 Skill 只读取 AniU 已归一化并已完成的报告数据，不读取 Worker 状态，也不解析 HTML。

## 5. 完整任务流程

### 5.1 创建任务

1. 前端调用 `POST /api/aniu/uzi/reports`，只提交股票代码或股票名称。
2. 后端验证 LLM 配置、Worker 可用性、队列容量和输入格式。
3. 若同一标准化股票已有非终态任务，返回该任务并标记 `reused=true`。
4. 否则创建 `UziReportJob`，状态为 `queued`。
5. 后端将任务交给单线程 UZI 编排执行器。

### 5.2 Stage 1

1. 后端将任务提交给 Worker。
2. Worker 为任务创建隔离工作目录和 UZI 源码副本。
3. Worker 设置固定环境变量并直接调用 UZI `stage1(ticker)`。
4. Stage 1 输出至少包括：
   - `raw_data.json`
   - `dimensions.json`
   - `panel.json`
   - `_data_gaps.json`，如果存在数据缺口
   - Stage 1 执行日志和清单
5. 中文名称无法解析、ETF、指数、基金、可转债等非个股标的应返回结构化失败，禁止继续生成空报告。
6. Worker 写入 `stage1-manifest.json` 后将阶段标记为成功。

### 5.3 AniU 大模型深度评审

1. AniU 读取 Stage 1 清单及 JSON 文件。
2. `UziLlmOrchestrator` 使用 AniU 当前 AppSettings 中的 LLM 配置执行多轮结构化评审。
3. 评审结果合并为 UZI 能识别的 `agent_analysis.json`。
4. 使用 UZI 自带校验器或等价 schema 校验结果。
5. 结构不合法时允许一次定向修复调用；仍不合法则任务失败。
6. 禁止为了“让流程完成”而写入 `agent_reviewed=true` 的空壳内容。

### 5.4 Stage 2

1. AniU 将最终 `agent_analysis.json` 写入任务缓存目录。
2. 后端通知 Worker 执行 `stage2(normalized_ticker)`。
3. Worker 生成 synthesis、独立 HTML、分享图和战报图。
4. Worker 将最终产物复制到 `artifacts.tmp/`，计算大小和 SHA256。
5. 全部校验通过后原子重命名为 `artifacts/`，并写入 `artifact-manifest.json`。
6. AniU 从 `synthesis.json` 和元数据提取标准化摘要，写入数据库并将任务置为 `completed`。

### 5.5 查看和引用

- 历史页面读取数据库摘要，不扫描磁盘目录。
- 详情页面通过鉴权接口获取 HTML Blob，并在 sandbox iframe 中展示。
- `uzi_get_report_context` 从数据库读取标准化摘要，必要时读取受控 JSON 章节，绝不将完整 HTML 塞入模型上下文。

## 6. 任务状态机

允许的状态：

| 状态 | 含义 | 是否终态 |
|---|---|---|
| `queued` | 已入队，等待执行 | 否 |
| `stage1_running` | Worker 正在采集和机械评分 | 否 |
| `llm_review` | AniU 正在执行投资者与定性评审 | 否 |
| `stage2_running` | Worker 正在综合和渲染报告 | 否 |
| `completed` | 报告和摘要均已校验完成 | 是 |
| `failed` | 任一阶段不可恢复失败 | 是 |
| `cancelled` | 用户取消或服务关闭时主动取消 | 是 |

唯一正常迁移路径：

```text
queued
  → stage1_running
  → llm_review
  → stage2_running
  → completed
```

任一非终态都可以进入 `failed` 或 `cancelled`。禁止状态倒退，重试必须创建新任务，不复用失败任务 ID。

建议进度映射：

| 阶段 | 进度 |
|---|---:|
| 入队 | 0 |
| Worker 接受 Stage 1 | 5 |
| 数据采集 | 10-35 |
| 机械评分与投资者面板 | 35-45 |
| LLM 评审开始 | 50 |
| 投资者分组完成 | 60 |
| 定性研究完成 | 72 |
| 一致性与综合完成 | 82 |
| Stage 2 开始 | 85 |
| HTML 与图片完成 | 95 |
| 数据落库并完成 | 100 |

进度消息只能描述阶段、数据源、完成数量和错误摘要，不能推送模型隐藏推理过程。

## 7. 建议代码组织

### 7.1 主后端新增

```text
backend/app/
├── api/
│   └── uzi_router.py
├── schemas/
│   └── uzi.py
├── services/
│   ├── uzi_event_bus.py
│   ├── uzi_llm_orchestrator.py
│   ├── uzi_report_service.py
│   └── uzi_worker_client.py
└── db/
    ├── models.py
    └── database.py
```

修改现有文件：

- `backend/app/api/router.py`：包含 UZI 子路由。
- `backend/app/main.py`：启动时对账任务，关闭时停止执行器。
- `backend/app/core/config.py`：增加部署级 UZI 配置。
- `backend/app/core/rate_limit.py`：增加报告创建接口限流。
- `backend/app/services/llm_service.py`：增加受限的 UZI 结构化分析入口。
- `backend/skills/mx_core/tool_specs.py`：增加 `uzi_analysis` 的只读工具配置。

### 7.2 Worker 新增

```text
backend/uzi_worker/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── models.py
│   ├── runner.py
│   ├── state_store.py
│   └── uzi_adapter.py
├── Dockerfile
├── requirements.in
├── requirements.lock
└── uzi-source.lock
```

`uzi-source.lock` 至少记录：

```json
{
  "repository": "https://github.com/wbh604/UZI-Skill",
  "commit": "7bc779dd15ca4a741fcda20319a431f283232366",
  "archive_url": "固定 commit 对应的归档 URL",
  "sha256": "实现时计算并固定"
}
```

### 7.3 内置 Skill 新增

```text
backend/skills/uzi_report_context/
├── SKILL.md
└── handler.py
```

不要添加 README、安装说明或重复 schema 文档。`SKILL.md` 保持精简，工具参数和数据裁剪由 `handler.py` 实现。

### 7.4 前端新增

```text
frontend/src/
├── views/
│   ├── UziReportsView.vue
│   └── UziReportDetailView.vue
├── composables/
│   ├── useUziReports.ts
│   └── useUziReportStream.ts
└── components/uzi/
    ├── UziReportForm.vue
    ├── UziReportHistory.vue
    ├── UziReportProgress.vue
    └── UziReportSummary.vue
```

修改：

- `frontend/src/config/navigation.ts`
- `frontend/src/router/index.ts`
- `frontend/src/components/layout/NavIcon.vue`
- `frontend/src/services/api.ts`
- `frontend/src/types.ts`

组件数量可根据实际复杂度合并，但不得把所有网络、SSE、列表和详情逻辑堆进单个 Vue 文件。

## 8. 配置设计

UZI Worker 属于部署基础设施，配置放在 `Settings` 环境变量层，不加入 AppSettings UI。

主服务新增配置：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `UZI_ENABLED` | `true` | 是否启用 UZI 模块 |
| `UZI_WORKER_URL` | `http://aniu-uzi-worker:9001` | Worker 内部地址 |
| `UZI_WORKER_SHARED_SECRET` | 无 | 内部 API 共享密钥，启用时必填 |
| `UZI_REPORT_ROOT` | `/app/data/uzi_reports` | 共享报告根目录 |
| `UZI_MAX_ACTIVE` | `1` | 主服务并发任务数，第一版固定只接受 1 |
| `UZI_MAX_QUEUED` | `3` | 最大排队任务数 |
| `UZI_JOB_TIMEOUT_SECONDS` | `3600` | 整体任务超时 |
| `UZI_POLL_INTERVAL_SECONDS` | `2` | Worker 状态轮询间隔 |
| `UZI_CREATE_RATE_LIMIT_SECONDS` | `60` | 同一登录来源创建任务的最小间隔 |

Worker 配置：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `UZI_WORKER_TOKEN` | 无 | 必须与主服务共享密钥一致 |
| `UZI_SOURCE_ROOT` | `/opt/uzi` | 镜像内固定上游源码 |
| `UZI_REPORT_ROOT` | `/app/data/uzi_reports` | 共享报告根目录 |
| `UZI_WORKER_PORT` | `9001` | 内部端口 |
| `UZI_DEPTH` | `deep` | 固定深度 |
| `UZI_LITE` | `0` | 禁用 lite |
| `UZI_NO_AUTO_OPEN` | `1` | 禁止自动打开浏览器 |
| `UZI_PLAYWRIGHT_ENABLE` | `1` | 启用镜像内 Playwright 兜底 |
| `PYTHONUNBUFFERED` | `1` | 实时输出阶段日志 |

敏感信息要求：

- `UZI_WORKER_SHARED_SECRET` 和 `UZI_WORKER_TOKEN` 不允许提供默认弱值。
- LLM API Key 只从当前 AppSettings 读取并由主服务使用。
- MX API Key 可通过内部请求头临时传给 Worker，必须在日志过滤器中脱敏。
- 数据库只记录模型名称、推理配置和可审计的非敏感元数据。

## 9. 数据库模型

新增 `UziReportJob`，建议字段如下：

| 字段 | 类型 | 约束/用途 |
|---|---|---|
| `id` | Integer | 主键 |
| `ticker_input` | String(64) | 用户原始输入 |
| `ticker_normalized` | String(32), nullable | UZI 解析后的标准代码 |
| `company_name` | String(128), nullable | 公司名称 |
| `status` | String(32) | 状态机值 |
| `phase` | String(64), nullable | 更细阶段标识 |
| `progress` | Integer | 0-100 |
| `progress_message` | Text, nullable | 用户可见进度 |
| `error_code` | String(64), nullable | 稳定错误码 |
| `error_message` | Text, nullable | 已脱敏错误摘要 |
| `worker_job_id` | String(64), nullable | Worker 内部任务 ID |
| `uzi_commit` | String(40) | 固定上游版本 |
| `llm_model` | String(255), nullable | 实际模型 |
| `llm_reasoning_effort` | String(32), nullable | 实际推理配置 |
| `data_as_of` | DateTime, nullable | 报告使用的数据时间 |
| `summary_json` | JSON, nullable | 给列表、详情和 Skill 使用 |
| `artifact_manifest_json` | JSON, nullable | 受控产物清单 |
| `report_rel_dir` | String(255) | 相对 `UZI_REPORT_ROOT` 的目录 |
| `created_at` | DateTime | 创建时间 |
| `started_at` | DateTime, nullable | 开始时间 |
| `finished_at` | DateTime, nullable | 结束时间 |
| `updated_at` | DateTime | 更新时间 |

索引：

- `(ticker_normalized, created_at)`，支持查询股票最新报告。
- `(status, created_at)`，支持活动任务和历史筛选。

该功能只新增表，因此初版可依靠 `Base.metadata.create_all`。若实施过程中修改现有表，则必须同步增加 `database.py` 的内联迁移，不能只改 ORM Model。

### 9.1 标准化摘要结构

`summary_json` 固定为以下顶层结构，允许字段为空但不允许随意改名：

```json
{
  "schema_version": 1,
  "ticker": "600519.SH",
  "company_name": "贵州茅台",
  "overall_score": 78.5,
  "verdict": "谨慎看多",
  "one_liner": "核心结论",
  "valuation": {
    "rating": "合理偏低",
    "target_price": 0,
    "upside_pct": 0,
    "methods": []
  },
  "risks": [],
  "catalysts": [],
  "panel": {
    "bullish": 0,
    "neutral": 0,
    "bearish": 0,
    "key_disagreements": []
  },
  "qualitative": {},
  "data_gaps": {
    "coverage_pct": 0,
    "unresolved": 0,
    "items": []
  },
  "sources": [],
  "data_as_of": "ISO-8601",
  "generated_at": "ISO-8601",
  "disclaimer": "历史研究资料，不构成投资建议"
}
```

## 10. 公共 API 契约

所有接口沿用 `/api/aniu` 前缀和现有 JWT 鉴权。

### 10.1 模块状态

`GET /api/aniu/uzi/status`

响应：

```json
{
  "enabled": true,
  "worker_available": true,
  "worker_version": "7bc779d",
  "active_jobs": 0,
  "queued_jobs": 0,
  "max_queued": 3,
  "reason": null
}
```

### 10.2 创建报告

`POST /api/aniu/uzi/reports`

请求：

```json
{
  "ticker": "600519.SH"
}
```

输入规则：

- 去除首尾空白。
- 长度 1-64。
- 支持上游能够解析的 A/H/美股代码和中文名称。
- 拒绝控制字符、路径分隔符和明显命令字符。
- 通过 Python 函数参数传递，禁止拼接 Shell 命令。

成功响应使用 202：

```json
{
  "report": {
    "id": 123,
    "ticker_input": "600519.SH",
    "ticker_normalized": null,
    "status": "queued",
    "phase": "queued",
    "progress": 0,
    "created_at": "ISO-8601"
  },
  "reused": false
}
```

若同一股票已有活动任务，仍返回 202，但 `reused=true` 且返回现有任务。

### 10.3 历史列表

`GET /api/aniu/uzi/reports?limit=20&offset=0&ticker=&status=`

约束：

- `limit` 默认 20，最大 100。
- 默认按 `created_at DESC`。
- `ticker` 同时匹配标准代码、原始输入和公司名称。
- `status` 只允许状态枚举值。

响应：

```json
{
  "items": [],
  "total": 0,
  "limit": 20,
  "offset": 0
}
```

列表项包含 ID、股票、公司名、状态、进度、评分、结论、模型、创建和完成时间，不返回完整章节。

### 10.4 报告详情

`GET /api/aniu/uzi/reports/{id}`

返回任务完整状态、`summary_json` 和经过清洗的产物清单。不得返回服务端绝对路径、Worker Token 或原始日志路径。

### 10.5 进度事件

`GET /api/aniu/uzi/reports/{id}/events`

事件格式沿用现有 SSE 工具，事件类型固定为：

- `snapshot`
- `status_changed`
- `progress`
- `completed`
- `failed`
- `cancelled`
- `heartbeat`

客户端断线重连后先发送数据库快照，再订阅内存事件。任务已终态时发送快照和终态事件后关闭。

### 10.6 取消

`POST /api/aniu/uzi/reports/{id}/cancel`

- 终态任务返回当前状态，不重复操作。
- 运行中任务向 Worker 发送取消请求。
- LLM 阶段使用 `threading.Event` 或现有取消机制终止后续调用。
- 取消成功后状态为 `cancelled`，保留任务记录。

### 10.7 删除

`DELETE /api/aniu/uzi/reports/{id}`

- 只允许删除终态任务。
- 运行中任务返回 409，并提示先取消。
- 删除前将 `report_rel_dir` 与固定根目录解析校验，禁止路径穿越。
- 成功后删除数据库记录及报告目录，返回 204。
- 删除失败时不应先提交数据库删除；文件和数据库操作需要可恢复的顺序及错误记录。

### 10.8 报告产物

`GET /api/aniu/uzi/reports/{id}/artifacts/{artifact_key}`

`artifact_key` 只能来自清单，例如：

- `html`
- `share_card`
- `war_report`
- `meta`
- `one_liner`
- `synthesis`

服务端根据清单映射文件，不接受文件名或相对路径。HTML 默认 inline，图片和 JSON 支持下载响应头。

## 11. Worker 内部 API

Worker 只监听 Docker 内部网络。所有 `/internal/*` 请求必须校验 `X-Aniu-Uzi-Token`，健康检查可按部署需要豁免。

### 11.1 健康检查

`GET /internal/health`

返回 Worker 版本、UZI Commit、Chromium 可用性、当前任务数和队列状态。

### 11.2 提交 Stage 1

`POST /internal/jobs/{report_id}/stage1`

请求：

```json
{
  "ticker": "600519.SH",
  "report_rel_dir": "123",
  "mx_api_key": "仅在内存中使用"
}
```

Worker 必须验证 `report_rel_dir` 与 `report_id` 一致，不允许任意目录。

### 11.3 查询 Worker 状态

`GET /internal/jobs/{report_id}`

返回 `accepted/running/succeeded/failed/cancelled`、阶段、进度、脱敏错误和产物清单位置。

### 11.4 提交 Stage 2

`POST /internal/jobs/{report_id}/stage2`

Worker 必须先验证：

- Stage 1 清单存在且成功。
- `agent_analysis.json` 存在。
- `agent_reviewed=true`。
- 股票代码与 Stage 1 清单一致。

### 11.5 取消

`POST /internal/jobs/{report_id}/cancel`

Worker 终止任务进程组：先发送 SIGTERM，等待最多 10 秒，再发送 SIGKILL。必须保证只终止该报告 ID 对应的子进程。

## 12. Worker 实现约束

### 12.1 UZI 源码和依赖

- Worker 镜像构建时下载固定 Commit 归档并校验 SHA256。
- 安装依赖必须来自 `requirements.lock`，不能直接依赖上游未锁定 requirements 作为最终生产输入。
- Chromium 在镜像构建阶段安装，运行时不允许下载浏览器。
- 镜像中保留 UZI LICENSE 和版本元数据。

### 12.2 任务目录

每个报告目录固定为：

```text
{UZI_REPORT_ROOT}/{report_id}/
├── worker-state.json
├── logs/
│   ├── stage1.log
│   └── stage2.log
├── work/
│   ├── uzi/                 # 任务期间的源码副本
│   ├── .cache/
│   ├── stage1-manifest.json
│   └── agent_analysis.json
├── artifacts.tmp/
└── artifacts/
    ├── index.html
    ├── report.meta.json
    ├── one-liner.txt
    ├── synthesis.json
    ├── share-card.png
    ├── war-report.png
    └── artifact-manifest.json
```

任务完成后删除 `work/uzi`、浏览器 Profile 和无用临时文件，但保留 Stage 1 核心 JSON、`agent_analysis.json`、synthesis 和最终产物，便于复核。

### 12.3 调用方式

- 通过受控 Python 适配器导入 UZI 模块并调用函数。
- 禁止 `shell=True`。
- 禁止将用户输入拼入命令行字符串。
- 运行前将进程当前目录切换到任务工作目录，确保 `Path(".cache")` 落在任务目录。
- 因部分 UZI 报告路径相对源码文件，任务运行时使用独立源码副本，避免多个任务共享报告目录。
- 固定单任务执行仍不能省略目录隔离，因为取消、重启和残留缓存都会造成串数据风险。

### 12.4 产物校验

Stage 2 完成条件：

- `index.html` 存在且大于 10 KB。
- HTML 可用 UTF-8 读取。
- `synthesis.json` 存在且包含评分和 verdict。
- `agent_analysis.json` 通过校验。
- 产物大小和 SHA256 写入清单。
- HTML、JSON 和图片的相对路径都位于 `artifacts.tmp`。
- 原子移动完成后才向主服务报告成功。

## 13. LLM 深度评审设计

### 13.1 配置复用

`UziLlmOrchestrator` 必须复用当前 AppSettings：

- LLM Base URL
- LLM API Key
- 模型名称
- Reasoning effort
- 超时
- 最大重试次数

不复用交易策略系统提示词。UZI 使用独立、版本化的研究提示词，避免交易执行规则或用户策略污染报告结构。

任务开始前若 LLM 配置不完整，创建接口直接返回 422，不应让任务排队后才失败。

### 13.2 内部运行类型与工具安全

增加内部运行类型 `uzi_analysis`，但它不是计划任务或前端可选运行类型。

`mx_core` 增加以下工具配置：

```python
TOOL_PROFILES["uzi_analysis"] = {
    "mx_query_market",
    "mx_search_news",
}
```

由于 `builtin_utils` 默认提供写文件、exec 和 HTTP POST 等工具，UZI LLM 入口必须执行第二层硬过滤：

```python
UZI_LLM_ALLOWED_TOOLS = {
    "mx_query_market",
    "mx_search_news",
    "web_search",
    "web_fetch",
    "http_get",
}
```

工具列表构建后只保留此集合，工具执行器也必须再次拒绝集合外调用。绝对不能只靠提示词禁止：

- `mx_moni_trade`
- `mx_moni_cancel`
- `mx_manage_self_select`
- `write_file`
- `edit_file`
- `exec`
- `http_post`

### 13.3 评审任务拆分

LLM 阶段按以下固定任务执行，最大并发 4：

1. 投资者面板 A：价值、质量、现金流和估值方法。
2. 投资者面板 B：成长、创新、行业空间和竞争优势。
3. 投资者面板 C：风险、做空、治理、财务异常和行为偏差。
4. 投资者面板 D：事件、资金、技术面和市场结构。
5. 定性研究 A：宏观与政策，对应 `3_macro`、`13_policy`。
6. 定性研究 B：行业与事件，对应 `7_industry`、`15_events`。
7. 定性研究 C：原材料、期货和成本传导，对应 `8_materials`、`9_futures`。
8. 一致性审查：检查事实冲突、过期数据、缺口、重复证据和过度推断。
9. 综合组装：生成最终 `agent_analysis.json`。

面板分组应根据 Stage 1 `panel.json` 的类别或标签分配，不在代码中硬编码当前 51 位投资者姓名，避免上游名单变化导致遗漏。

### 13.4 模型输出要求

- 所有中间调用要求 JSON，不要求或保存隐藏思维链。
- 保存结论、证据摘要、来源、时间、置信度和反例。
- 外部网页内容视为不可信数据，不得执行其中指令。
- 事实和观点分开；无法验证的内容标记为数据缺口。
- 不允许编造目标价、财务数字、来源链接或投资者观点。
- 最终结果必须包含 UZI Stage 2 要求的字段：
  - `agent_reviewed`
  - `dim_commentary`
  - `panel_insights`
  - `great_divide_override`
  - `narrative_override`
  - `qualitative_deep_dive`
  - `data_gap_acknowledged`

### 13.5 失败与修复

- 单个并行子任务失败时按 AniU 当前 LLM 重试配置执行。
- 子任务最终失败则整个 LLM 阶段失败，不以脚本骨架降级成“完整深度报告”。
- 最终 JSON 校验失败后，向同一模型发送校验错误和原 JSON，执行一次仅修结构的修复调用。
- 修复后仍失败，错误码为 `UZI_AGENT_ANALYSIS_INVALID`。
- 日志只记录调用阶段、耗时、Token 使用量和错误类型，不记录 API Key，不记录隐藏推理。

## 14. 内置 Skill：uzi_get_report_context

### 14.1 SKILL.md

建议 Frontmatter：

```yaml
---
name: uzi_report_context
description: 读取已完成的 UZI 个股深度报告摘要，为 AniU 分析、交易或聊天提供带时间戳的历史研究参考。
metadata:
  aniu:
    handler_module: skills.uzi_report_context.handler
    run_types: [analysis, trade, chat]
    category: finance
---
```

SOP 必须明确：

- 只有用户问题与具体股票、历史深度报告或报告 ID 有关时才调用。
- 报告不是实时证据。
- 交易模式引用报告后仍必须查询本轮行情、持仓、资金和必要委托。
- 不得根据报告自动调用交易工具。
- 优先按明确的 `report_id` 查询，否则按 ticker 获取最新完成报告。

### 14.2 工具参数

工具名称固定为 `uzi_get_report_context`。

```json
{
  "type": "object",
  "properties": {
    "report_id": {
      "type": "integer",
      "minimum": 1
    },
    "ticker": {
      "type": "string",
      "maxLength": 64
    },
    "sections": {
      "type": "array",
      "items": {
        "type": "string",
        "enum": [
          "overview",
          "valuation",
          "risks",
          "catalysts",
          "panel",
          "qualitative",
          "data_gaps",
          "sources"
        ]
      }
    },
    "max_chars": {
      "type": "integer",
      "minimum": 1000,
      "maximum": 20000,
      "default": 12000
    }
  },
  "additionalProperties": false
}
```

Handler 规则：

- `report_id` 和 `ticker` 至少提供一个。
- 同时提供时要求报告 ID 对应股票与 ticker 一致，否则返回参数冲突。
- ticker 查询只返回最新 `completed` 报告。
- `sections` 为空时返回 overview、valuation、risks、catalysts、panel 和 data_gaps。
- 结果超过 `max_chars` 时按章节优先级裁剪，并设置 `truncated=true`。

### 14.3 返回结构

```json
{
  "ok": true,
  "source": "uzi_report",
  "report_id": 123,
  "ticker": "600519.SH",
  "company_name": "贵州茅台",
  "generated_at": "ISO-8601",
  "data_as_of": "ISO-8601",
  "age_days": 2,
  "is_stale": false,
  "uzi_commit": "7bc779d...",
  "llm_model": "configured-model",
  "sections": {},
  "data_gap_warning": null,
  "truncated": false,
  "disclaimer": "该报告是历史研究资料，不构成投资建议；当前交易决策必须重新查询实时数据。"
}
```

默认过期提示：

- 报告超过 7 天：`is_stale=true`。
- 不论是否超过 7 天，交易模式都不能把报告内行情数字当作当前值。

## 15. 前端规格

### 15.1 导航与路由

- 导航名称：`UZI报告`。
- 列表路由：`/uzi-reports`。
- 详情路由：`/uzi-reports/:reportId`。
- 为 `AppNavIcon` 增加 `uzi`，在 `NavIcon.vue` 使用现有图标绘制方式。
- 移动端侧栏和标题必须自动继承新菜单名称。

### 15.2 列表页

页面结构：

1. `UiPageHeader`：标题“个股深度报告”，副标题说明由 UZI 生成完整深度研究。
2. 生成面板：
   - 股票代码或名称输入框。
   - “生成深度报告”主按钮。
   - Worker 或 LLM 不可用时禁用按钮并给出具体原因。
   - 明确显示本功能只支持完整深度分析。
3. 活动任务区域：
   - 股票、阶段、进度条、耗时、取消按钮。
   - 使用 SSE 更新，断线后自动回退到轮询。
4. 历史区域：
   - 搜索股票或公司名。
   - 状态筛选。
   - 分页。
   - 桌面端使用紧凑列表或表格，移动端改为纵向条目。

历史项展示：

- 股票代码和公司名称。
- 综合评分和 verdict。
- 状态及进度。
- 生成时间、耗时。
- LLM 模型。
- 查看、重新生成和删除操作。

### 15.3 详情页

详情页包含：

- 返回历史列表。
- 股票、公司名称、状态、报告时间和数据时间。
- 评分、结论、目标价或估值摘要、数据覆盖率。
- 风险、催化剂和投资者分歧。
- HTML 报告预览区。
- 下载 HTML、分享卡、战报图、元数据和 synthesis。
- 删除按钮。

HTML 加载：

1. 使用现有 API 封装携带 JWT 获取 Blob。
2. 使用 `URL.createObjectURL` 创建临时地址。
3. iframe 必须设置 sandbox，默认只开放 `allow-scripts` 和必要下载能力，不开放 `allow-same-origin`。
4. 组件卸载或报告变化时调用 `URL.revokeObjectURL`。
5. 加载失败时仍保留摘要和下载入口。

### 15.4 UI 约束

- 复用现有 `UiPageHeader`、`UiPanel`、`UiBadge`、`UiButton`、`UiStatCard` 和主题变量。
- 不引入新的 CSS 框架。
- 不使用嵌套卡片堆叠。
- 状态颜色沿用现有语义色：进行中为蓝色、成功为绿色、失败为红色、取消为中性。
- 所有按钮在移动端必须保持可点击尺寸，长股票名和错误消息必须换行。
- 不展示教学式大段文案；帮助信息使用简洁次要文本。

### 15.5 前端类型

新增：

- `UziStatus`
- `UziReportStatus`
- `UziReportSummary`
- `UziReportDetail`
- `UziReportArtifact`
- `UziReportListResponse`
- `UziReportEvent`
- `CreateUziReportRequest`
- `CreateUziReportResponse`

状态值应定义为联合类型，不使用任意字符串。

## 16. 报告文件与安全

### 16.1 路径安全

- 数据库只存相对目录。
- 路径解析后必须确认目标是 `UZI_REPORT_ROOT` 的后代。
- 产物访问通过 key 到 manifest 的映射，不通过客户端文件名。
- 删除时拒绝符号链接逃逸。
- Worker 创建目录时拒绝已存在且所有者不匹配的目录。

### 16.2 HTML 安全

- UZI HTML 视为受信生成器产生但仍需隔离的主动内容。
- iframe 使用 sandbox。
- 不把 HTML 直接插入 AniU DOM。
- 报告 API 设置正确 MIME 类型和 `X-Content-Type-Options: nosniff`。
- 若报告依赖外链资源，应在 Worker 导出阶段转换成独立 HTML；不能为此放宽 iframe 同源权限。

### 16.3 数据和日志

- 成功、失败、取消任务记录都永久保留，直到用户删除。
- 失败和取消任务可以删除无用源码副本与浏览器缓存，但保留脱敏日志尾部和错误信息。
- 日志不得包含 Authorization、LLM Key、MX Key、Worker Token。
- API 返回的错误信息不包含宿主机绝对路径和 Python 完整环境信息。

## 17. 重启恢复和故障处理

### 17.1 主服务启动对账

启动时查询所有非终态 `UziReportJob`：

1. 调用 Worker 状态接口。
2. Worker 仍在执行时恢复轮询。
3. Worker 已成功但数据库未推进时，根据阶段清单继续下一步。
4. Worker 不认识任务且本地有完整阶段产物时尝试从清单恢复。
5. 无法确认一致状态时标记 `failed`，错误码 `UZI_ORPHANED_JOB`。

### 17.2 Worker 重启

- Worker 状态同时写入内存和 `worker-state.json`。
- 重启时扫描非终态状态文件。
- 已不存在执行进程的任务标记为 failed，不自动重新运行外部数据采集。
- 由主服务对账并向用户展示可重新生成。

### 17.3 典型错误码

- `UZI_DISABLED`
- `UZI_WORKER_UNAVAILABLE`
- `UZI_QUEUE_FULL`
- `UZI_INVALID_TICKER`
- `UZI_UNRESOLVED_TICKER`
- `UZI_NON_STOCK_SECURITY`
- `UZI_STAGE1_FAILED`
- `UZI_LLM_NOT_CONFIGURED`
- `UZI_LLM_REVIEW_FAILED`
- `UZI_AGENT_ANALYSIS_INVALID`
- `UZI_STAGE2_FAILED`
- `UZI_ARTIFACT_INVALID`
- `UZI_JOB_TIMEOUT`
- `UZI_ORPHANED_JOB`
- `UZI_CANCELLED`

## 18. Docker 与部署

### 18.1 Compose

`docker-compose.yml` 新增 `aniu-uzi-worker`：

- 使用独立 Dockerfile。
- 与主服务共享 `./data:/app/data`。
- 仅加入内部网络，不映射 Worker 端口到宿主机。
- 配置健康检查。
- 主服务通过 `UZI_WORKER_URL` 访问。
- 为 Worker 设置合理 CPU 和内存限制，但不要设置小到导致 Pandas 或 Chromium OOM 的值。

### 18.2 主镜像

- 主 `Dockerfile` 不安装 UZI 依赖。
- 主镜像只增加 UZI API、服务和内置 Skill 代码。
- 发布流程需要同时构建并发布主镜像与 Worker 镜像。
- 镜像标签保持一致，例如主镜像和 Worker 使用同一 Git SHA 或版本号。

### 18.3 旧部署兼容

- 原单容器部署继续提供 AniU 现有功能。
- 未配置 Worker 时，`GET /uzi/status` 返回不可用。
- 前端菜单可以保留，但生成按钮禁用并显示“UZI Worker 未配置”。
- 不能因 Worker 不可用导致主服务启动失败。

## 19. 强制开发与调试隔离

本节是实施要求，不是建议。

### 19.1 AniU 主后端

沿用仓库现有虚拟环境：

```bash
cd backend
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python -m pytest
```

所有 Python 命令使用 `./.venv/bin/python -m ...`，不要调用全局 `pip`。

### 19.2 UZI Worker

Worker 使用完全独立的虚拟环境：

```bash
cd backend/uzi_worker
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.lock
PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright" \
  ./.venv/bin/python -m playwright install chromium
```

要求：

- `backend/.venv` 与 `backend/uzi_worker/.venv` 不得互相复用。
- `.playwright`、任务缓存和下载归档加入 `.gitignore`。
- 宿主机不得执行 `playwright install --with-deps`；缺少系统动态库时使用 Worker Docker 镜像调试。
- 不运行 UZI 带自动安装功能的 CLI 入口。
- 不使用 `sudo pip`、`pip install --user` 或全局 `npm install -g`。
- 生成锁文件的工具也安装在临时或 Worker 虚拟环境中，不能安装到系统 Python。

### 19.3 前端

```bash
cd frontend
npm ci
npm run build
```

- 依赖只安装在项目 `node_modules`。
- 不全局安装 Vite、Vue CLI 或测试运行器。
- 若需要新增 Vitest，必须作为开发依赖写入 `package.json` 和 lockfile，不能只在本机安装。

### 19.4 推荐联调方式

真实 UZI 依赖和 Chromium 联调优先使用：

```bash
docker compose build aniu-uzi-worker
docker compose up aniu aniu-uzi-worker
```

测试数据目录使用项目 `data/` 或 `mktemp -d` 创建的临时目录。严禁把 UZI 缓存写入系统根目录、用户 Home 或共享的全局 Python 环境。

## 20. 测试计划

### 20.1 后端单元测试

- 创建任务时 LLM 未配置返回 422。
- Worker 关闭时返回明确不可用状态。
- 输入为空、超长、含路径分隔符或控制字符时拒绝。
- 同一股票活动任务复用。
- 队列满时返回 429 或 409，错误码稳定。
- 状态只能按状态机迁移。
- 取消终态任务是幂等操作。
- 运行中任务不可删除。
- 删除执行路径逃逸防护。
- 列表分页、股票过滤和状态过滤。
- 应用重启后的任务对账。

### 20.2 Worker 测试

- Token 校验。
- Stage 1 创建独立目录。
- 两个任务不会共享 cache。
- 中文名解析失败映射到稳定错误码。
- 非个股标的停止在 Stage 1。
- 取消只终止目标子进程。
- Stage 2 缺少 `agent_analysis.json` 时拒绝。
- `agent_reviewed=false` 时拒绝完整报告。
- HTML 太小、文件缺失或 manifest 路径越界时失败。
- 日志脱敏 MX Key 和内部 Token。

### 20.3 LLM 编排测试

- 确认 Base URL、模型、reasoning 和重试配置来自 AppSettings。
- 确认 API Key 不发送给 Worker。
- `uzi_analysis` 工具列表只包含硬编码 allowlist。
- 工具执行器拒绝伪造的 `mx_moni_trade` 或 `exec` 调用。
- 并行分组结果正确汇总。
- 单组失败触发重试并最终终止。
- JSON 结构错误触发一次修复。
- 修复失败不会调用 Stage 2。
- 外部内容中的提示注入不改变系统任务或工具权限。

### 20.4 Skill 测试

- 按报告 ID 查询成功。
- 按 ticker 返回最新完成报告。
- 不返回 queued、failed、cancelled 报告。
- report ID 与 ticker 冲突时报错。
- 章节选择和默认章节正确。
- `max_chars` 裁剪稳定并设置 truncated。
- 报告超过 7 天时标记 stale。
- 交易模式可读取报告，但工具本身没有任何写操作。

### 20.5 API 与安全测试

- 全部公共接口要求 JWT。
- Worker 内部接口拒绝错误 Token。
- artifact key 白名单。
- 路径穿越、符号链接逃逸和未知 MIME。
- HTML 响应安全头。
- SSE 初始快照、心跳、终态关闭和重连。
- 错误响应不泄露绝对路径、Key 或完整 traceback。

### 20.6 前端验证

- 菜单和路由正常。
- Worker 不可用和 LLM 未配置状态。
- 表单防重复提交。
- SSE 更新与断线轮询回退。
- 历史分页和筛选。
- 详情摘要和 artifact Blob 预览。
- 页面卸载时释放 Blob URL。
- 取消、重新生成和删除确认。
- 桌面与移动端无文字重叠、按钮溢出或横向滚动。
- `npm run build` 通过 TypeScript 检查。

### 20.7 真实烟雾测试

真实网络测试不作为普通 CI 强制项，单独执行：

1. 使用固定股票代码，例如 `600519.SH`。
2. 使用测试专用 LLM 和 MX 配置。
3. 完成 Stage 1、AniU LLM 评审和 Stage 2。
4. 校验 HTML 可打开、PNG 非空、summary 可读取。
5. 在 AniU 分析模式调用 `uzi_get_report_context`。
6. 在交易模式调用该工具后，确认模型仍会查询本轮实时数据，且不会因报告自动下单。
7. 删除报告并确认文件与数据库记录消失。

## 21. 分批实施计划

每一批必须独立提交、独立测试，方便最终 Review 定位问题。不要把全部功能压入一个巨大提交。

### 批次 1：Worker 基础与依赖隔离

交付：

- Worker 目录、Dockerfile、锁定依赖和 UZI 来源锁。
- 健康检查、Token 认证、任务状态文件。
- 隔离工作目录和受控 Stage 1/Stage 2 适配器。
- Worker 单元测试。

完成条件：

- 能在容器中执行模拟 Stage 1/Stage 2。
- 主后端环境中没有新增 AkShare、Pandas、Playwright。
- 所有宿主机调试命令使用 Worker 独立 venv。

### 批次 2：任务模型与 Worker 客户端

交付：

- `UziReportJob` 模型和 schema。
- Worker Client。
- 报告服务、状态机、事件总线。
- 公共 API、取消、删除和 artifact 服务。
- 启动对账。

完成条件：

- 使用假 Worker 能完成完整状态流转。
- API、路径安全和恢复测试通过。

### 批次 3：LLM 深度评审

交付：

- `uzi_analysis` 内部运行类型。
- 工具双重 allowlist。
- 多组并行评审、综合、校验和修复。
- `agent_analysis.json` 落盘。

完成条件：

- 假模型可以产生合法分析并推进 Stage 2。
- 恶意工具调用和提示注入测试通过。
- 任何无效模型输出都不能被标记为完整报告。

### 批次 4：内置 Skill

交付：

- `uzi_report_context/SKILL.md`。
- `uzi_get_report_context` Handler。
- 查询、裁剪、时效和免责声明测试。

完成条件：

- 分析、交易和聊天模式能发现工具。
- 工具只读且不会扩大交易工具权限。

### 批次 5：前端页面

交付：

- 导航、列表、详情、进度、预览、下载和删除。
- API 类型和 composables。
- Worker 不可用及各种失败状态。

完成条件：

- `npm run build` 通过。
- 桌面和移动端截图检查通过。
- HTML iframe 隔离和 Blob 清理验证通过。

### 批次 6：容器集成与文档

交付：

- Compose 双服务。
- 发布流程同时构建 Worker。
- 环境变量模板和 README 部署说明。
- 真实股票烟雾测试记录。

完成条件：

- `docker compose up` 后主服务和 Worker 健康。
- 原单容器模式不因缺少 Worker 崩溃。
- 完整报告可以生成、保存、查看、引用和删除。

## 22. 最终验收标准

以下条件全部满足才算完成：

1. 用户能从“AniU → UZI报告”提交一只股票。
2. UI 能持续显示 Stage 1、LLM 评审和 Stage 2 进度。
3. 完整报告包含经过 AniU 当前模型评审的 `agent_analysis.json`。
4. 报告历史在刷新和服务重启后仍存在。
5. 详情页可以安全预览 HTML 并下载产物。
6. 用户可以手动删除报告及全部对应文件。
7. `uzi_get_report_context` 能按 ID 或股票返回结构化报告上下文。
8. 报告上下文明确标记时间和数据缺口。
9. 交易模式引用报告后仍受实时数据门槛和交易工具边界约束。
10. UZI Worker 不持有 LLM Key，不访问 SQLite。
11. AniU 主 Python 环境不包含 UZI 重依赖。
12. 开发、测试和浏览器依赖均在独立 venv、项目目录或容器中，没有全局环境污染。
13. 后端测试、前端构建、Worker 测试和 Compose 烟雾测试全部通过。

## 23. 最终 Review 清单

提交实现后，主审模型重点检查：

- 是否有人为了省事直接把 UZI 放进自定义 Skill 或 `builtin_utils.exec`。
- 是否把 AkShare、Pandas、Playwright 加入主后端 requirements。
- 是否存在运行时自动安装依赖。
- 是否有用户输入进入 Shell。
- 是否在 Worker 或数据库中保存 LLM Key。
- `uzi_analysis` 是否可能获得交易、写文件、exec 或 HTTP POST 工具。
- `agent_analysis.json` 是否真的经过结构校验。
- 是否能以脚本骨架冒充完整深度报告。
- 删除和 artifact 接口是否存在路径穿越。
- HTML 是否通过 sandbox iframe 展示。
- SSE 是否泄漏模型隐藏推理。
- 报告是否被错误地当作实时交易证据。
- 任务重启恢复是否会重复运行或串用其他股票缓存。
- 所有调试说明和脚本是否明确使用虚拟环境或容器。
- Docker Compose、环境模板、README 和发布流程是否同步更新。

## 24. 固定默认值与允许调整项

固定，不得自行改变：

- 独立 Worker。
- 只支持完整深度分析。
- AniU 编排大模型。
- 历史永久保留、手动删除。
- 单任务默认并发。
- 报告上下文只读。
- 交易仍要求本轮实时证据。
- 主后端与 Worker 依赖隔离。

允许在实现中按现有代码风格微调：

- 类和函数的具体拆分。
- Vue 子组件数量。
- 日志字段命名。
- Worker 内部进度细分。
- UI 文案的细微调整。

任何会改变公共 API、数据库摘要结构、安全边界、任务状态机或部署拓扑的调整，必须先更新本文档并说明理由，再进入实现。
