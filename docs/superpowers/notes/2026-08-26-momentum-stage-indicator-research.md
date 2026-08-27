# Momentum Stage Indicator Research

Date: 2026-08-26

Purpose: collect candidate indicators for improving the equity-only LLM stock selection engine. The goal is to move beyond "pick the highest current rank" and help the system distinguish early/mid-stage momentum from overextended late-stage momentum.

## Core Hypothesis

The current rank layer may over-prefer stocks that already had the strongest recent move. That can select stale or overextended momentum, where the most profitable part of the move has already happened.

The desired improvement is not to avoid strong stocks. The desired improvement is to identify strong stocks whose trend is confirmed but not exhausted.

## Research Themes

| Theme | Plain-English Goal | Main References / Inspiration |
| --- | --- | --- |
| Fresh vs stale momentum | Prefer momentum that is early in its trajectory, not long-crowded stale momentum. | Research Affiliates, "Can Momentum Investing Be Saved?" |
| 52-week high momentum | Use distance to the recent high as a separate signal from raw past return. | George and Hwang, "The 52-Week High and Momentum Investing" |
| Smooth momentum | Prefer gradual, continuous strength over one-off dramatic jumps. | Da, Gurun, Warachka, "Frog in the Pan" |
| Momentum crash risk | Avoid naive momentum exposure when reversals/crash states are more likely. | Daniel and Moskowitz, "Momentum Crashes" |
| Residual momentum | Prefer stock-specific strength, not just market or sector beta. | Blitz, Huij, Martens, "Residual Momentum" |
| Stage analysis | Classify whether a stock is basing, advancing, topping, or declining. | Stan Weinstein stage analysis / 30-week moving average framework |
| Pullback / breakout quality | Prefer strong stocks that consolidate constructively or break out with confirmation. | Minervini VCP, CANSLIM, practical trend-following frameworks |

## Candidate Indicator Families

### 1. Trend Age

Purpose: estimate how long the current uptrend has already been running.

Possible metrics:
- Days since price first crossed above the 200-day moving average.
- Days since 50-day moving average crossed above 200-day moving average.
- Days since a new 3-month or 6-month high breakout.
- Number of consecutive weeks above a rising 30-week moving average.

Interpretation:
- Too young can mean unconfirmed.
- Middle-aged can mean confirmed trend with room left.
- Very old can mean crowded or late-stage unless still supported by other evidence.

Candidate indicators to evaluate:
- `days_above_ma200`: number of trading days since close first moved above the 200-day moving average.
- `days_since_ma50_above_ma200`: number of trading days since the 50-day moving average moved above the 200-day moving average.
- `weeks_above_rising_ma30w`: number of completed weeks above a rising 30-week moving average.
- `stage2_score`: boolean or score based on price above 50/150/200-day moving averages, moving average alignment, and rising long moving average.

Borrowed idea:
- Weinstein stage analysis and Minervini trend template both use long moving-average structure to confirm that the stock is in an advancing phase, not merely bouncing for a few days.

### 2. Rank Change

Purpose: detect stocks that are improving quickly in relative strength, not merely stocks that have been rank 1 for a long time.

Possible metrics:
- Current rank minus rank 4 weeks ago.
- Current rank minus rank 12 weeks ago.
- Percentile improvement over 1, 3, and 6 months.
- Number of weeks spent in top decile.

Interpretation:
- A stock climbing from middle rank to top rank may be fresher than a stock that has sat at rank 1 for months.
- A stock that suddenly jumps to rank 1 after one violent move may need smoothness and overextension checks.

Candidate indicators to evaluate:
- `rank_delta_4w`: rank from 4 weeks ago minus current rank. Positive means improvement.
- `rank_delta_12w`: rank from 12 weeks ago minus current rank.
- `percentile_delta_4w`: current percentile minus percentile 4 weeks ago.
- `top_decile_age_weeks`: consecutive weeks in the top 10%.
- `fresh_momentum_flag`: improved into top group recently, but has not stayed there too long.

Borrowed idea:
- Research Affiliates' fresh versus stale momentum framing suggests that when a stock entered the momentum group matters, not only whether it is in the group today.

### 3. Price Extension

Purpose: detect when price is too far above normal trend levels.

Possible metrics:
- Percent distance from 20-day, 50-day, and 200-day moving averages.
- Z-score of price distance from moving average.
- Close relative to upper Bollinger Band.
- Current price divided by ATR-adjusted moving average distance.

