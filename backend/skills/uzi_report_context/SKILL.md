---
name: uzi_report_context
description: 读取已完成的 UZI 个股深度报告摘要，为 AniU 分析、交易或聊天提供带时间戳的历史研究参考。
metadata:
  aniu:
    handler_module: skills.uzi_report_context.handler
    run_types: [analysis, trade, chat]
    category: finance
---

# UZI 报告上下文（uzi_report_context）

读取已完成的 UZI 个股深度报告摘要，为分析、交易或聊天提供带时间戳的历史研究参考。

## 使用时机

- 只有用户问题与具体股票、历史深度报告或报告 ID 有关时才调用本工具。
- 优先按明确的 `report_id` 查询；否则按 `ticker` 获取该股票最新一份已完成报告。
- 未完成的报告（排队中、生成中、失败、已取消）不会返回。

## 重要边界

- 报告是**历史研究资料**，不是实时证据；报告中的行情、价格、资金数字均有时效性。
- 交易模式引用报告后，仍必须查询本轮实时行情、持仓、资金和必要委托，才能做出交易决策。
- 不得根据报告内容自动调用任何交易工具；本技能只读，不会触发下单、撤单或任何写操作。
- 报告超过 7 天视为过期（`is_stale=true`），引用时须标注数据时间。
