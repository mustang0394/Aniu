"""aniu-uzi-worker 应用包。

独立于 AniU 主后端运行的 UZI 执行节点：

- 只监听 Docker 内部网络，通过共享密钥与主服务通信。
- 无业务数据库，不持有 LLM Key，不访问 AniU SQLite。
- 负责运行 UZI Stage 1（采集与机械评分）与 Stage 2（综合与报告渲染）。
"""