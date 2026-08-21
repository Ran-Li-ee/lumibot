# QQQ Historical Constituent Universe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local QQQ historical holdings data layer from SEC N-PORT filings, with normalized snapshots, as-of lookup, CLI collection, and validation reports.

**Architecture:** Add one focused universe module under `lumibot/tools/universe/qqq_nport.py`, a CLI wrapper under `scripts/collect_qqq_nport_universe.py`, and unit tests under `tests/test_qqq_nport_universe.py`. The module will keep strategy integration out of scope: it only discovers SEC filings, downloads/reuses XML, parses holdings, writes local snapshots, resolves as-of universes, and generates reports.

**Tech Stack:** Python standard library (`argparse`, `dataclasses`, `datetime`, `json`, `pathlib`, `urllib.request`, `xml.etree.ElementTree`), pytest, existing repo test conventions.

---

## File Structure

Create:

- `lumibot/tools/universe/qqq_nport.py`
  Owns QQQ SEC N-PORT discovery, URL construction, XML parsing, normalization, snapshot IO, as-of resolution, report generation, and the collection orchestration function.

- `scripts/collect_qqq_nport_universe.py`
  Thin CLI wrapper around `qqq_nport.main()`.

- `tests/test_qqq_nport_universe.py`
  Unit tests that do not require live network access.

- `tests/fixtures/qqq_nport/sample_primary_doc.xml`
  Small hand-written XML fixture with representative namespace, included holdings, excluded holdings, missing symbol, and duplicate symbol cases.

Modify:

- `lumibot/tools/universe/__init__.py`
  Export `qqq_nport` module or selected public helpers.

Do not modify:

- `lumibot/example_strategies/ai_trading_team_equity_only_llm.py`
- `lumibot/example_strategies/ai_trading_team_equity_only_helpers.py`
- Any existing strategy runner behavior.

Generated local data path:

- `data/universe/qqq_nport/`

The implementation should create this at runtime but should not commit generated SEC downloads.

---

### Task 1: Add Core Data Models And SEC Filing Discovery

**Files:**
- Create: `lumibot/tools/universe/qqq_nport.py`
- Modify: `lumibot/tools/universe/__init__.py`
- Test: `tests/test_qqq_nport_universe.py`

- [ ] **Step 1: Write failing tests for SEC metadata filtering and URL building**

Add these tests to `tests/test_qqq_nport_universe.py`:

```python
from datetime import date

import pytest

from lumibot.tools.universe import qqq_nport


def test_build_sec_archive_urls_for_qqq_nport_filing():
    row = qqq_nport.build_filing_metadata(
        cik="0001067839",
        accession_number="0001067839-26-000024",
        filing_date="2026-05-28",
        report_date="2026-03-31",
        primary_document="primary_doc.xml",
    )

    assert row.cik == "0001067839"
    assert row.accession_number == "0001067839-26-000024"
    assert row.filing_date == date(2026, 5, 28)
    assert row.report_date == date(2026, 3, 31)
    assert row.sec_index_url == (
        "https://www.sec.gov/Archives/edgar/data/1067839/"
        "000106783926000024/0001067839-26-000024-index.htm"
    )
    assert row.sec_xml_url == (
        "https://www.sec.gov/Archives/edgar/data/1067839/"
        "000106783926000024/primary_doc.xml"
    )


def test_discover_nport_filings_from_submissions_json_filters_and_sorts():
    submissions = {
        "filings": {
            "recent": {
                "accessionNumber": [
                    "0001067839-26-000024",
                    "0001067839-26-000016",
                    "0001067839-26-000001",
                ],
                "filingDate": ["2026-05-28", "2026-02-27", "2026-01-15"],
                "reportDate": ["2026-03-31", "2025-12-31", "2025-11-30"],
                "form": ["NPORT-P", "NPORT-P", "497"],
                "primaryDocument": ["primary_doc.xml", "xslFormNPORT-P_X01/primary_doc.xml", "doc.htm"],
            }
        }
    }

    rows = qqq_nport.discover_nport_filings_from_submissions(submissions, cik="0001067839")

    assert [row.accession_number for row in rows] == [
        "0001067839-26-000024",
        "0001067839-26-000016",
    ]
    assert rows[0].primary_document == "primary_doc.xml"
    assert rows[1].primary_document == "xslFormNPORT-P_X01/primary_doc.xml"


def test_discover_nport_filings_applies_date_filters_and_limit():
    submissions = {
        "filings": {
            "recent": {
                "accessionNumber": [
                    "0001067839-26-000024",
                    "0001067839-26-000016",
                    "0001067839-25-000007",
                ],
                "filingDate": ["2026-05-28", "2026-02-27", "2025-11-19"],
                "reportDate": ["2026-03-31", "2025-12-31", "2025-09-30"],
                "form": ["NPORT-P", "NPORT-P", "NPORT-P"],
                "primaryDocument": ["primary_doc.xml", "primary_doc.xml", "primary_doc.xml"],
            }
        }
    }

    rows = qqq_nport.discover_nport_filings_from_submissions(
        submissions,
        cik="0001067839",
        start_date="2026-01-01",
        end_date="2026-12-31",
        limit=1,
    )

    assert len(rows) == 1
    assert rows[0].accession_number == "0001067839-26-000024"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_qqq_nport_universe.py -q
```

Expected: FAIL because `lumibot.tools.universe.qqq_nport` and the referenced functions do not exist.

- [ ] **Step 3: Implement data models, URL helpers, and metadata discovery**

Create `lumibot/tools/universe/qqq_nport.py` with:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.request import Request, urlopen
import argparse
import json
import math
import re
import xml.etree.ElementTree as ET


QQQ_CIK = "0001067839"
QQQ_SYMBOL = "QQQ"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVES_BASE_URL = "https://www.sec.gov/Archives/edgar/data"
DEFAULT_USER_AGENT = "Ran-Li Lumibot QQQ historical universe research contact@example.com"
DEFAULT_DATA_DIR = Path("data") / "universe" / "qqq_nport"
SNAPSHOT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class FilingMetadata:
    cik: str
    accession_number: str
    filing_date: date
    report_date: date
    primary_document: str
    sec_index_url: str
    sec_xml_url: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["filing_date"] = self.filing_date.isoformat()
        data["report_date"] = self.report_date.isoformat()
        return data


