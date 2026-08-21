# QQQ Historical Constituent Universe Design

## Purpose

Build a local, reusable data layer that can answer this question:

```text
Given a backtest date, what QQQ holdings universe would have been available for that date?
```

The immediate goal is not to change the trading strategy. The goal is to replace the idea of a manually fixed 50-stock equity universe with a trustworthy historical data foundation that can later feed an equity-only LLM strategy.

The first implementation should collect and parse official QQQ N-PORT filings from the SEC, store normalized point-in-time snapshots locally, and expose deterministic lookup functions for date-based research and future backtesting.

## Background

The current `equity-only-llm` strategy uses a fixed 50-stock US equity universe. That is useful as a controlled baseline, but it has two long-term weaknesses:

1. It is manually curated and static.
2. It does not reflect how QQQ's investable universe changed over time.

Using today's QQQ or Nasdaq-100 constituents to backtest the past would create survivorship bias. A historical universe layer must therefore use point-in-time snapshots or historical changes rather than today's list.

QQQ publishes portfolio holdings through SEC N-PORT filings. These filings are free, official, and available through SEC EDGAR. They are not daily holdings files, but they are good enough for the first version of an approximate historical universe system.

## Goals

1. Discover QQQ NPORT-P filings from SEC EDGAR for QQQ CIK `0001067839`.
2. Download the relevant filing XML documents into a local cache.
3. Parse holdings from each XML document into a normalized snapshot.
4. Preserve both `report_date` and `filing_date`.
5. Compute normalized holding rows with symbol, name, identifiers, value, and approximate weight.
6. Save normalized snapshots to local JSON files.
7. Provide an as-of resolver that returns the latest usable QQQ universe for a requested date.
8. Support two as-of modes:
   - `strict`: use only snapshots whose `filing_date` is on or before the requested date.
   - `prototype`: use snapshots whose `report_date` is on or before the requested date.
9. Generate a human-readable validation report summarizing snapshot quality.
10. Add tests for parsing, normalization, date resolution, and report generation.

## Non-Goals

This feature does not:

1. Modify `AITradingTeamEquityOnlyLLMStrategy`.
2. Replace the existing fixed 50-stock universe yet.
3. Add a new LLM tool.
4. Add a broker integration.
5. Add SPY, S&P 500, or any non-QQQ universe.
6. Solve delisted ticker price availability.
7. Build a perfect daily constituent history.
8. Use paid data sources.
9. Commit large downloaded SEC data to git.
10. Optimize trading performance.

## Data Source

### Primary Source: SEC N-PORT

Use SEC EDGAR submissions for QQQ:

```text
CIK: 0001067839
Form type: NPORT-P
Example submissions endpoint:
https://data.sec.gov/submissions/CIK0001067839.json
```

Each filing index includes:

```text
accession number
filing date
report date
primary XML document path
```

The implementation should download the XML document for each selected filing and parse its portfolio investments.

### Secondary Sources

Secondary sources are for validation and future work only:

1. Invesco current QQQ holdings page.
2. Wikipedia Nasdaq-100 component changes.
3. Open-source Nasdaq-100 constituent projects.

The first implementation should not depend on secondary sources for normal operation.

## Data Semantics

### Report Date

`report_date` is the date the portfolio snapshot describes.

Example:

```text
Period of Report: 2026-03-31
```

This tells us what QQQ held at the report date, not when the market learned it.

### Filing Date

`filing_date` is when the filing became publicly available through SEC EDGAR.

Example:

```text
Filing Date: 2026-05-28
```

This is the safer date for strict backtests because it avoids using information before it was public.

### As-Of Modes

The resolver must support:

```text
strict mode:
  choose latest snapshot where filing_date <= as_of_date

prototype mode:
  choose latest snapshot where report_date <= as_of_date
```

`strict` should be the default mode because it is safer for backtesting.

`prototype` is useful for research and for quickly checking whether the historical holdings themselves were parsed correctly.

## Architecture

### Module Layout

Add a focused QQQ universe module:

```text
lumibot/tools/universe/qqq_nport.py
```

Add a small CLI wrapper:

```text
scripts/collect_qqq_nport_universe.py
```

Add tests:

```text
tests/test_qqq_nport_universe.py
```

Optional small fixture files may be added under:

```text
tests/fixtures/qqq_nport/
```

Large downloaded SEC files should not be committed.

### Local Data Layout

Use this default data directory:

```text
data/universe/qqq_nport/
  raw/
  normalized/
  reports/
  latest.json
```

Expected contents:

```text
raw/
  <accession_number>.xml

normalized/
  qqq_nport_<report_date>_<accession_number>.json

reports/
  qqq_nport_collection_<timestamp>.md
  qqq_nport_collection_<timestamp>.json

latest.json
  pointer/metadata for the latest completed local collection
```

`data/universe/qqq_nport/` is local generated data. It should normally remain untracked unless a tiny fixture is explicitly needed for tests.

## Core Components

### SEC Filing Discovery

Function responsibility:

```text
Fetch SEC submissions metadata for QQQ CIK.
Filter to NPORT-P filings.
Return filing metadata rows.
```

Each metadata row should include:

```json
{
  "cik": "0001067839",
  "accession_number": "0001067839-26-000024",
  "filing_date": "2026-05-28",
  "report_date": "2026-03-31",
  "primary_document": "primary_doc.xml",
  "sec_index_url": "...",
  "sec_xml_url": "..."
}
```

The discovery function should be deterministic and testable with injected JSON.

### XML Downloader

Function responsibility:

```text
Download SEC XML for selected filings.
Reuse local raw files when present unless refresh=True.
Respect SEC-friendly request headers.
```

SEC requests must include a clear `User-Agent`.

The CLI should allow:

```text
--limit N
--start-date YYYY-MM-DD
--end-date YYYY-MM-DD
--refresh
--data-dir PATH
```

### N-PORT Parser

Function responsibility:

```text
Parse a QQQ N-PORT XML document into normalized holdings.
```

Each holding row should preserve useful raw fields where available:

```json
{
  "symbol": "AAPL",
  "name": "Apple Inc.",
  "cusip": "...",
  "lei": "...",
  "title": "...",
  "asset_category": "...",
  "issuer_category": "...",
  "balance": 123456.0,
  "units": "NS",
  "currency": "USD",
  "value_usd": 123456789.0,
  "weight": 0.085,
  "raw": {}
}
```

The parser should be tolerant of namespace differences in SEC XML.

### Holding Normalization

Normalization rules:

1. Normalize symbols to uppercase.
2. Keep only rows with a usable public symbol for the first version.
3. Keep US equity-like holdings by default.
4. Preserve non-equity or symbol-missing rows in `excluded_holdings` for audit.
5. Compute `weight` from holding value divided by total parsed included holding value when direct percentage is unavailable.
6. Do not silently discard large excluded value. Report it in validation.

The implementation should not assume every N-PORT row maps cleanly to a tradable ticker.

### Identifier Normalization

Real QQQ N-PORT filings may omit ticker symbols even for ordinary equity rows, while still providing CUSIP and ISIN identifiers. The holdings source remains the SEC N-PORT XML; OpenFIGI may be used only as a narrow identifier-normalization service that maps SEC-provided CUSIP/ISIN values to tradable ticker symbols.

OpenFIGI mappings should be cached locally under the QQQ N-PORT data directory so repeat collection runs do not need to re-query the same identifiers. If OpenFIGI is unavailable, rate limited, or does not resolve an identifier, the holding must remain in `excluded_holdings` and the snapshot/report should include a clear warning or metadata signal. The implementation must not fabricate symbols from CUSIP/ISIN values.

### Snapshot Model

Normalized snapshot schema:

```json
{
  "schema_version": 1,
  "source": "sec_nport",
  "fund_symbol": "QQQ",
  "cik": "0001067839",
  "accession_number": "...",
  "filing_date": "YYYY-MM-DD",
  "report_date": "YYYY-MM-DD",
  "downloaded_at": "...",
  "sec_index_url": "...",
  "sec_xml_url": "...",
  "mode_notes": {
    "strict_available_from": "filing_date",
    "prototype_available_from": "report_date"
  },
  "holdings": [],
  "excluded_holdings": [],
  "summary": {
    "holding_count": 0,
    "excluded_count": 0,
    "included_value_usd": 0.0,
    "excluded_value_usd": 0.0,
    "top_holdings": []
  }
}
```

### As-Of Resolver