Interpretation:
- Moderate extension can confirm strength.
- Extreme extension can indicate fish-tail risk: the stock is strong, but entry may be late.

Candidate indicators to evaluate:
- `extension_ma20_pct`: close / 20-day moving average - 1.
- `extension_ma50_pct`: close / 50-day moving average - 1.
- `extension_ma200_pct`: close / 200-day moving average - 1.
- `atr_extension_20d`: distance from 20-day moving average divided by ATR.
- `bollinger_position_20d`: close position inside or above 20-day Bollinger Bands.

Borrowed idea:
- Practical momentum traders commonly treat extreme distance from major moving averages as overextension risk. This is not the same as "near a 52-week high"; a stock can be near highs but not dangerously stretched.

### 4. Smoothness

Purpose: distinguish steady accumulation from one-off spikes.

Possible metrics:
- Ratio of cumulative return to realized volatility.
- Percent of positive days in the lookback window.
- Average up-day size vs average down-day size.
- Information discreteness proxy: whether return came from many small positive days or a few large jump days.
- Maximum single-day contribution to total lookback return.

Interpretation:
- Smooth strength is more likely to persist.
- A stock whose return came mostly from one jump may be more vulnerable to reversal.

Candidate indicators to evaluate:
- `positive_day_ratio_3m`: share of positive days in the last 3 months.
- `return_to_volatility_3m`: cumulative 3-month return divided by realized volatility.
- `max_day_return_share_3m`: largest one-day return divided by total 3-month return.
- `information_discreteness_proxy`: high when returns are concentrated in a few large moves; low when gains arrive gradually.
- `up_down_consistency_score`: combines positive-day ratio and low max-day concentration.

Borrowed idea:
- Frog-in-the-pan momentum argues that continuous, small pieces of information can produce more persistent momentum than dramatic, attention-grabbing moves.

### 5. Pullback Quality

Purpose: identify healthy pauses inside an uptrend.

Possible metrics:
- Pullback depth from recent high.
- Pullback duration in days.
- Whether pullback holds above 20-day or 50-day moving average.
- Volume on down days vs volume on up days during pullback.
- Whether price recovers above short-term moving average after pullback.

Interpretation:
- A shallow, orderly pullback inside a strong trend can be a better entry than chasing a vertical move.
- A deep pullback on heavy selling volume may indicate distribution rather than opportunity.

Candidate indicators to evaluate:
- `drawdown_from_20d_high`: close / 20-day high - 1.
- `drawdown_from_60d_high`: close / 60-day high - 1.
- `pullback_days_since_high`: trading days since recent high.
- `pullback_holds_ma20`: whether close remains above or near the 20-day moving average.
- `pullback_holds_ma50`: whether close remains above or near the 50-day moving average.
- `down_volume_ratio_pullback`: down-day volume during pullback divided by average volume.
- `recovery_from_pullback_score`: whether price has started recovering after the pullback.

Borrowed idea:
- Minervini/VCP and many practical trend-following setups prefer strong stocks after constructive consolidation, not after vertical extension.

### 6. 52-Week High Distance

Purpose: capture whether a stock is near its prior high, a classic momentum signal.

Possible metrics:
- Current close / 252-day high - 1.
- Distance from 52-week high percentile rank.
- Whether price made a new 52-week high in the last N days.

Interpretation:
- Near-high stocks can keep outperforming.
- This should be combined with extension and smoothness to avoid buying an exhausted vertical spike.

Candidate indicators to evaluate:
- `distance_to_252d_high_pct`: close / 252-day high - 1.
- `days_since_252d_high`: days since the most recent 252-day high.
- `new_high_recent_20d`: whether the stock made a 252-day high in the last 20 trading days.
- `high_low_range_position_252d`: where close sits between 252-day low and 252-day high.

Borrowed idea:
- George and Hwang found nearness to the 52-week high to be an important momentum signal separate from simple past return.

### 7. Short-Term Reversal Risk

Purpose: reduce buying immediately after short-term overreaction.

Possible metrics:
- 1-week return rank.
- 1-month return rank.
- RSI 14 and RSI 5.
- Gap-up frequency and size.
- Return over the most recent month compared with 12-to-2-month momentum.

Interpretation:
- Very strong 1-week or 1-month return can be a warning if intermediate momentum is not also strong.
- This relates to the classic "skip-month" idea in momentum research.