@dataclass(frozen=True)
class Holding:
    symbol: str | None
    name: str | None
    cusip: str | None
    lei: str | None
    title: str | None
    asset_category: str | None
    issuer_category: str | None
    balance: float | None
    units: str | None
    currency: str | None
    value_usd: float | None
    weight: float | None
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SnapshotResolution:
    as_of_date: date
    mode: str
    selected_report_date: date
    selected_filing_date: date
    accession_number: str
    symbols: list[str]
    snapshot_path: str
    source_url: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of_date": self.as_of_date.isoformat(),
            "mode": self.mode,
            "selected_report_date": self.selected_report_date.isoformat(),
            "selected_filing_date": self.selected_filing_date.isoformat(),
            "accession_number": self.accession_number,
            "symbols": list(self.symbols),
            "snapshot_path": self.snapshot_path,
            "source_url": self.source_url,
        }


def parse_date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise TypeError(f"date value must be date or YYYY-MM-DD string, got {type(value).__name__}")
    return date.fromisoformat(value)


def accession_without_dashes(accession_number: str) -> str:
    return accession_number.replace("-", "")


def cik_without_leading_zeroes(cik: str) -> str:
    return str(int(cik))


def build_sec_archive_url(cik: str, accession_number: str, document: str) -> str:
    return (
        f"{SEC_ARCHIVES_BASE_URL}/{cik_without_leading_zeroes(cik)}/"
        f"{accession_without_dashes(accession_number)}/{document}"
    )


def build_filing_metadata(
    *,
    cik: str,
    accession_number: str,
    filing_date: date | str,
    report_date: date | str,
    primary_document: str,
) -> FilingMetadata:
    index_url = build_sec_archive_url(cik, accession_number, f"{accession_number}-index.htm")
    xml_url = build_sec_archive_url(cik, accession_number, primary_document)
    return FilingMetadata(
        cik=cik,
        accession_number=accession_number,
        filing_date=parse_date(filing_date),
        report_date=parse_date(report_date),
        primary_document=primary_document,
        sec_index_url=index_url,
        sec_xml_url=xml_url,
    )


def _recent_filings(submissions: dict[str, Any]) -> dict[str, list[Any]]:
    recent = submissions.get("filings", {}).get("recent", {})
    if not isinstance(recent, dict):
        return {}
    return recent


def discover_nport_filings_from_submissions(
    submissions: dict[str, Any],
    *,
    cik: str = QQQ_CIK,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    limit: int | None = None,
) -> list[FilingMetadata]:
    recent = _recent_filings(submissions)
    forms = recent.get("form", [])
    accession_numbers = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    primary_documents = recent.get("primaryDocument", [])
    start = parse_date(start_date) if start_date else None
    end = parse_date(end_date) if end_date else None

    rows: list[FilingMetadata] = []
    for index, form in enumerate(forms):
        if form != "NPORT-P":
            continue
        try:
            row = build_filing_metadata(
                cik=cik,
                accession_number=accession_numbers[index],
                filing_date=filing_dates[index],
                report_date=report_dates[index],
                primary_document=primary_documents[index],
            )
        except (IndexError, ValueError, TypeError):
            continue
        if start and row.filing_date < start:
            continue
        if end and row.filing_date > end:
            continue
        rows.append(row)

    rows.sort(key=lambda item: (item.filing_date, item.report_date, item.accession_number), reverse=True)
    if limit is not None:
        return rows[: int(limit)]
    return rows
```

Modify `lumibot/tools/universe/__init__.py`:

```python
from . import qqq_nport

__all__ = ["qqq_nport"]
```

- [ ] **Step 4: Run tests**

Run:

```powershell
python -m pytest tests/test_qqq_nport_universe.py -q
```

Expected: PASS for the three metadata tests.

- [ ] **Step 5: Commit**

```powershell
git add lumibot/tools/universe/__init__.py lumibot/tools/universe/qqq_nport.py tests/test_qqq_nport_universe.py
git commit -m "feat: add qqq nport filing discovery"
```

---

### Task 2: Add N-PORT XML Fixture, Parser, And Holding Normalization

**Files:**
- Modify: `lumibot/tools/universe/qqq_nport.py`
- Modify: `tests/test_qqq_nport_universe.py`
- Create: `tests/fixtures/qqq_nport/sample_primary_doc.xml`

- [ ] **Step 1: Add XML fixture**

Create `tests/fixtures/qqq_nport/sample_primary_doc.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport">
  <formData>
    <genInfo>
      <regName>Invesco QQQ Trust, Series 1</regName>
      <seriesName>Invesco QQQ Trust, Series 1</seriesName>
      <repPdDate>2026-03-31</repPdDate>
    </genInfo>
    <invstOrSecs>
      <invstOrSec>
        <name>Apple Inc.</name>
        <lei>HWUPKR0MPOU8FGXBT394</lei>
        <title>Apple Inc.</title>
        <cusip>037833100</cusip>
        <identifiers>
          <ticker value="AAPL"/>
        </identifiers>
        <balance>1000</balance>
        <units>NS</units>
        <curCd>USD</curCd>
        <valUSD>150000</valUSD>
        <assetCat>EC</assetCat>
        <issuerCat>CORP</issuerCat>
      </invstOrSec>
      <invstOrSec>
        <name>Microsoft Corporation</name>
        <title>Microsoft Corporation</title>
        <cusip>594918104</cusip>
        <identifiers>
          <ticker value="MSFT"/>
        </identifiers>
        <balance>500</balance>
        <units>NS</units>
        <curCd>USD</curCd>
        <valUSD>100000</valUSD>
        <assetCat>EC</assetCat>
        <issuerCat>CORP</issuerCat>
      </invstOrSec>
      <invstOrSec>
        <name>Cash Collateral</name>
        <title>Cash Collateral</title>
        <balance>1</balance>
        <units>USD</units>
        <curCd>USD</curCd>
        <valUSD>1000</valUSD>
        <assetCat>STIV</assetCat>
        <issuerCat>RF</issuerCat>
      </invstOrSec>
    </invstOrSecs>
  </formData>
</edgarSubmission>
```

- [ ] **Step 2: Write failing parser tests**

Append to `tests/test_qqq_nport_universe.py`:

```python
from pathlib import Path


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "qqq_nport"


