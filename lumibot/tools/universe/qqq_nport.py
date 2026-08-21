from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.request import Request, urlopen
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


class NoSnapshotAvailableError(LookupError):
    """Raised when no stored QQQ snapshot can satisfy an as-of request."""


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


@dataclass(frozen=True)
class AsOfSnapshotResolution:
    as_of_date: date
    mode: str
    selected_report_date: date
    selected_filing_date: date
    accession_number: str
    symbols: tuple[str, ...]
    snapshot_path: Path
    source_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of_date": self.as_of_date.isoformat(),
            "mode": self.mode,
            "selected_report_date": self.selected_report_date.isoformat(),
            "selected_filing_date": self.selected_filing_date.isoformat(),
            "accession_number": self.accession_number,
            "symbols": list(self.symbols),
            "snapshot_path": str(self.snapshot_path),
            "source_url": self.source_url,
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


def raw_dir(data_dir: str | Path | None = None) -> Path:
    return Path(data_dir or DEFAULT_DATA_DIR) / "raw"


def normalized_dir(data_dir: str | Path | None = None) -> Path:
    return Path(data_dir or DEFAULT_DATA_DIR) / "normalized"


def reports_dir(data_dir: str | Path | None = None) -> Path:
    return Path(data_dir or DEFAULT_DATA_DIR) / "reports"


def _snapshot_mapping(snapshot: Mapping[str, Any] | SnapshotResolution) -> dict[str, Any]:
    if hasattr(snapshot, "to_dict"):
        data = snapshot.to_dict()
    else:
        data = dict(snapshot)

    filing = data.get("filing")
    if isinstance(filing, Mapping):
        data.setdefault("cik", filing.get("cik"))
        data.setdefault("accession_number", filing.get("accession_number"))
        data.setdefault("sec_index_url", filing.get("sec_index_url"))
        data.setdefault("sec_xml_url", filing.get("sec_xml_url"))
        data.setdefault("source_url", filing.get("sec_xml_url"))
    data.setdefault("source", "sec_nport")
    return data


def snapshot_filename(snapshot: Mapping[str, Any] | SnapshotResolution) -> str:
    data = _snapshot_mapping(snapshot)
    report_date = data.get("report_date")
    accession_number = data.get("accession_number")
    if not isinstance(report_date, str):
        raise ValueError("snapshot report_date must be an ISO date string")
    if not isinstance(accession_number, str):
        raise ValueError("snapshot accession_number must be a string")
    return f"qqq_nport_{report_date}_{accession_number}.json"


