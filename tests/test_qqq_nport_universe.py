from datetime import date
from pathlib import Path

import pytest

from lumibot.tools.universe import qqq_nport

NPORT_FIXTURE = Path(__file__).parent / "fixtures" / "qqq_nport" / "sample_primary_doc.xml"


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


def test_build_sec_archive_xml_url_uses_raw_document_for_xsl_wrapper_path():
    row = qqq_nport.build_filing_metadata(
        cik="0001067839",
        accession_number="0001067839-26-000024",
        filing_date="2026-05-28",
        report_date="2026-03-31",
        primary_document="xslFormNPORT-P_X01/primary_doc.xml",
    )

    assert row.primary_document == "xslFormNPORT-P_X01/primary_doc.xml"
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


def test_discover_nport_filings_skips_rows_with_invalid_dates():
    submissions = {
        "filings": {
            "recent": {
                "accessionNumber": [
                    "0001067839-26-000024",
                    "0001067839-26-000016",
                ],
                "filingDate": ["not-a-date", "2026-02-27"],
                "reportDate": ["2026-03-31", "2025-12-31"],
                "form": ["NPORT-P", "NPORT-P"],
                "primaryDocument": ["primary_doc.xml", "primary_doc.xml"],
            }
        }
    }

    rows = qqq_nport.discover_nport_filings_from_submissions(submissions, cik="0001067839")

    assert [row.accession_number for row in rows] == ["0001067839-26-000016"]


def test_discover_nport_filings_ignores_scalar_recent_values():
    submissions = {
        "filings": {
            "recent": {
                "accessionNumber": "0001067839-26-000024",
                "filingDate": "2026-05-28",
                "reportDate": {"date": "2026-03-31"},
                "form": "NPORT-P",
                "primaryDocument": "primary_doc.xml",
            }
        }
    }

    rows = qqq_nport.discover_nport_filings_from_submissions(submissions, cik="0001067839")

    assert rows == []


def test_discover_nport_filings_skips_rows_with_non_string_primary_document():
    submissions = {
        "filings": {
            "recent": {
                "accessionNumber": [
                    "0001067839-26-000024",
                    "0001067839-26-000016",
                ],
                "filingDate": ["2026-05-28", "2026-02-27"],
                "reportDate": ["2026-03-31", "2025-12-31"],
                "form": ["NPORT-P", "NPORT-P"],
                "primaryDocument": [{"bad": "doc"}, "primary_doc.xml"],
            }
        }
    }

    rows = qqq_nport.discover_nport_filings_from_submissions(submissions, cik="0001067839")

    assert [row.accession_number for row in rows] == ["0001067839-26-000016"]
    assert rows[0].primary_document == "primary_doc.xml"


def test_parse_nport_xml_normalizes_holdings_and_exclusions():
    metadata = qqq_nport.build_filing_metadata(
        cik="0001067839",
        accession_number="0001067839-26-000024",
        filing_date="2026-05-28",
        report_date="2026-03-31",
        primary_document="primary_doc.xml",
    )

    snapshot = qqq_nport.parse_nport_xml(NPORT_FIXTURE.read_text(encoding="utf-8"), metadata)

    assert snapshot.schema_version == 1
    assert snapshot.fund_symbol == "QQQ"
    assert snapshot.report_date == date(2026, 3, 31)
    assert snapshot.filing_date == date(2026, 5, 28)
    assert [holding.symbol for holding in snapshot.holdings] == ["AAPL", "MSFT"]
    assert snapshot.holding_count == 2
    assert snapshot.excluded_count == 1
    assert snapshot.included_value_usd == 250000.0
    assert snapshot.excluded_value_usd == 1000.0
    assert snapshot.holdings[0].weight == pytest.approx(0.6)
    assert snapshot.holdings[1].weight == pytest.approx(0.4)
    assert snapshot.excluded_holdings[0].name == "Cash Collateral"
    assert [holding.symbol for holding in snapshot.summary.top_holdings] == ["AAPL", "MSFT"]


def test_parse_nport_xml_warns_on_duplicate_symbols():
    metadata = qqq_nport.build_filing_metadata(
        cik="0001067839",
        accession_number="0001067839-26-000024",
        filing_date="2026-05-28",
        report_date="2026-03-31",
        primary_document="primary_doc.xml",
    )
    xml_text = NPORT_FIXTURE.read_text(encoding="utf-8").replace(
        '<ticker value="MSFT"/>',
        '<ticker value="AAPL"/>',
    )

    snapshot = qqq_nport.parse_nport_xml(xml_text, metadata)

    assert any("duplicate symbol" in warning for warning in snapshot.warnings)


def test_parse_nport_xml_resolves_no_ticker_equity_rows_from_identifiers():
    metadata = qqq_nport.build_filing_metadata(
        cik="0001067839",
        accession_number="0001067839-26-000024",
        filing_date="2026-05-28",
        report_date="2026-03-31",
        primary_document="primary_doc.xml",
    )
    xml_text = (
        NPORT_FIXTURE.read_text(encoding="utf-8")
        .replace('<ticker value="AAPL"/>', "")
        .replace('<ticker value="MSFT"/>', "")
    )

    def resolve_symbol(holding):
        return {
            "037833100": "AAPL",
            "US5949181045": "MSFT",
        }.get(holding.cusip) or {
            "037833100": "AAPL",
            "US5949181045": "MSFT",
        }.get(holding.isin)

    snapshot = qqq_nport.parse_nport_xml(xml_text, metadata, symbol_resolver=resolve_symbol)

    assert [holding.symbol for holding in snapshot.holdings] == ["AAPL", "MSFT"]
    assert snapshot.holding_count == 2
    assert snapshot.excluded_holdings[0].name == "Cash Collateral"


def test_parse_nport_xml_excludes_no_ticker_equity_rows_without_resolver():
    metadata = qqq_nport.build_filing_metadata(
        cik="0001067839",
        accession_number="0001067839-26-000024",
        filing_date="2026-05-28",
        report_date="2026-03-31",
        primary_document="primary_doc.xml",
    )
    xml_text = (
        NPORT_FIXTURE.read_text(encoding="utf-8")
        .replace('<ticker value="AAPL"/>', "")
        .replace('<ticker value="MSFT"/>', "")
    )

    snapshot = qqq_nport.parse_nport_xml(xml_text, metadata)

    assert snapshot.holdings == ()
    assert [holding.cusip for holding in snapshot.excluded_holdings[:2]] == ["037833100", "594918104"]
    assert [holding.symbol for holding in snapshot.excluded_holdings[:2]] == [None, None]