Candidate indicators to evaluate:
- `return_5d`: most recent 5 trading-day return.
- `return_20d`: most recent 20 trading-day return.
- `rsi_14`: standard 14-period RSI.
- `rsi_5`: short RSI for near-term overheat.
- `gap_up_count_20d`: number of large positive gaps in the last 20 trading days.
- `recent_vs_intermediate_momentum`: recent 1-month return compared with 12-to-2-month or 12-to-7-month momentum.

Borrowed idea:
- Classic momentum commonly skips the most recent month, partly to reduce short-term reversal noise. Novy-Marx's intermediate momentum work suggests that older intermediate returns can matter more than the latest return.

### 8. Volume Confirmation

Purpose: check whether price strength is supported by trading activity.

Possible metrics:
- Up-day volume / down-day volume.
- Current volume relative to 20-day average volume.
- Volume trend over the last 20, 60, and 120 trading days.
- Accumulation/distribution style metrics.
- Breakout-day volume relative to average volume.

Interpretation:
- Rising price on supportive volume is stronger evidence.
- Weak volume during rallies or heavy volume during declines can weaken the signal.

Candidate indicators to evaluate:
- `relative_volume_20d`: latest volume / 20-day average volume.
- `up_down_volume_ratio_60d`: volume on positive-return days divided by volume on negative-return days.
- `volume_trend_60d`: slope of average volume over recent period.
- `breakout_volume_ratio`: volume on breakout day divided by average volume.
- `price_volume_confirmation_score`: price trend score adjusted by up/down volume behavior.

Borrowed idea:
- Lee and Swaminathan show that volume helps explain the magnitude and persistence of price momentum. Practical trend frameworks also use volume to distinguish accumulation from weak rallies.

### 9. Residual Momentum

Purpose: separate stock-specific strength from broad market or industry strength.

Possible metrics:
- Return minus QQQ return over the same period.
- Return minus SPY return over the same period.
- Return minus sector/industry ETF return where available.
- Regression residual return versus benchmark over 6-12 months.
- Idiosyncratic momentum standardized by residual volatility.

Interpretation:
- A stock that beats QQQ/SMH/SPY may have its own strength.
- This matters if the whole basket is rising together and we need to identify the true leader.

Candidate indicators to evaluate:
- `excess_return_vs_spy_3m`, `excess_return_vs_spy_6m`, `excess_return_vs_spy_12m`.
- `excess_return_vs_qqq_3m`, `excess_return_vs_qqq_6m`, `excess_return_vs_qqq_12m`.
- `residual_momentum_12m1m`: residual return after regressing against a benchmark, excluding the most recent month.
- `residual_return_to_residual_volatility`: stock-specific return divided by stock-specific volatility.

Borrowed idea:
- Residual momentum research argues that removing broad factor or market exposure can produce more consistent momentum signals.

## Candidate Development Priority

The first implementation should avoid trying to build every metric above. A practical first pass could be:

1. `rank_delta_4w` and `top_decile_age_weeks` for freshness.
2. `extension_ma50_pct` and `atr_extension_20d` for overextension.
3. `positive_day_ratio_3m` and `max_day_return_share_3m` for smoothness.
4. `distance_to_252d_high_pct` for 52-week high momentum.
5. `recent_vs_intermediate_momentum` for short-term reversal risk.
6. `up_down_volume_ratio_60d` for volume confirmation.
7. `excess_return_vs_qqq_6m` and `excess_return_vs_spy_6m` for residual/relative momentum.

This keeps the first version focused: enough to distinguish fresh, smooth, confirmed momentum from stale or overextended momentum without flooding the LLM with too many numbers.

## Development Implications

The next rank layer should avoid one single composite score at first. It should expose each indicator family separately so the LLM can see why a stock is strong:

- raw momentum strength,
- freshness,
- trend stage,
- overextension risk,
- smoothness,
- pullback quality,
- volume support,
- benchmark-relative strength.

The LLM prompt should not say "pick the highest rank." It should say to prefer confirmed but not exhausted strength, and to penalize candidates whose strength appears stale, overextended, or driven by one-off jumps.

## Sources

- Research Affiliates, "Can Momentum Investing Be Saved?"
- George and Hwang, "The 52-Week High and Momentum Investing"
- Da, Gurun, Warachka, "Frog in the Pan: Continuous Information and Momentum"
- Daniel and Moskowitz, "Momentum Crashes"
- Blitz, Huij, Martens, "Residual Momentum"
- Stan Weinstein stage analysis / 30-week moving average frameworks
- CANSLIM / Minervini-style practical momentum and breakout frameworks
