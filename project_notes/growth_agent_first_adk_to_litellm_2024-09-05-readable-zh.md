# Growth Agent ?? ADK -> LiteLLM ?? ???

?? JSON?`project_notes/growth_agent_first_adk_to_litellm_2024-09-05.json`

???????? `2024-09-05 09:30:00 -04:00` ???????`growth_agent` ????????Google ADK ?? LiteLLM ?????????

## 1. ??????????

| ?? | ???? | ??? |
|---|---|---|
| `capture_point` | trace ????????? | `observed_litellm_generate_content_async_entry` |
| `current_model_turn_id` | ????? agent ???????? | `1fb5906f50614a5089928a8bab528e15:turn:0001` |
| `previous_model_turn_id` | ????????????????? | `None` |
| `model` | ?????????? | `openai/gpt-5.4-mini` |
| `max_output_tokens` | ?????????? token | `65535` |
| `labels.adk_agent_name` | ADK ??? agent ?? | `growth_agent` |

## 2. ?????????

| ?? | ? JSON ???? | ???? |
|---|---|---|
| ???? | `llm_request.model`, `llm_request.config` | ?? LiteLLM / OpenAI ??????????????????????? |
| ????? | `llm_request.config.system_instruction` | ?? growth agent ?????????????????????????? DuckDB |
| ?????? | `llm_request.contents[0].parts[0].text` | ??????????????????????????????universe |
| ???? | `llm_request.config.tools[0].function_declarations` | ? LLM ????????????????????????? |
| context pruning | `context_pruning` | ???????????????????? |

## 3. ??????????

????????`14937` ??????????????? agent ?????????????

????????????????

- `DEFAULT INVESTOR POLICY - FOLLOW THIS UNLESS THE USER'S SYSTEM PROMPT CLEARLY ASKS FOR A DIFFERENT STYLE:`
- `RISK AND DRAWDOWN DISCIPLINE:`
- `POSITION SIZING AND ORDER EXECUTION:`
- `TOOL USAGE:`
- `BACKTESTING SAFETY RULES - READ THIS AS A HARD REQUIREMENT:`
- `USER SYSTEM PROMPT:`
- `DUCKDB SQL GUIDANCE`

?????????? agent?

- ??? LumiBot ???? agent?
- ???? runtime context ??????????????
- ?????????????????????
- ??????????????????
- ????????????????????????
- ???? DuckDB / history table ?????? SQL ??????????????????? `datetime` ?????
- growth agent ?????????????

## 4. ?????????????? agent ?????

???? JSON ?? `contents[0].parts[0].text`???????????????????????????

### 4.1 Runtime Context JSON???????

| ?? | ???? | ??? |
|---|---|---|
| `agent_name` | ?????? agent | `growth_agent` |
| `strategy_name` | ?????? | `AITradingTeamGrowthExecutionTestStrategy` |
| `mode` | ?????? | `backtesting` |
| `current_datetime` | ???????? | `2024-09-05T09:30:00-04:00` |
| `timezone` | ?????? | `America/New_York` |
| `account.cash` | ???? | `100000.0` |
| `account.portfolio_value` | ??????? | `100000.0` |
| `positions` | ???? | `[{"asset": {"asset_type": "forex", "expiration": null, "multiplier": 1, "right": null, "strike": 0.0, "symbol": "USD"}, "quantity": 100000.0}]` |
| `recent_orders` | ???? | `[]` |
| `recent_trades` | ???? | `[]` |

????????????????? `100000 USD` ???????/ETF ??????????????????

### 4.2 Available Tools JSON????????????

??? growth agent ????????`28`?

`account_portfolio`, `account_positions`, `close_thesis`, `duckdb_query`, `get_balance_sheet`, `get_cash_flow`, `get_company_facts`, `get_filing_document`, `get_filing_section`, `get_filings`, `get_income_statement`, `get_indicator`, `get_indicators`, `list_filing_sections`, `list_indicators`, `lumibot_docs_search`, `market_last_price`, `market_load_history_table`, `notify_user`, `open_thesis`, `orders_open_orders`, `remember`, `remember_lesson`, `remember_proposal`, `remember_risk_note`, `search_filing`, `search_memory`, `update_thesis`

?????????????????????????? `function_declarations` ??

### 4.3 Lumibot Memory State JSON???????

| ?? | ???? | ??? |
|---|---|---|
| `as_of` | ?????? | `"2024-09-05T09:30:00-04:00"` |
| `current_position_rationales` | ?????????? | `[]` |
| `held_symbols` | ?????????? | `[]` |
| `open_theses` | ?????????? | `[]` |
| `retrieval_policy` | ?????? | `"If an agent is holding a symbol and plans to add, reduce, or sell it, it should call search_memory for the open thesis first."` |
| `schema_version` | ?????? | `1` |
| `strategy` | ?????? | `"AITradingTeamGrowthExecutionTestStrategy"` |
| `validated_lessons` | ?????????? | `[]` |