def test_parse_nport_xml_normalizes_holdings_and_exclusions():
    xml_text = (FIXTURE_DIR / "sample_primary_doc.xml").read_text(encoding="utf-8")
    metadata = qqq_nport.build_filing_metadata(
        cik="0001067839",
        accession_number="0001067839-26-000024",
        filing_date="2026-05-28",
        report_date="2026-03-31",
        primary_document="primary_doc.xml",
    )

    snapshot = qqq_nport.parse_nport_xml(xml_text, metadata=metadata, downloaded_at="2026-08-21T00:00:00Z")

    assert snapshot["schema_version"] == 1
    assert snapshot["fund_symbol"] == "QQQ"
    assert snapshot["report_date"] == "2026-03-31"
    assert snapshot["filing_date"] == "2026-05-28"
    assert [holding["symbol"] for holding in snapshot["holdings"]] == ["AAPL", "MSFT"]
    assert snapshot["summary"]["holding_count"] == 2
    assert snapshot["summary"]["excluded_count"] == 1
    assert snapshot["summary"]["included_value_usd"] == 250000.0
    assert snapshot["summary"]["excluded_value_usd"] == 1000.0
    assert snapshot["holdings"][0]["weight"] == pytest.approx(0.6)
    assert snapshot["holdings"][1]["weight"] == pytest.approx(0.4)
    assert snapshot["excluded_holdings"][0]["name"] == "Cash Collateral"


def test_parse_nport_xml_warns_on_duplicate_symbols():
    xml_text = (FIXTURE_DIR / "sample_primary_doc.xml").read_text(encoding="utf-8")
    xml_text = xml_text.replace("<ticker value=\"MSFT\"/>", "<ticker value=\"AAPL\"/>")
    metadata = qqq_nport.build_filing_metadata(
        cik="0001067839",
        accession_number="0001067839-26-000024",
        filing_date="2026-05-28",
        report_date="2026-03-31",
        primary_document="primary_doc.xml",
    )

    snapshot = qqq_nport.parse_nport_xml(xml_text, metadata=metadata, downloaded_at="2026-08-21T00:00:00Z")

    assert any("duplicate symbol" in warning.lower() for warning in snapshot["summary"]["warnings"])
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_qqq_nport_universe.py::test_parse_nport_xml_normalizes_holdings_and_exclusions tests/test_qqq_nport_universe.py::test_parse_nport_xml_warns_on_duplicate_symbols -q
```

Expected: FAIL because `parse_nport_xml` does not exist.

- [ ] **Step 4: Implement XML parsing and normalization**

Append to `qqq_nport.py`:

```python
def _strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _children_by_local_name(element: ET.Element, local_name: str) -> list[ET.Element]:
    return [child for child in list(element) if _strip_namespace(child.tag) == local_name]


def _first_child_text(element: ET.Element, *names: str) -> str | None:
    for name in names:
        for child in _children_by_local_name(element, name):
            if child.text is not None and child.text.strip():
                return child.text.strip()
    return None


def _first_descendant_text(element: ET.Element, *names: str) -> str | None:
    wanted = set(names)
    for descendant in element.iter():
        if _strip_namespace(descendant.tag) in wanted and descendant.text and descendant.text.strip():
            return descendant.text.strip()
    return None


def _extract_ticker(element: ET.Element) -> str | None:
    for descendant in element.iter():
        if _strip_namespace(descendant.tag).lower() == "ticker":
            value = descendant.attrib.get("value") or descendant.text
            if value and value.strip():
                return normalize_symbol(value)
    for name in ("ticker", "tickerSymbol", "tickerSymb"):
        value = _first_descendant_text(element, name)
        if value:
            return normalize_symbol(value)
    return None


def normalize_symbol(value: str | None) -> str | None:
    if not value:
        return None
    symbol = value.strip().upper()
    symbol = symbol.replace(".", "-")
    if not re.fullmatch(r"[A-Z][A-Z0-9-]{0,9}", symbol):
        return None
    return symbol


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value.replace(",", "").strip())
    except Exception:
        return None
    if not math.isfinite(number):
        return None
    return number


def _is_included_equity_holding(holding: Holding) -> bool:
    if not holding.symbol:
        return False
    if holding.value_usd is None:
        return False
    asset_category = (holding.asset_category or "").upper()
    if asset_category and asset_category not in {"EC", "EQUITY", "COMMON"}:
        return False
    return True


def _parse_holding(element: ET.Element) -> Holding:
    raw = {
        "name": _first_child_text(element, "name"),
        "title": _first_child_text(element, "title"),
        "cusip": _first_child_text(element, "cusip"),
        "asset_category": _first_child_text(element, "assetCat", "assetCategory"),
        "issuer_category": _first_child_text(element, "issuerCat", "issuerCategory"),
    }
    return Holding(
        symbol=_extract_ticker(element),
        name=raw["name"],
        cusip=raw["cusip"],
        lei=_first_child_text(element, "lei"),
        title=raw["title"],
        asset_category=raw["asset_category"],
        issuer_category=raw["issuer_category"],
        balance=_to_float(_first_child_text(element, "balance")),
        units=_first_child_text(element, "units"),
        currency=_first_child_text(element, "curCd", "currency"),
        value_usd=_to_float(_first_child_text(element, "valUSD", "valueUSD")),
        weight=None,
        raw=raw,
    )


def _find_investment_elements(root: ET.Element) -> list[ET.Element]:
    elements = []
    for element in root.iter():
        if _strip_namespace(element.tag) in {"invstOrSec", "invstOrSecs"}:
            if _strip_namespace(element.tag) == "invstOrSec":
                elements.append(element)
    return elements


def parse_nport_xml(
    xml_text: str,
    *,
    metadata: FilingMetadata,
    downloaded_at: str | None = None,
) -> dict[str, Any]:
    root = ET.fromstring(xml_text)
    downloaded_at = downloaded_at or datetime.now(timezone.utc).isoformat()
    parsed = [_parse_holding(element) for element in _find_investment_elements(root)]
    included: list[Holding] = []
    excluded: list[Holding] = []
    for holding in parsed:
        if _is_included_equity_holding(holding):
            included.append(holding)
        else:
            excluded.append(holding)

    included_value = sum(holding.value_usd or 0.0 for holding in included)
    excluded_value = sum(holding.value_usd or 0.0 for holding in excluded)
    normalized_included = []
    for holding in included:
        weight = (holding.value_usd or 0.0) / included_value if included_value else None
        normalized_included.append(
            Holding(
                symbol=holding.symbol,
                name=holding.name,
                cusip=holding.cusip,
                lei=holding.lei,
                title=holding.title,
                asset_category=holding.asset_category,
                issuer_category=holding.issuer_category,
                balance=holding.balance,
                units=holding.units,
                currency=holding.currency,
                value_usd=holding.value_usd,
                weight=weight,
                raw=holding.raw,
            )
        )

    symbols = [holding.symbol for holding in normalized_included if holding.symbol]
    warnings: list[str] = []
    duplicate_symbols = sorted({symbol for symbol in symbols if symbols.count(symbol) > 1})
    if duplicate_symbols:
        warnings.append(f"Duplicate symbol(s) detected: {', '.join(duplicate_symbols)}")
    if len(normalized_included) < 80:
        warnings.append(f"Unexpectedly low included holding count: {len(normalized_included)}")
    if excluded_value > included_value * 0.05 and included_value:
        warnings.append("Excluded holding value is more than 5% of included value.")

    top_holdings = sorted(
        [holding.to_dict() for holding in normalized_included],
        key=lambda row: row.get("weight") or 0.0,
        reverse=True,
    )[:10]

    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "source": "sec_nport",
        "fund_symbol": QQQ_SYMBOL,
        "cik": metadata.cik,
        "accession_number": metadata.accession_number,
        "filing_date": metadata.filing_date.isoformat(),
        "report_date": metadata.report_date.isoformat(),
        "downloaded_at": downloaded_at,
        "sec_index_url": metadata.sec_index_url,
        "sec_xml_url": metadata.sec_xml_url,
        "mode_notes": {
            "strict_available_from": "filing_date",
            "prototype_available_from": "report_date",
        },
        "holdings": [holding.to_dict() for holding in normalized_included],
        "excluded_holdings": [holding.to_dict() for holding in excluded],
        "summary": {
            "holding_count": len(normalized_included),
            "excluded_count": len(excluded),
            "included_value_usd": included_value,
            "excluded_value_usd": excluded_value,
            "top_holdings": top_holdings,
            "warnings": warnings,
        },
    }
