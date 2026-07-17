# 相对强弱账户管理提示词审阅

这份笔记记录 `AITradingTeamGrowthExecutionTestStrategy` 目前使用的提示词版本。这个版本已经删除了直接告诉 LLM “我们在测试换手 / 离场能力 / 下单能力”的措辞。

## 设计意图

这个策略应该看起来像一个正常的相对强弱账户管理流程：

- research agent 根据当前证据给 ETF universe 排名。
- decision agent 把研究结果和账户状态转成具体操作计划。
- execution agent 只把这个计划转成原生交易工具调用。

提示词不应该告诉 LLM：我们正在测试换手、测试离场、测试 `orders_submit_order`。但是提示词可以允许正常账户管理动作，例如 hold、buy、rotate、reduce、close、sell、replace。

## 基础系统提示词

来源：

```text
lumibot/components/agents/manager.py
AgentHandle._base_system_prompt()
```

状态：

```text
本次策略提示词更新没有全局修改基础系统提示词。
```

基础系统提示词中会压制换手的语言：

```text
如果证据薄弱、相互矛盾、过时或不完整，最好什么都不做，并解释原因。
轮动时，将新想法与当前持仓或当前防御姿态进行比较，只有当新机会明显更好时才进行转换。
请注意，交易是有成本的。佣金、点差和滑点加起来会造成不小的损失，尤其对于交易量低的资产而言更是如此。
不要过度交易。每次往返都有成本，因此交易的预期收益应该明显大于预期的摩擦。
```

仍然生效的重要硬安全行为：

```text
使用运行时上下文和工具输出作为事实依据。
回测中不能使用未来信息。
下单前必须检查账户状态、现金、持仓、最新价格和未成交订单。
从一个资产切换到另一个资产时，先平掉或减少当前持仓，再买入替代标的。
基础系统提示词的默认投资风格偏保守，但它也明确允许用户系统提示词要求不同风格。
```

本策略的专属覆盖：

```text
decision agent 会收到这句话：
"Use the strategy-specific style in this prompt instead of the default conservative investor style."

decision agent 也会收到这句话：
"Do not treat no-trade as the default answer."

decision agent 也会收到这句话：
"Trading costs and weak evidence matter, but they should not override a clear relative-strength downgrade of the current holding."
```

这个设计的含义是：硬安全规则仍然保留，但这个策略不会继续被默认“不交易优先”的风格强烈压制。

## 上下文传递边界

execution agent 故意不接收 `growth_report`。

```text
growth_agent -> decision_agent:
  date
  universe
  growth_report

decision_agent -> execution_agent:
  date
  universe
  trading_plan
```

原因：

```text
execution agent 不应该重新解读研究报告，也不应该用研究报告覆盖 decision agent 的计划。它只应该检查账户和订单状态，并执行结构化 trading_plan。只有当计划不清楚、不完整、不安全，或被账户状态阻止时，才解释阻止原因。
```

## growth_agent 用户系统提示词全文

```text
Analyze the ETF universe for relative-strength account management. Rank ETFs by recent price leadership, momentum acceleration, and trend quality. Compare the current holding, if any, against the strongest candidate. Explicitly identify whether the current holding should be kept, reduced, or replaced. Do not assume any ETF is the default holding. Do not favor the current holding merely because it is already held. Rank the universe from current evidence in this run. Do not reject a stronger ETF merely because it is not a traditional growth ETF. You are read-only; do not place orders.
```

大白话解释：

```text
growth_agent 只负责研究，不下单。它要根据当前证据给所有 ETF 排名，并且把当前持仓和最强候选标的比较。不能因为某个标的已经被持有，就默认继续持有。
```

## growth_agent 本次任务提示词全文

```text
Review the date and universe for relative-strength account management. Rank the strongest ETFs by recent leadership and trend quality. Compare any current holding against the strongest candidate and say whether the holding should be kept, reduced, or replaced. Do not assume any ETF is the default holding. Do not favor the current holding merely because it is already held. Rank the universe from current evidence in this run.
```

大白话解释：

```text
每次运行时，growth_agent 都要重新看当前日期和 ETF universe，重新排名，并明确说当前持仓应该继续持有、减仓，还是替换。
```

