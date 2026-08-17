# UZI 深度报告：真实烟雾测试检查清单

> 来源：`docs/uzi-integration-implementation-plan.md` §20.7 / §22 最终验收标准。
>
> 真实网络测试不作为普通 CI 强制项，由运维在部署环境单独执行。本清单给出可执行的步骤与验收点；每一项需记录实际结果，全部通过才视为烟雾测试完成。

## 前置条件

- [ ] 已完成 `docker compose build aniu-uzi-worker` 并启动双服务：
  ```bash
  docker compose up -d aniu aniu-uzi-worker
  ```
- [ ] 主服务与 Worker 均健康：
  ```bash
  curl -fsS http://127.0.0.1:8000/health
  docker inspect --format '{{.State.Health.Status}}' aniu-uzi-worker
  ```
- [ ] `.env.docker` 与根目录 `.env` 中 `UZI_WORKER_SHARED_SECRET` 已配置且一致。
- [ ] 「功能设置」中已配置测试专用 LLM（Base URL / API Key / Model）与妙想密钥。
- [ ] 已登录获取 JWT（`POST /api/aniu/login`），后续请求携带 `Authorization: Bearer <token>`。

## 1. 模块状态

- [ ] `GET /api/aniu/uzi/status` 返回 `enabled=true`、`worker_available=true`、`worker_version=7bc779d`。
- [ ] 未配置 Worker（`UZI_ENABLED=false`）时主服务仍正常启动，`/api/aniu/uzi/status` 返回不可用，主服务其他接口不受影响。

## 2. 创建任务与全流程

- [ ] `POST /api/aniu/uzi/reports`，body `{"ticker": "600519.SH"}`，返回 `202`、`status=queued`、`reused=false`。
- [ ] 同一股票在任务未终态时再次提交，返回 `202` 且 `reused=true`，返回现有任务。
- [ ] 通过 `GET /api/aniu/uzi/reports/{id}/events`（SSE）观察状态迁移：
  `queued → stage1_running → llm_review → stage2_running → completed`，进度从 0 到 100。
- [ ] SSE 事件不含模型隐藏推理内容（仅阶段、数据源、完成数量与错误摘要）。
- [ ] 任务结束后 SSE 发送终态事件并关闭；断线重连先收到数据库快照再订阅增量。
- [ ] Stage 2 日志显示基金风格识别最多查询 `UZI_QUANT_MAX_FUNDS` 只基金；模拟查询超时后记录“已跳过”并继续完成报告。

## 2.1 上游更新

- [ ] 无活动报告时，页面“更新上游”按钮可检查并更新到 GitHub 最新 commit；已是最新版时幂等返回。
- [ ] 有 queued/running 报告时更新返回 `409`，不会改变该任务记录的 `uzi_commit`。
- [ ] 更新成功后 `/api/aniu/uzi/status` 显示新短 commit，新建报告记录新版本。
- [ ] 重启 `aniu-uzi-worker` 后仍使用 `/app/data/uzi_source` 中的更新版本。
- [ ] 上游版本不兼容或下载失败时保留旧源码，新报告仍可使用旧版本生成。

## 3. 产物校验

- [ ] 详情接口 `GET /api/aniu/uzi/reports/{id}` 返回 `summary_json`（含 overall_score / verdict / one_liner / risks / catalysts / panel / data_gaps / sources）与清洗后的产物清单；不含宿主机绝对路径。
- [ ] `GET /api/aniu/uzi/reports/{id}/artifacts/html` 返回 HTML（`Content-Type: text/html; charset=utf-8`、`X-Content-Type-Options: nosniff`），可直接在浏览器打开。
- [ ] `GET /api/aniu/uzi/reports/{id}/artifacts/share_card` 与 `war_report` 返回非空 PNG（`size > 0`）。
- [ ] `GET /api/aniu/uzi/reports/{id}/artifacts/synthesis` 返回 JSON 且含评分与 verdict。
- [ ] 磁盘目录存在：`{UZI_REPORT_ROOT}/{id}/artifacts/`（full-report-standalone.html / share-card.png / war-report.png / synthesis.json / report.meta.json / artifact-manifest.json）。
- [ ] 产物请求携带未知 artifact key 返回 4xx；路径穿越（如 `../`）请求被拒绝。

