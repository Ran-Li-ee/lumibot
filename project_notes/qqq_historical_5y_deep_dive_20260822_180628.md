# QQQ Historical Equity-Only LLM 5Y Deep Dive

Artifact: `C:\Users\Ran\.config\superpowers\worktrees\lumibot\optimize-equity-only-llm\artifacts\ai_trading_team_example_benchmarks\20260822_180628_226869\qqq-historical-equity-only-llm`
Window: 2021-08-16 to 2026-08-13

## 1. Headline: The Outperformance Arrived Late

- Final multiple: Strategy 2.83x vs SPY 1.86x.
- Final relative ratio Strategy/SPY: 1.52x.
- First dates where Strategy crossed above SPY: 2021-08-17, 2021-08-23, 2021-10-27, 2022-04-18, 2022-04-21, 2022-04-29, 2022-05-05, 2022-06-13.
- First date after which Strategy stayed above SPY through the end: 2026-04-02.
- Worst drawdown: Strategy -37.2% on 2022-10-14, SPY -24.5% on 2022-10-12.

## 2. Annual Returns
| date   |   Strategy |     SPY |
|:-------|-----------:|--------:|
| 2021   |     17.34% |   6.99% |
| 2022   |    -26.21% | -18.63% |
| 2023   |     44.81% |  26.74% |
| 2024   |      9.70% |  25.59% |
| 2025   |     18.38% |  18.00% |
| 2026   |     74.89% |  14.47% |

## 3. Annual Excess vs SPY
| date   |   Strategy_minus_SPY |
|:-------|---------------------:|
| 2021   |               10.34% |
| 2022   |               -7.58% |
| 2023   |               18.07% |
| 2024   |              -15.89% |
| 2025   |                0.37% |
| 2026   |               60.42% |

## 4. Longest Periods Below SPY
| start      | end        |   days |   rel_start |   rel_end |
|:-----------|:-----------|-------:|------------:|----------:|
| 2024-12-31 | 2025-11-06 |    310 |       0.985 |     0.971 |
| 2023-01-06 | 2023-05-25 |    139 |       0.971 |     0.945 |
| 2024-09-09 | 2024-12-09 |     91 |       1.000 |     0.983 |
| 2022-06-29 | 2022-09-21 |     84 |       0.995 |     0.989 |
| 2022-01-24 | 2022-04-14 |     80 |       0.941 |     0.990 |
| 2025-11-10 | 2025-12-30 |     50 |       0.956 |     0.984 |
| 2021-10-04 | 2021-10-26 |     22 |       0.984 |     0.996 |
| 2022-05-20 | 2022-06-10 |     21 |       0.992 |     0.999 |

## 5. Worst Strategy-vs-SPY Months
| date    |   Strategy_minus_SPY |
|:--------|---------------------:|
| 2026-07 |              -19.44% |
| 2022-01 |              -16.80% |
| 2026-08 |              -10.94% |
| 2021-12 |               -8.96% |
| 2024-07 |               -8.96% |
| 2025-01 |               -8.94% |
| 2025-11 |               -7.50% |
| 2024-09 |               -6.84% |
| 2025-08 |               -6.47% |
| 2022-05 |               -6.21% |
| 2022-07 |               -5.82% |
| 2023-01 |               -4.44% |
| 2025-07 |               -4.43% |
| 2023-02 |               -3.83% |
| 2024-06 |               -3.60% |

## 6. Best Strategy-vs-SPY Months
| date    |   Strategy_minus_SPY |
|:--------|---------------------:|
| 2026-05 |               31.86% |
| 2026-04 |               22.40% |
| 2023-05 |               20.32% |
| 2026-01 |               17.87% |
| 2023-11 |               14.72% |
| 2025-10 |               11.55% |
| 2026-06 |               11.48% |
| 2021-11 |               10.60% |
| 2025-12 |                8.97% |
| 2022-04 |                8.37% |
| 2022-08 |                6.34% |
| 2021-08 |                5.34% |
| 2024-11 |                4.06% |
| 2025-06 |                3.93% |
| 2025-09 |                3.92% |

## 7. Realized PnL By Symbol (Approx From Filled Trades)
This excludes unrealized PnL on final open lots unless sold during the window.

