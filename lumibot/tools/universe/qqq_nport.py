from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Mapping
from xml.etree import ElementTree

from lumibot.constants import LUMIBOT_CACHE_FOLDER

QQQ_CIK = "0001067839"
QQQ_SYMBOL = "QQQ"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVES_BASE_URL = "https://www.sec.gov/Archives/edgar/data"
DEFAULT_USER_AGENT = "LumiBot qqq-nport universe discovery contact@example.com"
DEFAULT_DATA_DIR = Path(LUMIBOT_CACHE_FOLDER) / "universe" / "qqq_nport"
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
        return {
            "cik": self.cik,
            "accession_number": self.accession_number,
            "filing_date": self.filing_date.isoformat(),
            "report_date": self.report_date.isoformat(),
            "primary_document": self.primary_document,
            "sec_index_url": self.sec_index_url,
            "sec_xml_url": self.sec_xml_url,
        }


@dataclass(frozen=True)
class Holding:
    symbol: str | None
    name: str
    title: str | None = None
    cusip: str | None = None
    isin: str | None = None
    value_usd: float | None = None
    balance: float | None = None
    units: str | None = None
    percent_value: float | None = None
    asset_category: str | None = None
    weight: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "title": self.title,
            "cusip": self.cusip,
            "isin": self.isin,
            "value_usd": self.value_usd,
            "balance": self.balance,
            "units": self.units,
            "percent_value": self.percent_value,
            "asset_category": self.asset_category,
            "weight": self.weight,
        }


SymbolResolver = Callable[[Holding], str | None]


@dataclass(frozen=True)
class SnapshotSummary:
    top_holdings: tuple[Holding, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "top_holdings": [holding.to_dict() for holding in self.top_holdings],
        }


@dataclass(frozen=True)
class SnapshotResolution:
    fund_symbol: str
    report_date: date
    filing_date: date
    filing: FilingMetadata
    holdings: tuple[Holding, ...] = field(default_factory=tuple)
    excluded_holdings: tuple[Holding, ...] = field(default_factory=tuple)
    included_value_usd: float = 0.0
    excluded_value_usd: float = 0.0
    warnings: tuple[str, ...] = field(default_factory=tuple)
    summary: SnapshotSummary = field(default_factory=SnapshotSummary)
    downloaded_at: str | None = None
    schema_version: int = SNAPSHOT_SCHEMA_VERSION

    @property
    def symbol(self) -> str:
        return self.fund_symbol

    @property
    def as_of_date(self) -> date:
        return self.report_date

    @property
    def holding_count(self) -> int:
        return len(self.holdings)

    @property
    def excluded_count(self) -> int:
        return len(self.excluded_holdings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fund_symbol": self.fund_symbol,
            "report_date": self.report_date.isoformat(),
            "filing_date": self.filing_date.isoformat(),
            "filing": self.filing.to_dict(),
            "holdings": [holding.to_dict() for holding in self.holdings],
            "excluded_holdings": [holding.to_dict() for holding in self.excluded_holdings],
            "holding_count": self.holding_count,
            "excluded_count": self.excluded_count,
            "included_value_usd": self.included_value_usd,
            "excluded_value_usd": self.excluded_value_usd,
            "warnings": list(self.warnings),
            "summary": self.summary.to_dict(),
            "downloaded_at": self.downloaded_at,
            "schema_version": self.schema_version,
        }


def parse_date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def accession_without_dashes(accession_number: str) -> str:
    return accession_number.replace("-", "")


def cik_without_leading_zeroes(cik: str) -> str:
    stripped = cik.lstrip("0")
    return stripped or "0"


def build_sec_archive_url(
    *,
    cik: str,
    accession_number: str,
    document: str,
) -> str:
    return (
        f"{SEC_ARCHIVES_BASE_URL}/"
        f"{cik_without_leading_zeroes(cik)}/"
        f"{accession_without_dashes(accession_number)}/"
        f"{document}"
    )


def _raw_xml_document_path(primary_document: str) -> str:
    if primary_document.startswith("xslFormNPORT-P_"):
        return primary_document.rsplit("/", 1)[-1]
    return primary_document


def build_filing_metadata(
    *,
    cik: str,
    accession_number: str,
    filing_date: date | str,
    report_date: date | str,
    primary_document: str,
) -> FilingMetadata:
    if not isinstance(accession_number, str):
        raise TypeError("accession_number must be a string")
    if not isinstance(filing_date, (date, str)):
        raise TypeError("filing_date must be a date or string")
    if not isinstance(report_date, (date, str)):
        raise TypeError("report_date must be a date or string")
    if not isinstance(primary_document, str):
        raise TypeError("primary_document must be a string")

    sec_index_url = build_sec_archive_url(
        cik=cik,
        accession_number=accession_number,
        document=f"{accession_number}-index.htm",
    )
    sec_xml_url = build_sec_archive_url(
        cik=cik,
        accession_number=accession_number,
        document=_raw_xml_document_path(primary_document),
    )
    return FilingMetadata(
        cik=cik,
        accession_number=accession_number,
        filing_date=parse_date(filing_date),
        report_date=parse_date(report_date),
        primary_document=primary_document,
        sec_index_url=sec_index_url,
        sec_xml_url=sec_xml_url,
    )


def _recent_filings(submissions: Mapping[str, Any]) -> Mapping[str, list[Any]]:
    filings = submissions.get("filings")
    if not isinstance(filings, Mapping):
        return {}
    recent = filings.get("recent")
    if not isinstance(recent, Mapping):
        return {}
    return {key: value for key, value in recent.items() if isinstance(value, list)}


