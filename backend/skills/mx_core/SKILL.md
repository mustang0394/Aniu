---
name: mx_core
description: 东方财富妙想股票行情、资讯、选股与A股模拟交易核心工具集
always: true
metadata:
  aniu:
    handler_module: skills.mx_core.handler
    run_types: [analysis, trade, chat]
    category: finance
---

# 妙想核心技能（mx_core）

提供基于东方财富妙想 OpenAPI 的数据查询与 A 股模拟盘操作工具。

## 工具总览

- `mx_query_market`：权威行情 / 财务 / 关系类结构化数据查询
- `mx_search_news`：金融资讯、研报、公告、政策检索
- `mx_screen_stocks`：自然语言选股
- `mx_get_positions` / `mx_get_balance` / `mx_get_orders`：模拟组合持仓、资金、委托
- `mx_get_self_selects` / `mx_manage_self_select`：自选股读取与维护
- `mx_moni_trade` / `mx_moni_cancel`：A 股模拟交易下单与撤单

## 使用建议

- 数量必须是 100 的整数倍；LIMIT 委托必须附带有效价格。
- 撤单优先按委托编号，撤单前建议先 `mx_get_orders` 查到最新 order_id。

## 交易执行铁律

当你处于 trade 模式时，以下规则不可违反：

1. **判断买入 → 必须调用 `mx_moni_trade`（action="BUY"）函数**
2. **判断卖出 → 必须调用 `mx_moni_trade`（action="SELL"）函数**
3. **判断撤单 → 必须调用 `mx_moni_cancel` 函数**
4. 只在文本中说「建议买入」「应该卖出」而不调用函数 = 交易不会发生，这等于是失职
5. 仅当判断应该继续持有、不做操作时，才可以在不调用交易工具的情况下直接输出结论

## 选股范围

系统在功能设置中配置了允许的市场范围（上证A股 / 深证A股 / 创业板 / 科创板 / 北交所）。

- 选股与**买入**必须落在允许范围内；服务端会对 `mx_moni_trade(BUY)` 做代码级拦截。
- **卖出 / 撤单**不受范围限制。
- 调用 `mx_screen_stocks` 时请在 query 中体现范围；服务端也会过滤候选。

## 资金封印

若功能设置启用了资金封印：

- `mx_get_balance` / 账户总览返回的总资产、可用资金等已是「真实值 − 封印」的可操作口径。
- 持仓明细市值不减封印；仓位比例按虚拟总资产重算。
- `mx_moni_trade(BUY)` 不得超过虚拟可用资金；卖出/撤单不受限。
- 必须以本轮工具返回为准，忽略历史对话中的过期资金数字。