```

- [ ] **Step 5: Run parser tests**

Run:

```powershell
python -m pytest tests/test_qqq_nport_universe.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add lumibot/tools/universe/qqq_nport.py tests/test_qqq_nport_universe.py tests/fixtures/qqq_nport/sample_primary_doc.xml
git commit -m "feat: parse qqq nport holdings snapshots"
```

---

### Task 3: Add Snapshot Storage And As-Of Resolver

**Files:**
- Modify: `lumibot/tools/universe/qqq_nport.py`
- Modify: `tests/test_qqq_nport_universe.py`

- [ ] **Step 1: Write failing tests for snapshot IO and as-of resolution**

Append:

```python
def _snapshot(accession, report_date, filing_date, symbols):
    return {
        "schema_version": 1,
        "source": "sec_nport",
        "fund_symbol": "QQQ",
        "cik": "0001067839",
        "accession_number": accession,
        "filing_date": filing_date,
        "report_date": report_date,
        "downloaded_at": "2026-08-21T00:00:00Z",
        "sec_index_url": f"https://example.test/{accession}/index",
        "sec_xml_url": f"https://example.test/{accession}/xml",
        "mode_notes": {"strict_available_from": "filing_date", "prototype_available_from": "report_date"},
        "holdings": [{"symbol": symbol, "weight": 1 / len(symbols)} for symbol in symbols],
        "excluded_holdings": [],
        "summary": {
            "holding_count": len(symbols),
            "excluded_count": 0,
            "included_value_usd": 100.0,
            "excluded_value_usd": 0.0,
            "top_holdings": [],
            "warnings": [],
        },
    }


def test_write_snapshot_and_resolve_strict_as_of(tmp_path):
    older = _snapshot("old", "2025-12-31", "2026-02-27", ["AAPL"])
    newer = _snapshot("new", "2026-03-31", "2026-05-28", ["MSFT"])
    qqq_nport.write_normalized_snapshot(older, data_dir=tmp_path)
    qqq_nport.write_normalized_snapshot(newer, data_dir=tmp_path)

    resolution = qqq_nport.resolve_qqq_snapshot("2026-04-15", data_dir=tmp_path, mode="strict")

    assert resolution.accession_number == "old"
    assert resolution.symbols == ["AAPL"]
    assert resolution.selected_report_date == date(2025, 12, 31)
    assert resolution.selected_filing_date == date(2026, 2, 27)


def test_resolve_prototype_as_of_uses_report_date(tmp_path):
    older = _snapshot("old", "2025-12-31", "2026-02-27", ["AAPL"])
    newer = _snapshot("new", "2026-03-31", "2026-05-28", ["MSFT"])
    qqq_nport.write_normalized_snapshot(older, data_dir=tmp_path)
    qqq_nport.write_normalized_snapshot(newer, data_dir=tmp_path)

    resolution = qqq_nport.resolve_qqq_snapshot("2026-04-15", data_dir=tmp_path, mode="prototype")

    assert resolution.accession_number == "new"
    assert resolution.symbols == ["MSFT"]


def test_resolve_as_of_raises_clear_error_when_no_snapshot_available(tmp_path):
    qqq_nport.write_normalized_snapshot(_snapshot("new", "2026-03-31", "2026-05-28", ["MSFT"]), data_dir=tmp_path)

    with pytest.raises(qqq_nport.NoSnapshotAvailableError, match="No QQQ snapshot available"):
        qqq_nport.resolve_qqq_snapshot("2026-01-01", data_dir=tmp_path, mode="strict")
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
python -m pytest tests/test_qqq_nport_universe.py::test_write_snapshot_and_resolve_strict_as_of tests/test_qqq_nport_universe.py::test_resolve_prototype_as_of_uses_report_date tests/test_qqq_nport_universe.py::test_resolve_as_of_raises_clear_error_when_no_snapshot_available -q
```

Expected: FAIL because storage and resolver functions do not exist.

- [ ] **Step 3: Implement snapshot paths, writing, loading, and resolution**

Append:

```python
class NoSnapshotAvailableError(RuntimeError):
    pass


def normalized_dir(data_dir: Path | str | None = None) -> Path:
    return Path(data_dir or DEFAULT_DATA_DIR) / "normalized"


def reports_dir(data_dir: Path | str | None = None) -> Path:
    return Path(data_dir or DEFAULT_DATA_DIR) / "reports"


def raw_dir(data_dir: Path | str | None = None) -> Path:
    return Path(data_dir or DEFAULT_DATA_DIR) / "raw"


def snapshot_filename(snapshot: dict[str, Any]) -> str:
    report_date = snapshot["report_date"]
    accession = snapshot["accession_number"]
    return f"qqq_nport_{report_date}_{accession}.json"


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_normalized_snapshot(snapshot: dict[str, Any], *, data_dir: Path | str | None = None) -> Path:
    output_path = normalized_dir(data_dir) / snapshot_filename(snapshot)
    write_json(output_path, snapshot)
    return output_path