???????????????? thesis????? lesson??? open thesis????? growth agent ???????????

### 4.4 Task???????

?????

```text
Review the date and universe for relative-strength account management. Rank the strongest ETFs by recent leadership and trend quality. Compare any current holding against the strongest candidate and say whether the holding should be kept, reduced, or replaced. Do not assume any ETF is the default holding. Do not favor the current holding merely because it is already held. Rank the universe from current evidence in this run. You cannot place orders, but you must still make a clear research recommendation, including whether cash should be deployed into the strongest ETF candidate.
```

?????

- ??????? ETF ??
- ???????????????????? ETF ???
- ??????????????????????????
- ?????? ETF ???????
- ?????????????????
- growth agent ????????????????
- ????????????????????? ETF ???

### 4.5 User Context JSON??????????

| ?? | ???? | ??? |
|---|---|---|
| `date` | ?????? | `2024-09-05` |
| `universe` | ??????? ETF / ??? | `SPY, QQQ, IWM, TLT, IEF, TIP, GLD, DBC, VNQ, UUP, FXI, EEM` |

## 5. ?????LLM ??????????

???????? `28` ? function declaration??? declaration ???? LLM?????????? schema?

| # | ??? | ???? | ???? |
|---:|---|---|---|
| 1 | `account_positions` | Return current positions as structured data. Each entry includes asset fields and quantity. Use this before trading to understand current exposure, whether a symbol is already held, and whether the current portfolio is concentrated. Example: call this before rotating into a new symbol so you can compare it against what is already owned. | ????? |
| 2 | `account_portfolio` | Return current cash and total portfolio value for sizing decisions. Use this before placing orders when you need to calculate a sensible whole-share quantity or compare a risky asset against a defensive parking asset. Example: call this before buying TQQQ so you can size a near-fully-invested position intentionally instead of buying one share. | ????? |
| 3 | `market_last_price` | Get the current last price for one asset. Arguments: symbol, asset_type, optional expiration/strike/right for derivatives, optional quote_symbol, optional exchange. Valid asset_type values: stock, option, future, cont_future, forex, crypto, index, multileg, us_equity. The symbol argument must be one tradable symbol, not a comma-separated universe; call once per symbol when comparing multiple assets. Use stock for ... | `asset_type`, `exchange`, `expiration`, `quote_symbol`, `right`, `strike`, `symbol` |
| 4 | `market_load_history_table` | Load visible historical bars into DuckDB and return the table metadata. Arguments: symbol, length, timestep, optional table_name, asset_type, quote_symbol, exchange, expiration, strike, right, include_after_hours. Valid asset_type values: stock, option, future, cont_future, forex, crypto, index, multileg, us_equity. The symbol argument must be the exact tradable symbol, such as XLY or SPY, not a generated table na... | `asset_type`, `exchange`, `expiration`, `include_after_hours`, `length`, `quote_symbol`, `right`, `strike`, `symbol`, `table_name`, `timestep` |
| 5 | `duckdb_query` | Run a read-only SQL query against tables previously loaded into DuckDB. Arguments: sql, optional limit. Load a table first with market_load_history_table, then analyze it here. Use exact column names from market_load_history_table or pragma_table_info('table_name'); do not invent columns. History tables loaded by market_load_history_table often use Date as the timestamp column; Do not invent datetime unless the sc... | `limit`, `sql` |
| 6 | `lumibot_docs_search` | Search LumiBot's local documentation and return the best matching snippets. Arguments: query, optional max_results or limit. Use this when you are unsure how a LumiBot tool, asset type, benchmark, or backtesting feature works. Example: lumibot_docs_search(query='run_backtest benchmark_asset SPY'). | `limit`, `max_results`, `query` |
| 7 | `list_indicators` | List common pandas-ta-classic indicator names available through Lumibot's current-bar indicator system. | ????? |
| 8 | `get_indicator` | Get one technical indicator for the current strategy datetime. Arguments: symbol, indicator, timestep='day', asset_type='stock', optional parameters_json as a JSON object string. Examples: get_indicator(symbol='SPY', indicator='rsi', parameters_json='{"length": 14}'); get_indicator(symbol='NVDA', indicator='macd'). In backtests this returns only the current-bar value and does not expose future bars. | `asset_type`, `indicator`, `parameters_json`, `symbol`, `timestep` |
| 9 | `get_indicators` | Get multiple current-bar technical indicators for one symbol. Pass indicators=['rsi', 'macd', 'bbands', ...]. | `asset_type`, `indicators`, `symbol`, `timestep` |
| 10 | `get_income_statement` | Get SEC income statement facts for a US equity, gated to as_of or the current strategy datetime. Fields are kept within one SEC filing/statement period when possible; mismatched old facts are omitted with warnings. | `as_of`, `raw`, `symbol` |
| 11 | `get_balance_sheet` | Get SEC balance sheet facts for a US equity, gated to as_of or the current strategy datetime. Fields are kept within one SEC filing/statement period when possible; mismatched old facts are omitted with warnings. | `as_of`, `raw`, `symbol` |
| 12 | `get_cash_flow` | Get SEC cash flow facts for a US equity, gated to as_of or the current strategy datetime. Fields are kept within one SEC filing/statement period when possible; mismatched old facts are omitted with warnings. | `as_of`, `raw`, `symbol` |
| 13 | `get_company_facts` | Get compact or raw SEC companyfacts for a US equity, gated to as_of or the current strategy datetime. Default output is capped to important/latest facts so agent runs stay within context; use max_facts or raw=True only when needed. | `as_of`, `max_facts`, `raw`, `symbol` |
| 14 | `get_filings` | List SEC filings for a US equity, point-in-time gated by as_of/current strategy datetime. Use form='10-K' or form='10-Q' when you need annual or quarterly reports. | `as_of`, `form`, `limit`, `symbol` |
| 15 | `search_filing` | Keyword-search a cached SEC filing document and return matching context snippets. Use after get_filings when you want annual-report details about risks, margins, debt, accounting, customers, liquidity, guidance, dilution, buybacks, or management commentary. | `accession_number`, `max_results`, `primary_document`, `query`, `symbol` |
| 16 | `get_filing_document` | Read a SEC filing document as text. This can be large, so prefer search_filing first. Use max_chars to bound context, or set max_chars=None only when you intentionally need the full document. | `accession_number`, `max_chars`, `primary_document`, `symbol` |
| 17 | `list_filing_sections` | List detected sections in a SEC filing, such as item_1a risk factors, item_7 MD&A, item_7a market risk, and item_8 financial statements. Use after get_filings before reading a long report. | `accession_number`, `primary_document`, `symbol` |
| 18 | `get_filing_section` | Read one sanitized text section from a SEC filing without loading the whole report. Useful section values include risk_factors, mda, liquidity, results_of_operations, market_risk, financial_statements, controls, or exact IDs like item_1a and item_7. | `accession_number`, `max_chars`, `primary_document`, `section`, `symbol` |
| 19 | `notify_user` | Send a user notification through configured Lumibot notification providers. Backtests keep notifications disabled by default unless enabled=True is passed or notifications are configured as enabled. | `enabled`, `message`, `severity`, `title` |
| 20 | `remember` | Store a local Lumibot agent memory or note. | `kind`, `tags`, `text` |
| 21 | `search_memory` | Search local Lumibot agent memories, lessons, decisions, and theses. Use symbol/status filters when checking an open thesis for a held position. | `kind`, `limit`, `query`, `status`, `symbol` |
| 22 | `remember_proposal` | Record a research proposal or non-final trade idea without marking it as an executed trading decision. | `action`, `symbol`, `tags`, `text` |
| 23 | `remember_risk_note` | Record a compact risk note or bear-case memory without marking it as an executed trading decision. | `symbol`, `tags`, `text` |
| 24 | `remember_lesson` | Record a compact trading lesson for future agent runs. | `symbol`, `text` |
| 25 | `open_thesis` | Open a hedge-fund-style investment thesis in local Lumibot memory. | `symbol`, `tags`, `text` |
| 26 | `update_thesis` | Append an update to an open investment thesis. | `text`, `thesis_id` |
| 27 | `close_thesis` | Close an investment thesis and record its outcome/reflection. | `text`, `thesis_id` |
| 28 | `orders_open_orders` | List the strategy's currently tracked orders, including identifiers, status, side, quantity, and prices. | ????? |

## 6. context_pruning?????????

- `pruned`: `False`
- `reason`: `not_pruned`
- `omitted_function_response_count`: `0`
- `omitted_function_response_bytes`: `0`

???????? growth agent ??????????????????????????????????

## 7. ??????

?? ADK -> LiteLLM ????????????????????????

```text
????
+ ?????
+ ?????? / runtime context
+ ????????
+ memory ??
+ ????
+ ?? universe
```

LiteLLM ??????????????? OpenAI API ??????????? OpenAI LLM?