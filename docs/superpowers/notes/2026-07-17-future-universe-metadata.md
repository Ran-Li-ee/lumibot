# Future Idea: Add Universe Metadata for Agent Context

## Status

Backlog note only. Do not treat this as the next implementation task.

Current higher priority: design and implement agent exit / position-closing tools.

## Why This Exists

While reviewing the Agent Replay UI, we found a similar transparency issue to the earlier tool-list issue.

The UI originally showed only available tool names, but the actual LLM input also included model-facing tool descriptions. After exposing those tool definitions, we checked whether the strategy universe had a similar hidden description layer.

For the Ray Dalio idea-meritocracy demo, the answer appears to be no.

## Current Behavior

The demo strategy passes the ETF universe as a plain ticker list:

```json
["SPY", "QQQ", "IWM", "TLT", "IEF", "TIP", "GLD", "DBC", "VNQ", "UUP", "FXI", "EEM"]
```

The agent context contains `date` and `universe`, but it does not include structured descriptions such as:

- what each ticker represents
- broad asset class
- macro exposure
- issuer/fund family
- region
- duration / commodity / currency / equity-style tags

This means the LLM currently relies on a combination of:

- its own prior knowledge of common tickers
- tool calls for price/history/news/macro evidence
- general prompt instructions

It is not explicitly given a first-class universe metadata object.

## Future Improvement

Add optional universe metadata to agent context, for example:

```json
{
  "universe": ["SPY", "QQQ", "TLT", "GLD"],
  "universe_metadata": {
    "SPY": {
      "name": "SPDR S&P 500 ETF Trust",
      "category": "US large-cap equity",
      "macro_exposure": ["US equities", "risk-on", "broad market"]
    },
    "QQQ": {
      "name": "Invesco QQQ Trust",
      "category": "US growth / Nasdaq-100 equity",
      "macro_exposure": ["growth", "technology-heavy", "risk-on"]
    },
    "TLT": {
      "name": "iShares 20+ Year Treasury Bond ETF",
      "category": "long-duration US Treasury bonds",
      "macro_exposure": ["duration", "rates-down", "defensive"]
    },
    "GLD": {
      "name": "SPDR Gold Shares",
      "category": "gold",
      "macro_exposure": ["real rates", "inflation hedge", "safe haven"]
    }
  }
}
```

## UI Follow-Up

Once universe metadata exists, the Agent Replay UI should expose it clearly in `Input Material`, similar to how tool definitions are now exposed.

Possible UI behavior:

- `universe` tickers become clickable chips.
- Clicking a ticker shows its metadata.
- The UI marks whether the agent actually called tools for that ticker.
- The UI distinguishes "ticker was available to the LLM" from "ticker was investigated by tools."

## Open Design Questions

- Should universe metadata live in each strategy, a reusable registry, or a broker/data-source lookup?
- Should metadata be manually curated, fetched from an API, or cached after first lookup?
- Should metadata be minimal and stable, or include richer live facts such as holdings and expense ratio?
- How should the system handle unknown tickers without encouraging the LLM to hallucinate descriptions?

## Reminder

Do not implement this before the exit-tool work unless it becomes necessary for that feature.
