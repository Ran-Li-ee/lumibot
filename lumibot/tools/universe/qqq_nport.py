from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

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
    symbol: str
    name: str
    cusip: str | None = None
    isin: str | None = None
    value_usd: Decimal | None = None
    balance: Decimal | None = None
    units: str | None = None
    percent_value: Decimal | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "cusip": self.cusip,
            "isin": self.isin,
            "value_usd": str(self.value_usd) if self.value_usd is not None else None,
            "balance": str(self.balance) if self.balance is not None else None,
            "units": self.units,
            "percent_value": str(self.percent_value) if self.percent_value is not None else None,
        }


@dataclass(frozen=True)
class SnapshotResolution:
    symbol: str
    as_of_date: date
    filing: FilingMetadata
    holdings: tuple[Holding, ...] = field(default_factory=tuple)
    schema_version: int = SNAPSHOT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "as_of_date": self.as_of_date.isoformat(),
            "filing": self.filing.to_dict(),
            "holdings": [holding.to_dict() for holding in self.holdings],
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
        document=primary_document,
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