Function responsibility:

```text
Given a local snapshot directory, as_of_date, and mode,
return the selected snapshot and symbols.
```

Expected API shape:

```python
resolve_qqq_snapshot(
    as_of_date: date | str,
    *,
    mode: str = "strict",
    data_dir: str | Path | None = None,
) -> QQQSnapshotResolution
```

Resolution result should include:

```json
{
  "as_of_date": "YYYY-MM-DD",
  "mode": "strict",
  "selected_report_date": "YYYY-MM-DD",
  "selected_filing_date": "YYYY-MM-DD",
  "accession_number": "...",
  "symbols": ["AAPL", "MSFT", "..."],
  "snapshot_path": "...",
  "source_url": "..."
}
```

If no snapshot is available, raise a clear error explaining which mode and date failed.

### Validation Report

The CLI should write a report containing:

1. Collection timestamp.
2. Number of filings discovered.
3. Number of XML files downloaded or reused.
4. Number of snapshots normalized.
5. Per-snapshot:
   - report date
   - filing date
   - accession number
   - included holding count
   - excluded holding count
   - included value
   - excluded value
   - top 10 holdings by weight
   - warnings
6. Example as-of lookups in both strict and prototype modes.

The report should be readable by a human and useful for manual inspection.

## CLI Design

Example command:

```powershell
python scripts\collect_qqq_nport_universe.py --limit 12
```

Optional arguments:

```text
--limit N
--start-date YYYY-MM-DD
--end-date YYYY-MM-DD
--mode strict|prototype
--data-dir PATH
--refresh
--write-report
--as-of YYYY-MM-DD
```

Initial CLI behavior:

1. Discover filings.
2. Download/reuse XML files.
3. Normalize snapshots.
4. Write reports.
5. If `--as-of` is provided, print the selected snapshot and symbol count.

## Error Handling

The implementation should handle:

1. SEC network failures with clear error messages.
2. Missing XML document references.
3. XML parse failures.
4. Holdings without symbols.
5. Snapshots with unexpectedly low holding counts.
6. Duplicate symbols inside one snapshot.
7. Empty as-of resolution.

Warnings should be included in the validation report instead of being hidden.

Hard failures should stop the CLI only when the requested operation cannot be completed.

## Testing

Add tests for:

1. Filtering SEC submissions to NPORT-P filings.
2. Building SEC archive URLs from CIK, accession number, and document name.
3. Parsing a small representative N-PORT XML fixture.
4. Normalizing holding symbols and weights.
5. Preserving excluded holdings for audit.
6. Resolving strict as-of snapshots by `filing_date`.
7. Resolving prototype as-of snapshots by `report_date`.
8. Handling no available snapshot.
9. Writing a validation report from fake snapshots.
10. CLI argument parsing without live network calls.

Tests should not require network access.

Live SEC smoke testing may be manual or marked explicitly so it does not run in normal unit test flows.

## Acceptance Criteria

This feature is complete when:

1. Running the collector with a small limit downloads or reuses QQQ NPORT-P XML files.
2. Normalized snapshot JSON files are created locally.
3. Each recent snapshot returns a plausible QQQ holdings universe.
4. The as-of resolver can answer at least one strict-mode date and one prototype-mode date.
5. The validation report clearly shows snapshot dates, holding counts, warnings, and top holdings.
6. Unit tests pass without live network access.
7. No current trading strategy behavior changes.
8. No Alpaca all-market universe collector code is reintroduced.

## Future Work

After this data layer is verified, later features may:

1. Add SPY or S&P 500 historical universe support.
2. Cross-check QQQ snapshots against Nasdaq-100 component-change sources.
3. Add ticker-change normalization.
4. Add price-data availability checks for parsed symbols.
5. Expose the historical universe as an LLM tool.
6. Replace the fixed 50-stock equity-only universe with QQQ historical snapshots.
7. Compare QQQ-historical-universe selection against fixed-universe selection.

## Spec Self-Review

1. Scope is limited to QQQ historical universe data collection and lookup.
2. Strategy integration is explicitly out of scope.
3. Both report-date and filing-date semantics are defined.
4. The default as-of mode is strict to reduce look-ahead bias.
5. Large downloaded data is local generated data and should not be committed.
6. Tests are designed to avoid live network dependency.