## 4. 报告引用（uzi_get_report_context）

- [ ] 在「AI 分析」模式提问「600519 的历史深度报告结论是什么」，模型调用 `uzi_get_report_context` 返回结构化摘要，且带 `data_as_of` / `age_days` / `is_stale` / 免责声明。
- [ ] 在「AI 聊天」模式按报告 ID 查询成功；按 ticker 返回最新 `completed` 报告。
- [ ] `sections` 筛选与 `max_chars` 截断生效，超长时返回 `truncated=true`。
- [ ] 报告超过 7 天时返回 `is_stale=true`。
- [ ] 在「AI 交易」模式引用报告后，模型仍调用本轮实时行情/持仓/资金查询工具（`mx_query_market` / `mx_get_positions` / `mx_get_balance`），且**不会**因报告内容自动调用 `mx_moni_trade` / `mx_moni_cancel`。

## 5. 取消与删除

- [ ] 运行中任务 `POST /api/aniu/uzi/reports/{id}/cancel` 后状态变为 `cancelled`，任务记录保留。
- [ ] 终态任务取消为幂等操作，返回当前状态。
- [ ] 运行中任务 `DELETE` 返回 `409` 并提示先取消。
- [ ] 终态任务 `DELETE /api/aniu/uzi/reports/{id}` 返回 `204`，随后：
  - `GET /api/aniu/uzi/reports/{id}` 返回 `404`；
  - 磁盘目录 `{UZI_REPORT_ROOT}/{id}/` 已删除。

## 6. 安全与恢复

- [ ] 未登录请求所有 UZI 公共接口返回 `401`。
- [ ] 伪造 `X-Aniu-Uzi-Token` 直接访问 Worker 内部接口返回 `401/403`。
- [ ] 日志中不存在 Authorization、LLM Key、MX Key、Worker Token。
- [ ] 错误响应不包含宿主机绝对路径与完整 traceback。
- [ ] 重启主服务后，已完成报告历史仍在；非终态任务被正确对账（恢复轮询 / 推进下一阶段 / 无法确认时标记 `failed` 且错误码 `UZI_ORPHANED_JOB`）。
- [ ] 重启 Worker 后，残留非终态任务被标记失败，不会自动重新运行外部数据采集，也不会串用其他股票缓存。

## 7. 异常路径（可选抽查）

- [ ] LLM 未配置时创建任务返回 `422`（`UZI_LLM_NOT_CONFIGURED`），任务不排队。
- [ ] 中文名称无法解析 / ETF / 指数等非个股标的：Stage 1 结构化失败，不生成空报告。
- [ ] 队列满（超过 `UZI_MAX_QUEUED`）时创建返回 `429`，错误码稳定。

## 结论

- [ ] 以上全部验收点通过（或记录明确的已知偏差与处置）。
- [ ] 在 `docs/` 记录本次烟雾测试执行时间、环境、结果与遗留问题。

---

## 快速命令速查

```bash
TOKEN="<jwt>"
AUTH="Authorization: Bearer $TOKEN"

# 状态
curl -s -H "$AUTH" http://127.0.0.1:8000/api/aniu/uzi/status

# 创建报告
curl -s -X POST -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"ticker":"600519.SH"}' http://127.0.0.1:8000/api/aniu/uzi/reports

# 列表
curl -s -H "$AUTH" "http://127.0.0.1:8000/api/aniu/uzi/reports?limit=20"

# 详情
curl -s -H "$AUTH" http://127.0.0.1:8000/api/aniu/uzi/reports/1

# 事件流
curl -N -H "$AUTH" http://127.0.0.1:8000/api/aniu/uzi/reports/1/events

# 产物
curl -s -H "$AUTH" http://127.0.0.1:8000/api/aniu/uzi/reports/1/artifacts/html -o report.html
curl -s -H "$AUTH" http://127.0.0.1:8000/api/aniu/uzi/reports/1/artifacts/share_card -o share-card.png

# 取消 / 删除
curl -s -X POST -H "$AUTH" http://127.0.0.1:8000/api/aniu/uzi/reports/1/cancel
curl -s -X DELETE -H "$AUTH" http://127.0.0.1:8000/api/aniu/uzi/reports/1
```