def discover_nport_filings_from_submissions(
    submissions: Mapping[str, Any],
    *,
    cik: str = QQQ_CIK,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    limit: int | None = None,
) -> list[FilingMetadata]:
    recent = _recent_filings(submissions)
    accession_numbers = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    forms = recent.get("form", [])
    primary_documents = recent.get("primaryDocument", [])

    start = parse_date(start_date) if start_date is not None else None
    end = parse_date(end_date) if end_date is not None else None

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
        except (AttributeError, IndexError, TypeError, ValueError):
            continue

        if start is not None and row.filing_date < start:
            continue
        if end is not None and row.filing_date > end:
            continue
        rows.append(row)

    rows.sort(key=lambda row: (row.filing_date, row.accession_number), reverse=True)
    if limit is not None:
        return rows[:limit]
    return rows


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _child_text(element: ElementTree.Element, *names: str) -> str | None:
    wanted = set(names)
    for child in element.iter():
        if child is element:
            continue
        if _local_name(child.tag) in wanted:
            raw_value = child.text or child.attrib.get("value")
            if raw_value is None:
                continue
            value = raw_value.strip()
            if value:
                return value
    return None


def normalize_symbol(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    symbol = value.strip().upper()
    if symbol in {"", "N/A", "NA", "NIL", "NONE", "NULL"}:
        return None
    if not all(character.isalnum() or character in {".", "-"} for character in symbol):
        return None
    return symbol


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    normalized = value.strip().replace(",", "")
    if not normalized:
        return None
    try:
        return float(normalized)
    except ValueError:
        return None


def _parse_holding(element: ElementTree.Element) -> Holding:
    title = _child_text(element, "title")
    return Holding(
        symbol=normalize_symbol(_child_text(element, "ticker", "issuerTicker")),
        name=_child_text(element, "name") or title or "",
        title=title,
        cusip=_child_text(element, "cusip"),
        isin=_child_text(element, "isin"),
        value_usd=_to_float(_child_text(element, "valUSD")),
        balance=_to_float(_child_text(element, "balance")),
        units=_child_text(element, "units"),
        percent_value=_to_float(_child_text(element, "pctVal", "percentValue")),
        asset_category=_child_text(element, "assetCat"),
    )


def _find_investment_elements(root: ElementTree.Element) -> list[ElementTree.Element]:
    return [
        element
        for element in root.iter()
        if _local_name(element.tag) in {"invstOrSec", "investment"}
    ]


def _is_equity_like(holding: Holding) -> bool:
    return (holding.asset_category or "").upper() in {"EC"}


def _with_weight(holding: Holding, weight: float | None) -> Holding:
    return Holding(
        symbol=holding.symbol,
        name=holding.name,
        title=holding.title,
        cusip=holding.cusip,
        isin=holding.isin,
        value_usd=holding.value_usd,
        balance=holding.balance,
        units=holding.units,
        percent_value=holding.percent_value,
        asset_category=holding.asset_category,
        weight=weight,
    )


def _with_symbol(holding: Holding, symbol: str) -> Holding:
    return Holding(
        symbol=symbol,
        name=holding.name,
        title=holding.title,
        cusip=holding.cusip,
        isin=holding.isin,
        value_usd=holding.value_usd,
        balance=holding.balance,
        units=holding.units,
        percent_value=holding.percent_value,
        asset_category=holding.asset_category,
        weight=holding.weight,
    )


def parse_nport_xml(
    xml_text: str,
    metadata: FilingMetadata,
    downloaded_at: str | None = None,
    symbol_resolver: SymbolResolver | None = None,
) -> SnapshotResolution:
    root = ElementTree.fromstring(xml_text)
    included: list[Holding] = []
    excluded: list[Holding] = []

    for element in _find_investment_elements(root):
        holding = _parse_holding(element)
        if holding.symbol is None and symbol_resolver is not None:
            resolved_symbol = normalize_symbol(symbol_resolver(holding))
            if resolved_symbol is not None:
                holding = _with_symbol(holding, resolved_symbol)
        if holding.symbol is not None and _is_equity_like(holding):
            included.append(holding)
        else:
            excluded.append(holding)

    included_value_usd = sum(holding.value_usd or 0.0 for holding in included)
    excluded_value_usd = sum(holding.value_usd or 0.0 for holding in excluded)

    weighted_holdings = tuple(
        _with_weight(
            holding,
            (holding.value_usd or 0.0) / included_value_usd
            if included_value_usd
            else None,
        )
        for holding in included
    )
    excluded_holdings = tuple(_with_weight(holding, None) for holding in excluded)

    warnings: list[str] = []
    duplicate_symbols = [
        symbol
        for symbol, count in Counter(holding.symbol for holding in weighted_holdings).items()
        if symbol is not None and count > 1
    ]
    for symbol in duplicate_symbols:
        warnings.append(f"duplicate symbol {symbol} in N-PORT holdings")
    if len(weighted_holdings) < 2:
        warnings.append(f"low holding count: {len(weighted_holdings)}")
    total_value_usd = included_value_usd + excluded_value_usd
    if total_value_usd and excluded_value_usd / total_value_usd > 0.2:
        warnings.append("high excluded value ratio")

    top_holdings = tuple(
        sorted(
            weighted_holdings,
            key=lambda holding: holding.weight or 0.0,
            reverse=True,
        )
    )

    return SnapshotResolution(
        fund_symbol=QQQ_SYMBOL,
        report_date=metadata.report_date,
        filing_date=metadata.filing_date,
        filing=metadata,
        holdings=weighted_holdings,
        excluded_holdings=excluded_holdings,
        included_value_usd=included_value_usd,
        excluded_value_usd=excluded_value_usd,
        warnings=tuple(warnings),
        summary=SnapshotSummary(top_holdings=top_holdings),
        downloaded_at=downloaded_at,
    )