### Top realized winners
| symbol   |   realized_pnl |   open_qty |   open_cost |   traded_value |   sell_wins |   sell_losses |
|:---------|---------------:|-----------:|------------:|---------------:|------------:|--------------:|
| MU       |       62901.82 |       0.00 |        0.00 |      733984.10 |          34 |             3 |
| NVDA     |       34240.92 |       0.00 |        0.00 |      592072.35 |          45 |             5 |
| STX      |       32765.48 |       0.00 |        0.00 |      356737.78 |           8 |             2 |
| MRVL     |       16786.24 |     272.00 |    49101.44 |      472662.19 |          12 |             4 |
| PLTR     |       14922.57 |       0.00 |        0.00 |      228690.99 |          16 |             2 |
| ZS       |       13753.08 |       0.00 |        0.00 |      245821.01 |          13 |             1 |
| LRCX     |       12900.76 |       0.00 |        0.00 |      334371.68 |          14 |             1 |
| CRWD     |       12276.38 |     251.00 |    56224.00 |      351695.70 |          14 |             4 |
| WDC      |       11414.52 |       0.00 |        0.00 |      395889.35 |           6 |             4 |
| INTC     |       10822.20 |       0.00 |        0.00 |      265432.22 |           6 |             1 |
| META     |        9214.74 |       0.00 |        0.00 |       83018.58 |          11 |             0 |
| CEG      |        5468.31 |       0.00 |        0.00 |      191564.39 |          20 |             2 |
| WBD      |        5397.45 |       0.00 |        0.00 |      202059.93 |           9 |             1 |
| FTNT     |        4094.37 |     345.00 |    56317.80 |      384285.25 |           9 |             3 |
| DDOG     |        3553.48 |     219.00 |    56201.97 |      182891.69 |           2 |             0 |
| VRTX     |        3002.51 |       0.00 |        0.00 |      308623.05 |          17 |             2 |
| CPRT     |        2949.45 |       0.00 |        0.00 |       76901.07 |           6 |             0 |
| GILD     |        2884.69 |       0.00 |        0.00 |      258862.11 |          11 |             8 |
| ISRG     |        2854.75 |       0.00 |        0.00 |      364542.08 |           5 |             3 |
| PDD      |        2428.10 |       0.00 |        0.00 |      337581.90 |          11 |             7 |

### Worst realized losers
| symbol   |   realized_pnl |   open_qty |   open_cost |   traded_value |   sell_wins |   sell_losses |
|:---------|---------------:|-----------:|------------:|---------------:|------------:|--------------:|
| KLAC     |      -12033.52 |       0.00 |        0.00 |      278531.54 |           1 |             5 |
| PANW     |      -11877.54 |     146.00 |    56093.20 |      480696.73 |           2 |             5 |
| TSLA     |      -11788.87 |       0.00 |        0.00 |      438761.87 |           8 |             7 |
| AMD      |       -5644.76 |       0.00 |        0.00 |      663310.20 |          21 |             6 |
| MRNA     |       -5507.71 |       0.00 |        0.00 |      125914.71 |           2 |             3 |
| GOOGL    |       -4958.24 |       0.00 |        0.00 |      401071.38 |           4 |             5 |
| AMAT     |       -4807.52 |       0.00 |        0.00 |      404674.44 |           6 |             4 |
| REGN     |       -4110.24 |       0.00 |        0.00 |      396686.00 |           3 |             6 |
| TEAM     |       -3665.53 |       0.00 |        0.00 |      104145.25 |           1 |             2 |
| ALGN     |       -3391.87 |       0.00 |        0.00 |      138109.83 |           1 |             3 |
| FANG     |       -3092.25 |       0.00 |        0.00 |      154055.87 |           1 |             3 |
| MELI     |       -2588.91 |       0.00 |        0.00 |      284662.03 |           2 |             5 |
| AEP      |       -2548.89 |       0.00 |        0.00 |      170788.91 |           8 |             3 |
| KHC      |       -2430.31 |       0.00 |        0.00 |       68948.24 |           1 |             2 |
| IDXX     |       -2329.93 |       0.00 |        0.00 |      185407.59 |           0 |             4 |
| VRSK     |       -2152.41 |       0.00 |        0.00 |      124795.95 |           0 |             3 |
| CHTR     |       -2139.38 |       0.00 |        0.00 |      126609.48 |           0 |             3 |
| ROST     |       -2051.33 |       0.00 |        0.00 |      192400.41 |           4 |             3 |
| FAST     |       -2009.98 |       0.00 |        0.00 |       97620.10 |           0 |             2 |
| NTES     |       -1941.39 |       0.00 |        0.00 |       30965.25 |           0 |             1 |

## 8. Selection Concentration
- Equity agent runs: 261
- Runs mentioning news/alpaca in summary text: 213

| symbol   |   selection_count |
|:---------|------------------:|
| NVDA     |                83 |
| MU       |                57 |
| PDD      |                42 |
| AMD      |                41 |
| VRTX     |                36 |
| CEG      |                34 |
| AVGO     |                32 |
| TMUS     |                32 |
| NFLX     |                32 |
| MRVL     |                31 |
| PLTR     |                31 |
| LRCX     |                29 |
| CRWD     |                28 |
| TSLA     |                27 |
| GILD     |                27 |
| META     |                27 |
| DASH     |                26 |
| EXC      |                25 |
| ZS       |                25 |
| FTNT     |                24 |
| REGN     |                23 |
| ORLY     |                22 |
| ADBE     |                20 |
| AEP      |                20 |
| STX      |                20 |
| DLTR     |                19 |
| BKNG     |                19 |
| WBD      |                19 |
| WDC      |                19 |
| ISRG     |                18 |

## 9. Execution Health
- execution_plan_execute tool results: 287
- completed count: 287
- NEGATIVE_CASH_NOT_ALLOWED count in trace: 0
- sell-only dates count: 27

## 10. Notes / Hypotheses
- The strategy did outperform SPY over the full window, but durable separation appears late rather than steady from inception.
- The realized winner table should be used as a clue, not a precise full attribution, because final open positions still contain unrealized gains/losses.
- If optimization continues, focus on reducing periods of SPY-like drift before 2026 and controlling late-cycle drawdowns without killing the upside engine.