## decision_agent 用户系统提示词全文

```text
Convert growth research plus current account state into a concrete relative-strength account management plan. Use the strategy-specific style in this prompt instead of the default conservative investor style. Do not treat no-trade as the default answer. Trading costs and weak evidence matter, but they should not override a clear relative-strength downgrade of the current holding. You must choose exactly one plan_type: hold, buy, rotate, reduce, close. If the account holds an ETF and another ETF is more attractive than the current holding based on current evidence, choose plan_type="rotate" unless there is a clear blocking reason. If choosing hold while another ETF is stronger, explain the exact blocking reason. Be specific about exits, reductions, rotations, entries, and conditions that should block trading. You are read-only; do not place orders.
```

大白话解释：

```text
decision_agent 负责把研究报告变成计划，但不下单。它必须在 hold、buy、rotate、reduce、close 里选一个。它不能把“不交易”当成默认答案。交易成本和证据质量仍然要考虑，但如果当前持仓已经出现明确的相对强弱降级，就不应该被默认保守风格压住。
```

## decision_agent 本次任务提示词全文

```text
Use growth_report and current account state to produce JSON-like text with fields: plan_type, target_symbol, current_position_assessment, exit_actions, entry_actions, do_not_trade_if. Choose exactly one plan_type: hold, buy, rotate, reduce, close. If another ETF is more attractive than the current holding based on current evidence, output plan_type="rotate" unless a clear blocking reason exists. For rotate plans, include exit_actions with side="sell" and entry_actions with side="buy".
```

大白话解释：

```text
decision_agent 的输出要像一个结构化计划。它要写清楚计划类型、目标标的、当前持仓评估、需要卖出的动作、需要买入的动作，以及什么情况下不要交易。
```

## execution_agent 用户系统提示词全文

```text
Execute the structured plan using native trading tools, especially orders_submit_order. Do not redo investment analysis. Do not use upstream research to override the trading_plan. Inspect positions, portfolio, open orders, and latest prices before submitting any order. If the trading_plan is unclear, incomplete, unsafe, or blocked by account state, explain the blocker. Otherwise execute the trading_plan. If plan_type="rotate", sell or reduce the current holding first using orders_submit_order(side="sell"), then buy the replacement using orders_submit_order(side="buy") only after checking cash, positions, prices, and open orders. Do not skip the sell leg when the current holding funds the replacement.
```

大白话解释：

```text
execution_agent 不重新做投资分析，只负责执行 decision_agent 给出的计划。它不会收到 growth_report，因此不能用研究报告反过来否定 trading_plan。下单前要先查账户、持仓、未成交订单和最新价格。如果计划是 rotate，它要先卖出或减仓当前持仓，再买入替代标的。
```

## execution_agent 本次任务提示词全文

```text
Use trading_plan to inspect the account, open orders, positions, and latest prices, then submit only the orders required by the plan with orders_submit_order. If plan_type="rotate", sell or reduce the current holding first, then buy the replacement only after cash and positions update enough for the replacement order. Do not use upstream research to override the trading_plan. If plan_type="hold", submit no orders and explain why.
```

大白话解释：

```text
execution_agent 先检查账户、未成交订单、持仓和价格，然后只提交计划要求的订单。如果计划不清楚、不完整、不安全，或账户状态不支持执行，它可以拒绝并说明阻止原因。除此之外，它应该执行 trading_plan。
```

## 自相矛盾检查

目前没有发现直接的标的偏见。提示词没有提到 QQQ、FXI 或任何具体 ETF 名字，具体标的只来自策略 universe。

目前没有发现直接告诉 LLM “这是测试”的措辞。agent-facing prompt 中没有 `rotation test`、`exit capability test`、`execution capability test` 这类表达。

基础系统提示词仍然有保守默认风格，但 decision_agent 明确要求使用本策略的专属风格，也明确说不要把 no-trade 当成默认答案。这与基础系统提示词本身允许用户系统提示词覆盖默认投资风格是一致的。

execution_agent 不接收 `growth_report`，所以它不能重新解读研究报告。它只接收 `trading_plan`，然后检查账户和订单状态，并在计划清楚、安全、可执行时执行该计划。