def load_snapshot(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def iter_snapshot_paths(data_dir: Path | str | None = None) -> list[Path]:
    directory = normalized_dir(data_dir)
    if not directory.exists():
        return []
    return sorted(directory.glob("qqq_nport_*.json"))


def _resolution_date(snapshot: dict[str, Any], mode: str) -> date:
    if mode == "strict":
        return parse_date(snapshot["filing_date"])
    if mode == "prototype":
        return parse_date(snapshot["report_date"])
    raise ValueError("mode must be 'strict' or 'prototype'")


def resolve_qqq_snapshot(
    as_of_date: date | str,
    *,
    mode: str = "strict",
    data_dir: str | Path | None = None,
) -> SnapshotResolution:
    as_of = parse_date(as_of_date)
    candidates: list[tuple[date, Path, dict[str, Any]]] = []
    for path in iter_snapshot_paths(data_dir):
        snapshot = load_snapshot(path)
        available_date = _resolution_date(snapshot, mode)
        if available_date <= as_of:
            candidates.append((available_date, path, snapshot))

    if not candidates:
        raise NoSnapshotAvailableError(f"No QQQ snapshot available for {as_of.isoformat()} in {mode} mode.")

    candidates.sort(key=lambda item: (item[0], parse_date(item[2]["report_date"])), reverse=True)
    _available_date, path, snapshot = candidates[0]
    symbols = [holding["symbol"] for holding in snapshot.get("holdings", []) if holding.get("symbol")]
    return SnapshotResolution(
        as_of_date=as_of,
        mode=mode,
        selected_report_date=parse_date(snapshot["report_date"]),
        selected_filing_date=parse_date(snapshot["filing_date"]),
        accession_number=snapshot["accession_number"],
        symbols=symbols,
        snapshot_path=str(path),
        source_url=snapshot["sec_xml_url"],
    )
```

- [ ] **Step 4: Run tests**

Run:

```powershell
python -m pytest tests/test_qqq_nport_universe.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add lumibot/tools/universe/qqq_nport.py tests/test_qqq_nport_universe.py
git commit -m "feat: resolve qqq snapshots by date"
```

---

### Task 4: Add SEC Downloading And Collection Orchestration

**Files:**
- Modify: `lumibot/tools/universe/qqq_nport.py`
- Modify: `tests/test_qqq_nport_universe.py`

- [ ] **Step 1: Write failing tests for network-injected collection**

Append:

```python
class FakeSecClient:
    def __init__(self, submissions, xml_by_url):
        self.submissions = submissions
        self.xml_by_url = xml_by_url
        self.requested_urls = []

    def get_json(self, url):
        self.requested_urls.append(url)
        return self.submissions

    def get_text(self, url):
        self.requested_urls.append(url)
        return self.xml_by_url[url]


def test_collect_qqq_nport_snapshots_uses_injected_client_and_writes_files(tmp_path):
    xml_text = (FIXTURE_DIR / "sample_primary_doc.xml").read_text(encoding="utf-8")
    submissions = {
        "filings": {
            "recent": {
                "accessionNumber": ["0001067839-26-000024"],
                "filingDate": ["2026-05-28"],
                "reportDate": ["2026-03-31"],
                "form": ["NPORT-P"],
                "primaryDocument": ["primary_doc.xml"],
            }
        }
    }
    metadata = qqq_nport.build_filing_metadata(
        cik="0001067839",
        accession_number="0001067839-26-000024",
        filing_date="2026-05-28",
        report_date="2026-03-31",
        primary_document="primary_doc.xml",
    )
    client = FakeSecClient(submissions, {metadata.sec_xml_url: xml_text})

    result = qqq_nport.collect_qqq_nport_snapshots(limit=1, data_dir=tmp_path, sec_client=client)

    assert result["summary"]["filings_discovered"] == 1
    assert result["summary"]["snapshots_normalized"] == 1
    assert len(list((tmp_path / "raw").glob("*.xml"))) == 1
    assert len(list((tmp_path / "normalized").glob("*.json"))) == 1
    assert (tmp_path / "latest.json").exists()


def test_collect_qqq_nport_snapshots_reuses_raw_xml_without_refresh(tmp_path):
    xml_text = (FIXTURE_DIR / "sample_primary_doc.xml").read_text(encoding="utf-8")
    submissions = {
        "filings": {
            "recent": {
                "accessionNumber": ["0001067839-26-000024"],
                "filingDate": ["2026-05-28"],
                "reportDate": ["2026-03-31"],
                "form": ["NPORT-P"],
                "primaryDocument": ["primary_doc.xml"],
            }
        }
    }
    metadata = qqq_nport.build_filing_metadata(
        cik="0001067839",
        accession_number="0001067839-26-000024",
        filing_date="2026-05-28",
        report_date="2026-03-31",
        primary_document="primary_doc.xml",
    )
    raw_path = tmp_path / "raw" / "0001067839-26-000024.xml"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_text(xml_text, encoding="utf-8")
    client = FakeSecClient(submissions, {metadata.sec_xml_url: "<should-not-download/>"})

    result = qqq_nport.collect_qqq_nport_snapshots(limit=1, data_dir=tmp_path, sec_client=client)

    assert result["filings"][0]["download_status"] == "reused"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
python -m pytest tests/test_qqq_nport_universe.py::test_collect_qqq_nport_snapshots_uses_injected_client_and_writes_files tests/test_qqq_nport_universe.py::test_collect_qqq_nport_snapshots_reuses_raw_xml_without_refresh -q
```

Expected: FAIL because `collect_qqq_nport_snapshots` does not exist.

- [ ] **Step 3: Implement SEC client, downloader, and collection orchestration**

Append:

```python
class SecClient:
    def __init__(self, *, user_agent: str = DEFAULT_USER_AGENT, timeout: int = 30):
        self.user_agent = user_agent
        self.timeout = timeout

    def _request(self, url: str) -> Request:
        return Request(url, headers={"User-Agent": self.user_agent, "Accept-Encoding": "identity"})

    def get_json(self, url: str) -> dict[str, Any]:
        with urlopen(self._request(url), timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def get_text(self, url: str) -> str:
        with urlopen(self._request(url), timeout=self.timeout) as response:
            return response.read().decode("utf-8")


def fetch_submissions_json(*, cik: str = QQQ_CIK, sec_client: Any | None = None) -> dict[str, Any]:
    client = sec_client or SecClient()
    return client.get_json(SEC_SUBMISSIONS_URL.format(cik=cik))


def raw_xml_path(metadata: FilingMetadata, *, data_dir: Path | str | None = None) -> Path:
    return raw_dir(data_dir) / f"{metadata.accession_number}.xml"


def download_or_read_xml(
    metadata: FilingMetadata,
    *,
    data_dir: Path | str | None = None,
    sec_client: Any | None = None,
    refresh: bool = False,
) -> tuple[str, str, Path]:
    path = raw_xml_path(metadata, data_dir=data_dir)
    if path.exists() and not refresh:
        return path.read_text(encoding="utf-8"), "reused", path

    client = sec_client or SecClient()
    text = client.get_text(metadata.sec_xml_url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text, "downloaded", path


def collect_qqq_nport_snapshots(
    *,
    limit: int | None = None,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    data_dir: Path | str | None = None,
    sec_client: Any | None = None,
    refresh: bool = False,
    downloaded_at: str | None = None,
) -> dict[str, Any]:
    client = sec_client or SecClient()
    submissions = fetch_submissions_json(cik=QQQ_CIK, sec_client=client)
    filings = discover_nport_filings_from_submissions(
        submissions,
        cik=QQQ_CIK,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )
    result_rows = []
    snapshot_paths = []
    warnings = []
    for metadata in filings:
        try:
            xml_text, status, raw_path = download_or_read_xml(
                metadata,
                data_dir=data_dir,
                sec_client=client,
                refresh=refresh,
            )
            snapshot = parse_nport_xml(xml_text, metadata=metadata, downloaded_at=downloaded_at)
            snapshot_path = write_normalized_snapshot(snapshot, data_dir=data_dir)
            snapshot_paths.append(str(snapshot_path))
            result_rows.append(
                {
                    **metadata.to_dict(),
                    "download_status": status,
                    "raw_path": str(raw_path),
                    "snapshot_path": str(snapshot_path),
                    "holding_count": snapshot["summary"]["holding_count"],
                    "excluded_count": snapshot["summary"]["excluded_count"],
                    "warnings": snapshot["summary"].get("warnings", []),
                }
            )
        except Exception as exc:
            message = f"{metadata.accession_number}: {type(exc).__name__}: {exc}"
            warnings.append(message)
            result_rows.append({**metadata.to_dict(), "error": message})

    summary = {
        "filings_discovered": len(filings),
        "snapshots_normalized": len(snapshot_paths),
        "warnings": warnings,
        "collected_at": downloaded_at or datetime.now(timezone.utc).isoformat(),
    }
    result = {"summary": summary, "filings": result_rows, "snapshot_paths": snapshot_paths}
    latest_path = Path(data_dir or DEFAULT_DATA_DIR) / "latest.json"
    write_json(latest_path, result)
    return result
```

- [ ] **Step 4: Run tests**

Run:

```powershell
python -m pytest tests/test_qqq_nport_universe.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add lumibot/tools/universe/qqq_nport.py tests/test_qqq_nport_universe.py
git commit -m "feat: collect qqq nport snapshots"
```

---

### Task 5: Add Human-Readable Validation Reports

**Files:**
- Modify: `lumibot/tools/universe/qqq_nport.py`
- Modify: `tests/test_qqq_nport_universe.py`

- [ ] **Step 1: Write failing report tests**

Append:

```python
def test_build_validation_report_includes_snapshot_quality(tmp_path):
    qqq_nport.write_normalized_snapshot(
        _snapshot("new", "2026-03-31", "2026-05-28", ["AAPL", "MSFT"]),
        data_dir=tmp_path,
    )
    result = {
        "summary": {"filings_discovered": 1, "snapshots_normalized": 1, "warnings": [], "collected_at": "now"},
        "filings": [
            {
                "accession_number": "new",
                "report_date": "2026-03-31",
                "filing_date": "2026-05-28",
                "snapshot_path": str(next((tmp_path / "normalized").glob("*.json"))),
                "holding_count": 2,
                "excluded_count": 0,
                "warnings": [],
            }
        ],
    }

    report = qqq_nport.build_validation_report(result, data_dir=tmp_path, example_as_of_dates=["2026-06-01"])

    assert "# QQQ N-PORT Universe Collection Report" in report
    assert "2026-03-31" in report
    assert "new" in report
    assert "AAPL" in report
    assert "strict" in report
    assert "prototype" in report


def test_write_validation_report_writes_markdown_and_json(tmp_path):
    result = {"summary": {"filings_discovered": 0, "snapshots_normalized": 0, "warnings": [], "collected_at": "now"}, "filings": []}

    paths = qqq_nport.write_validation_report(result, data_dir=tmp_path, timestamp="20260821_120000")

    assert paths["markdown"].name == "qqq_nport_collection_20260821_120000.md"
    assert paths["json"].name == "qqq_nport_collection_20260821_120000.json"
    assert paths["markdown"].exists()
    assert paths["json"].exists()
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
python -m pytest tests/test_qqq_nport_universe.py::test_build_validation_report_includes_snapshot_quality tests/test_qqq_nport_universe.py::test_write_validation_report_writes_markdown_and_json -q
```

Expected: FAIL because report functions do not exist.

- [ ] **Step 3: Implement report generation**

Append:

```python
def _format_money(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except Exception:
        return "n/a"


def build_validation_report(
    collection_result: dict[str, Any],
    *,
    data_dir: Path | str | None = None,
    example_as_of_dates: Iterable[date | str] | None = None,
) -> str:
    lines = [
        "# QQQ N-PORT Universe Collection Report",
        "",
        "## Summary",
        "",
    ]
    summary = collection_result.get("summary", {})
    lines.extend(
        [
            f"- Collected at: {summary.get('collected_at', 'n/a')}",
            f"- Filings discovered: {summary.get('filings_discovered', 0)}",
            f"- Snapshots normalized: {summary.get('snapshots_normalized', 0)}",
            f"- Warnings: {len(summary.get('warnings', []))}",
            "",
            "## Snapshots",
            "",
        ]
    )
    for row in collection_result.get("filings", []):
        lines.extend(
            [
                f"### {row.get('report_date', 'n/a')} / {row.get('accession_number', 'n/a')}",
                "",
                f"- Filing date: {row.get('filing_date', 'n/a')}",
                f"- Holding count: {row.get('holding_count', 'n/a')}",
                f"- Excluded count: {row.get('excluded_count', 'n/a')}",
            ]
        )
        snapshot_path = row.get("snapshot_path")
        if snapshot_path and Path(snapshot_path).exists():
            snapshot = load_snapshot(Path(snapshot_path))
            lines.append(f"- Included value USD: {_format_money(snapshot.get('summary', {}).get('included_value_usd'))}")
            lines.append(f"- Excluded value USD: {_format_money(snapshot.get('summary', {}).get('excluded_value_usd'))}")
            lines.extend(["", "| Symbol | Weight | Value USD |", "|---|---:|---:|"])
            top = snapshot.get("summary", {}).get("top_holdings", []) or snapshot.get("holdings", [])[:10]
            for holding in top[:10]:
                weight = holding.get("weight")
                weight_text = f"{float(weight) * 100:.2f}%" if weight is not None else "n/a"
                lines.append(f"| {holding.get('symbol', '')} | {weight_text} | {_format_money(holding.get('value_usd'))} |")
        warnings = row.get("warnings") or []
        if warnings:
            lines.extend(["", "Warnings:"])
            for warning in warnings:
                lines.append(f"- {warning}")
        lines.append("")

    dates = list(example_as_of_dates or [])
    if dates:
        lines.extend(["## Example As-Of Lookups", ""])
        for as_of in dates:
            for mode in ("strict", "prototype"):
                try:
                    resolution = resolve_qqq_snapshot(as_of, mode=mode, data_dir=data_dir)
                    lines.append(
                        f"- {parse_date(as_of).isoformat()} `{mode}` -> "
                        f"{resolution.accession_number}, {len(resolution.symbols)} symbols"
                    )
                except Exception as exc:
                    lines.append(f"- {parse_date(as_of).isoformat()} `{mode}` -> unavailable: {exc}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_validation_report(
    collection_result: dict[str, Any],
    *,
    data_dir: Path | str | None = None,
    timestamp: str | None = None,
    example_as_of_dates: Iterable[date | str] | None = None,
) -> dict[str, Path]:
    timestamp = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    directory = reports_dir(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    markdown_path = directory / f"qqq_nport_collection_{timestamp}.md"
    json_path = directory / f"qqq_nport_collection_{timestamp}.json"
    markdown_path.write_text(
        build_validation_report(collection_result, data_dir=data_dir, example_as_of_dates=example_as_of_dates),
        encoding="utf-8",
    )
    write_json(json_path, collection_result)
    return {"markdown": markdown_path, "json": json_path}
```

- [ ] **Step 4: Run tests**

Run:

```powershell
python -m pytest tests/test_qqq_nport_universe.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add lumibot/tools/universe/qqq_nport.py tests/test_qqq_nport_universe.py
git commit -m "feat: report qqq nport snapshot quality"
```

---

### Task 6: Add CLI Wrapper

**Files:**
- Modify: `lumibot/tools/universe/qqq_nport.py`
- Create: `scripts/collect_qqq_nport_universe.py`
- Modify: `tests/test_qqq_nport_universe.py`

- [ ] **Step 1: Write failing CLI tests**

Append:

```python
def test_parse_cli_args_defaults():
    args = qqq_nport.parse_args([])

    assert args.limit is None
    assert args.mode == "strict"
    assert args.data_dir == str(qqq_nport.DEFAULT_DATA_DIR)
    assert args.write_report is False


def test_parse_cli_args_accepts_collection_options():
    args = qqq_nport.parse_args(
        [
            "--limit",
            "12",
            "--start-date",
            "2025-01-01",
            "--end-date",
            "2026-01-01",
            "--mode",
            "prototype",
            "--data-dir",
            "tmp/qqq",
            "--refresh",
            "--write-report",
            "--as-of",
            "2025-04-15",
        ]
    )

    assert args.limit == 12
    assert args.start_date == "2025-01-01"
    assert args.end_date == "2026-01-01"
    assert args.mode == "prototype"
    assert args.data_dir == "tmp/qqq"
    assert args.refresh is True
    assert args.write_report is True
    assert args.as_of == "2025-04-15"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
python -m pytest tests/test_qqq_nport_universe.py::test_parse_cli_args_defaults tests/test_qqq_nport_universe.py::test_parse_cli_args_accepts_collection_options -q
```

Expected: FAIL because `parse_args` does not exist.

- [ ] **Step 3: Implement CLI parser and main**

Append to `qqq_nport.py`:

```python
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect QQQ historical holdings snapshots from SEC N-PORT filings.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--mode", choices=["strict", "prototype"], default="strict")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    client = SecClient(user_agent=args.user_agent)
    result = collect_qqq_nport_snapshots(
        limit=args.limit,
        start_date=args.start_date,
        end_date=args.end_date,
        data_dir=args.data_dir,
        sec_client=client,
        refresh=args.refresh,
    )
    print(
        f"Discovered {result['summary']['filings_discovered']} filings; "
        f"normalized {result['summary']['snapshots_normalized']} snapshots."
    )
    if args.write_report:
        example_dates = [args.as_of] if args.as_of else []
        paths = write_validation_report(result, data_dir=args.data_dir, example_as_of_dates=example_dates)
        print(f"Wrote report: {paths['markdown']}")
    if args.as_of:
        resolution = resolve_qqq_snapshot(args.as_of, mode=args.mode, data_dir=args.data_dir)
        print(
            f"{args.as_of} {args.mode}: {resolution.accession_number} "
            f"{resolution.selected_report_date.isoformat()} "
            f"{len(resolution.symbols)} symbols"
        )
    return 0
```

Create `scripts/collect_qqq_nport_universe.py`:

```python
from lumibot.tools.universe.qqq_nport import main


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests**

Run:

```powershell
python -m pytest tests/test_qqq_nport_universe.py -q
```

Expected: PASS.

- [ ] **Step 5: Run CLI help smoke**

Run:

```powershell
python scripts\collect_qqq_nport_universe.py --help
```

Expected: exits `0` and prints arguments including `--limit`, `--mode`, and `--as-of`.

- [ ] **Step 6: Commit**

```powershell
git add lumibot/tools/universe/qqq_nport.py scripts/collect_qqq_nport_universe.py tests/test_qqq_nport_universe.py
git commit -m "feat: add qqq nport collector cli"
```

---

### Task 7: Add Manual Live SEC Smoke Validation

**Files:**
- Modify: `tests/test_qqq_nport_universe.py`
- No production file changes expected unless the live smoke exposes a parser bug.

- [ ] **Step 1: Add skipped-by-default live test marker**

Append:

```python
@pytest.mark.apitest
def test_live_sec_smoke_collects_one_recent_qqq_snapshot(tmp_path):
    result = qqq_nport.collect_qqq_nport_snapshots(limit=1, data_dir=tmp_path)

    assert result["summary"]["filings_discovered"] == 1
    assert result["summary"]["snapshots_normalized"] == 1
    snapshot_paths = list((tmp_path / "normalized").glob("*.json"))
    assert len(snapshot_paths) == 1
    snapshot = qqq_nport.load_snapshot(snapshot_paths[0])
    assert snapshot["fund_symbol"] == "QQQ"
    assert snapshot["summary"]["holding_count"] >= 80
```

- [ ] **Step 2: Run normal tests to ensure live test is not selected by default only if local command excludes apitest**

Run:

```powershell
python -m pytest tests/test_qqq_nport_universe.py -m "not apitest" -q
```

Expected: PASS.

- [ ] **Step 3: Run live smoke manually**

Run:

```powershell
python -m pytest tests/test_qqq_nport_universe.py::test_live_sec_smoke_collects_one_recent_qqq_snapshot -q
```

Expected: PASS if SEC is reachable. If it fails due to network or SEC availability, record the failure in the final notes but do not weaken unit tests.

- [ ] **Step 4: Run real CLI small collection**

Run:

```powershell
python scripts\collect_qqq_nport_universe.py --limit 3 --write-report --as-of 2026-04-15
```

Expected:

```text
Discovered 3 filings; normalized 3 snapshots.
Wrote report: data\universe\qqq_nport\reports\qqq_nport_collection_<timestamp>.md
2026-04-15 strict: <accession> <report_date> <symbol_count> symbols
```

If strict mode has no snapshot for the selected date because downloaded filings are too recent, rerun with an as-of date after the newest filing date:

```powershell
python scripts\collect_qqq_nport_universe.py --limit 3 --write-report --as-of 2026-06-01
```

- [ ] **Step 5: Inspect generated report**

Open the newest report under:

```text
data/universe/qqq_nport/reports/
```

Check:

1. each snapshot has plausible holding counts;
2. top holdings include familiar QQQ names;
3. warnings are understandable;
4. strict/prototype examples are present when `--as-of` is provided.

- [ ] **Step 6: Commit live test**

```powershell
git add tests/test_qqq_nport_universe.py
git commit -m "test: add qqq nport live sec smoke"
```

Do not commit generated files under `data/universe/qqq_nport/`.

---

### Task 7b: Resolve SEC Identifiers To Tickers With OpenFIGI

Live SEC validation showed that real QQQ N-PORT equity rows often include CUSIP/ISIN identifiers but no ticker fields. The holdings source remains SEC N-PORT; OpenFIGI is used only as a narrow identifier-normalization layer to translate SEC-provided CUSIP/ISIN values to tradable tickers when the XML lacks ticker data.

Implementation notes:

1. Use stdlib `urllib` against `https://api.openfigi.com/v3/mapping`.
2. Support `OPENFIGI_API_KEY` or an explicit API key parameter, sent as `X-OPENFIGI-APIKEY` when present.
3. Use conservative batches, defaulting to 10 mapping jobs per request.
4. Cache successful mappings under the QQQ N-PORT data directory, for example `mappings/openfigi_symbol_cache.json`.
5. Resolve CUSIP first, then ISIN fallback, using `exchCode` `US` for US identifiers.
6. Keep unresolved rows in `excluded_holdings`; do not fabricate symbols from identifiers.
7. Include clear warning/report metadata when identifier mapping fails or leaves equity rows unresolved.
8. Keep all unit tests network-free with an injected fake OpenFIGI client.

Verification:

```powershell
python -m pytest tests/test_qqq_nport_universe.py -m "not apitest" -q
python -m ruff check lumibot/tools/universe/qqq_nport.py scripts/collect_qqq_nport_universe.py tests/test_qqq_nport_universe.py
python scripts\collect_qqq_nport_universe.py --limit 1 --write-report --as-of 2026-06-01
```

Do not commit generated OpenFIGI cache or QQQ N-PORT data files.

---

### Task 8: Final Verification And Hygiene

**Files:**
- No planned production changes.
- Possible minor docs note only if verification reveals caveats that should be recorded.

- [ ] **Step 1: Run focused unit tests**

Run:

```powershell
python -m pytest tests/test_qqq_nport_universe.py -m "not apitest" -q
```

Expected: PASS.

- [ ] **Step 2: Run relevant existing strategy tests to prove no strategy behavior changed**

Run:

```powershell
python -m pytest tests/test_ai_trading_team_equity_only_llm.py -q
```

Expected: PASS. If this fails, inspect whether the failure is unrelated; the QQQ data layer should not modify equity-only strategy behavior.

- [ ] **Step 3: Run lint on changed files**

Run:

```powershell
python -m ruff check lumibot/tools/universe/qqq_nport.py scripts/collect_qqq_nport_universe.py tests/test_qqq_nport_universe.py
```

Expected: PASS. If `ruff` is unavailable in the environment, run:

```powershell
python -m compileall lumibot/tools/universe/qqq_nport.py scripts/collect_qqq_nport_universe.py tests/test_qqq_nport_universe.py
```

Expected: all files compile.

- [ ] **Step 4: Confirm generated data is not staged**

Run:

```powershell
git status --short
```

Expected: no staged `data/universe/qqq_nport/` files. If generated data appears, leave it untracked or add a focused `.gitignore` entry only if needed.

- [ ] **Step 5: Final commit only if hygiene fixes were needed**

If any minor hygiene changes were made:

```powershell
git add <changed-files>
git commit -m "chore: finalize qqq nport universe collector"
```

If no changes were made, do not create an empty commit.

---

## Implementation Notes

1. Keep all tests network-free except the explicit `@pytest.mark.apitest` live smoke.
2. Use `strict` mode as the default as-of resolver mode.
3. Do not expose this as an LLM tool in this feature.
4. Do not replace the current fixed 50-stock universe in this feature.
5. Do not reintroduce the discarded Alpaca all-market universe collector.
6. Prefer clear errors and validation warnings over silent data loss.
7. Preserve raw excluded holdings in normalized snapshots for audit.

## Self-Review

Spec coverage:

1. SEC discovery: Task 1.
2. XML download/reuse: Task 4.
3. XML parsing and holding normalization: Task 2.
4. Local snapshot storage: Task 3.
5. Strict/prototype as-of resolution: Task 3.
6. Validation report: Task 5.
7. CLI wrapper: Task 6.
8. Network-free tests and live smoke: Tasks 1-8.
9. No strategy integration: Task 8 verifies existing strategy tests and no strategy files are in planned changes.

Red-flag scan: no incomplete implementation instructions are present.

Type consistency: public names used across tasks are `FilingMetadata`, `Holding`, `SnapshotResolution`, `discover_nport_filings_from_submissions`, `parse_nport_xml`, `write_normalized_snapshot`, `resolve_qqq_snapshot`, `collect_qqq_nport_snapshots`, `build_validation_report`, `write_validation_report`, `parse_args`, and `main`.