def _json_ready(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def write_json(path: str | Path, data: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(_json_ready(data), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_normalized_snapshot(
    snapshot: Mapping[str, Any] | SnapshotResolution,
    data_dir: str | Path | None = None,
) -> Path:
    data = _snapshot_mapping(snapshot)
    path = normalized_dir(data_dir) / snapshot_filename(data)
    write_json(path, data)
    return path


def load_snapshot(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def iter_snapshot_paths(data_dir: str | Path | None = None) -> list[Path]:
    directory = normalized_dir(data_dir)
    if not directory.exists():
        return []
    return sorted(directory.glob("qqq_nport_*.json"))


def _snapshot_field(snapshot: Mapping[str, Any], name: str) -> Any:
    value = snapshot.get(name)
    if value is not None:
        return value
    filing = snapshot.get("filing")
    if isinstance(filing, Mapping):
        return filing.get(name)
    return None


def _snapshot_symbols(snapshot: Mapping[str, Any]) -> tuple[str, ...]:
    holdings = snapshot.get("holdings")
    if not isinstance(holdings, list):
        return ()
    symbols: list[str] = []
    for holding in holdings:
        if not isinstance(holding, Mapping):
            continue
        symbol = holding.get("symbol")
        if isinstance(symbol, str) and symbol:
            symbols.append(symbol)
    return tuple(symbols)


def resolve_qqq_snapshot(
    as_of_date: date | str,
    mode: str = "strict",
    data_dir: str | Path | None = None,
) -> AsOfSnapshotResolution:
    as_of = parse_date(as_of_date)
    if mode not in {"strict", "prototype"}:
        raise ValueError("mode must be 'strict' or 'prototype'")

    candidates: list[tuple[date, date, Path, Mapping[str, Any], str, str]] = []
    for path in iter_snapshot_paths(data_dir):
        try:
            snapshot = load_snapshot(path)
        except json.JSONDecodeError:
            continue
        if not isinstance(snapshot, Mapping):
            continue
        try:
            report = parse_date(_snapshot_field(snapshot, "report_date"))
            filing = parse_date(_snapshot_field(snapshot, "filing_date"))
        except (TypeError, ValueError):
            continue
        accession_number = _snapshot_field(snapshot, "accession_number")
        if not isinstance(accession_number, str):
            continue
        source_url = _snapshot_field(snapshot, "source_url") or _snapshot_field(snapshot, "sec_xml_url")
        if not isinstance(source_url, str) or not source_url:
            continue
        effective_date = filing if mode == "strict" else report
        if effective_date <= as_of:
            candidates.append((report, filing, path, snapshot, accession_number, source_url))

    if not candidates:
        raise NoSnapshotAvailableError(
            f"No QQQ snapshot available for as_of_date={as_of.isoformat()} mode={mode}"
        )

    if mode == "strict":
        report, filing, path, snapshot, accession_number, source_url = max(
            candidates,
            key=lambda item: (item[1], item[0]),
        )
    else:
        report, filing, path, snapshot, accession_number, source_url = max(
            candidates,
            key=lambda item: (item[0], item[1]),
        )
    return AsOfSnapshotResolution(
        as_of_date=as_of,
        mode=mode,
        selected_report_date=report,
        selected_filing_date=filing,
        accession_number=accession_number,
        symbols=_snapshot_symbols(snapshot),
        snapshot_path=path,
        source_url=source_url,
    )


class SecClient:
    def __init__(self, *, user_agent: str = DEFAULT_USER_AGENT, timeout: int = 30):
        self.user_agent = user_agent
        self.timeout = timeout

    def _request(self, url: str) -> Request:
        return Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept-Encoding": "identity",
            },
        )

    def get_json(self, url: str) -> dict[str, Any]:
        with urlopen(self._request(url), timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"SEC JSON response must be an object for {url}")
        return payload

    def get_text(self, url: str) -> str:
        with urlopen(self._request(url), timeout=self.timeout) as response:
            return response.read().decode("utf-8")


def fetch_submissions_json(
    *,
    cik: str = QQQ_CIK,
    sec_client: Any | None = None,
) -> dict[str, Any]:
    client = sec_client or SecClient()
    return client.get_json(SEC_SUBMISSIONS_URL.format(cik=cik))


def raw_xml_path(metadata: FilingMetadata, *, data_dir: str | Path | None = None) -> Path:
    return raw_dir(data_dir) / f"{metadata.accession_number}.xml"


def download_or_read_xml(
    metadata: FilingMetadata,
    *,
    data_dir: str | Path | None = None,
    sec_client: Any | None = None,
    refresh: bool = False,
) -> tuple[str, str, Path]:
    path = raw_xml_path(metadata, data_dir=data_dir)
    if path.exists() and not refresh:
        return path.read_text(encoding="utf-8"), "reused", path

    client = sec_client or SecClient()
    xml_text = client.get_text(metadata.sec_xml_url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(xml_text, encoding="utf-8")
    return xml_text, "downloaded", path


def collect_qqq_nport_snapshots(
    *,
    limit: int | None = None,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    data_dir: str | Path | None = None,
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

    result_rows: list[dict[str, Any]] = []
    snapshot_paths: list[str] = []
    warnings: list[str] = []

    for metadata in filings:
        try:
            xml_text, status, raw_path = download_or_read_xml(
                metadata,
                data_dir=data_dir,
                sec_client=client,
                refresh=refresh,
            )
            snapshot = parse_nport_xml(
                xml_text,
                metadata=metadata,
                downloaded_at=downloaded_at,
            )
            snapshot_path = write_normalized_snapshot(snapshot, data_dir=data_dir)
            snapshot_paths.append(str(snapshot_path))
            result_rows.append(
                {
                    **metadata.to_dict(),
                    "download_status": status,
                    "raw_path": str(raw_path),
                    "snapshot_path": str(snapshot_path),
                    "holding_count": snapshot.holding_count,
                    "excluded_count": snapshot.excluded_count,
                    "warnings": list(snapshot.warnings),
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
    result = {
        "summary": summary,
        "filings": result_rows,
        "snapshot_paths": snapshot_paths,
    }
    write_json(Path(data_dir or DEFAULT_DATA_DIR) / "latest.json", result)
    return result


def _format_money(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _format_weight(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "n/a"


def _collection_warnings(summary: Mapping[str, Any]) -> list[Any]:
    warnings = summary.get("warnings", [])
    if isinstance(warnings, list):
        return warnings
    return []


def _snapshot_report_data(path: Any) -> Mapping[str, Any] | None:
    if not isinstance(path, str):
        return None
    snapshot_path = Path(path)
    if not snapshot_path.exists():
        return None
    try:
        snapshot = load_snapshot(snapshot_path)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(snapshot, Mapping):
        return None
    return snapshot


def build_validation_report(
    collection_result: Mapping[str, Any],
    *,
    data_dir: Path | str | None = None,
    example_as_of_dates: Iterable[date | str] | None = None,
) -> str:
    summary = collection_result.get("summary", {})
    if not isinstance(summary, Mapping):
        summary = {}
    warnings = _collection_warnings(summary)

    lines = [
        "# QQQ N-PORT Universe Collection Report",
        "",
        "## Summary",
        "",
        f"- Collected at: {summary.get('collected_at', 'n/a')}",
        f"- Filings discovered: {summary.get('filings_discovered', 0)}",
        f"- Snapshots normalized: {summary.get('snapshots_normalized', 0)}",
        f"- Warnings: {len(warnings)}",
        "",
    ]
    if warnings:
        lines.extend(["## Collection Warnings", ""])
        for warning in warnings:
            lines.append(f"- {warning}")
        lines.append("")

    lines.extend(["## Snapshots", ""])
    filings = collection_result.get("filings", [])
    if not isinstance(filings, list):
        filings = []
    for row in filings:
        if not isinstance(row, Mapping):
            continue
        lines.extend(
            [
                f"### {row.get('report_date', 'n/a')} / {row.get('accession_number', 'n/a')}",
                "",
                f"- Filing date: {row.get('filing_date', 'n/a')}",
                f"- Holding count: {row.get('holding_count', 'n/a')}",
                f"- Excluded count: {row.get('excluded_count', 'n/a')}",
            ]
        )

        snapshot = _snapshot_report_data(row.get("snapshot_path"))
        if snapshot is not None:
            lines.append(f"- Included value USD: {_format_money(snapshot.get('included_value_usd'))}")
            lines.append(f"- Excluded value USD: {_format_money(snapshot.get('excluded_value_usd'))}")
            lines.extend(["", "| Symbol | Weight | Value USD |", "|---|---:|---:|"])
            snapshot_summary = snapshot.get("summary", {})
            top_holdings = []
            if isinstance(snapshot_summary, Mapping):
                top_holdings = snapshot_summary.get("top_holdings", [])
            if not isinstance(top_holdings, list) or not top_holdings:
                top_holdings = snapshot.get("holdings", [])
            if not isinstance(top_holdings, list):
                top_holdings = []
            for holding in top_holdings[:10]:
                if not isinstance(holding, Mapping):
                    continue
                lines.append(
                    "| "
                    f"{holding.get('symbol', '')} | "
                    f"{_format_weight(holding.get('weight'))} | "
                    f"{_format_money(holding.get('value_usd'))} |"
                )

        row_warnings = row.get("warnings", [])
        if isinstance(row_warnings, list) and row_warnings:
            lines.extend(["", "Warnings:"])
            for warning in row_warnings:
                lines.append(f"- {warning}")
        lines.append("")

    dates = list(example_as_of_dates or [])
    if dates:
        lines.extend(["## Example As-Of Lookups", ""])
        for as_of in dates:
            as_of_text = parse_date(as_of).isoformat()
            for mode in ("strict", "prototype"):
                try:
                    resolution = resolve_qqq_snapshot(as_of, mode=mode, data_dir=data_dir)
                    lines.append(
                        f"- {as_of_text} `{mode}` -> "
                        f"{resolution.accession_number}, {len(resolution.symbols)} symbols"
                    )
                except (NoSnapshotAvailableError, ValueError) as exc:
                    lines.append(f"- {as_of_text} `{mode}` -> unavailable: {exc}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_validation_report(
    collection_result: Mapping[str, Any],
    *,
    data_dir: Path | str | None = None,
    timestamp: str | None = None,
    example_as_of_dates: Iterable[date | str] | None = None,
) -> dict[str, Path]:
    report_timestamp = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    directory = reports_dir(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    markdown_path = directory / f"qqq_nport_collection_{report_timestamp}.md"
    json_path = directory / f"qqq_nport_collection_{report_timestamp}.json"
    markdown_path.write_text(
        build_validation_report(
            collection_result,
            data_dir=data_dir,
            example_as_of_dates=example_as_of_dates,
        ),
        encoding="utf-8",
    )
    write_json(json_path, collection_result)
    return {"markdown": markdown_path, "json": json_path}


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
