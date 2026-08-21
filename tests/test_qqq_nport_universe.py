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


def _snapshot(accession, report_date, filing_date, symbols):
    metadata = qqq_nport.build_filing_metadata(
        cik="0001067839",
        accession_number=accession,
        filing_date=filing_date,
        report_date=report_date,
        primary_document="primary_doc.xml",
    )
    holdings = tuple(
        qqq_nport.Holding(symbol=symbol, name=f"{symbol} Inc.", value_usd=100.0)
        for symbol in symbols
    )
    return qqq_nport.SnapshotResolution(
        fund_symbol="QQQ",
        report_date=date.fromisoformat(report_date),
        filing_date=date.fromisoformat(filing_date),
        filing=metadata,
        holdings=holdings,
        included_value_usd=100.0 * len(holdings),
        summary=qqq_nport.SnapshotSummary(top_holdings=holdings),
    )


def test_write_snapshot_and_resolve_strict_as_of(tmp_path):
    qqq_nport.write_normalized_snapshot(
        _snapshot("0001067839-26-000016", "2025-12-31", "2026-02-27", ["AAPL"]),
        data_dir=tmp_path,
    )
    qqq_nport.write_normalized_snapshot(
        _snapshot("0001067839-26-000024", "2026-03-31", "2026-05-28", ["MSFT"]),
        data_dir=tmp_path,
    )

    result = qqq_nport.resolve_qqq_snapshot("2026-04-15", mode="strict", data_dir=tmp_path)

    assert result.as_of_date == date(2026, 4, 15)
    assert result.mode == "strict"
    assert result.selected_report_date == date(2025, 12, 31)
    assert result.selected_filing_date == date(2026, 2, 27)
    assert result.accession_number == "0001067839-26-000016"
    assert result.symbols == ("AAPL",)
    assert result.snapshot_path.name == "qqq_nport_2025-12-31_0001067839-26-000016.json"
    assert result.source_url.endswith("/primary_doc.xml")


def test_resolve_prototype_as_of_uses_report_date(tmp_path):
    qqq_nport.write_normalized_snapshot(
        _snapshot("0001067839-26-000016", "2025-12-31", "2026-02-27", ["AAPL"]),
        data_dir=tmp_path,
    )
    qqq_nport.write_normalized_snapshot(
        _snapshot("0001067839-26-000024", "2026-03-31", "2026-05-28", ["MSFT"]),
        data_dir=tmp_path,
    )

    result = qqq_nport.resolve_qqq_snapshot("2026-04-15", mode="prototype", data_dir=tmp_path)

    assert result.mode == "prototype"
    assert result.selected_report_date == date(2026, 3, 31)
    assert result.selected_filing_date == date(2026, 5, 28)
    assert result.accession_number == "0001067839-26-000024"
    assert result.symbols == ("MSFT",)


def test_resolve_as_of_sorts_strict_by_filing_date_and_prototype_by_report_date(tmp_path):
    qqq_nport.write_normalized_snapshot(
        _snapshot("0001067839-26-000024", "2026-03-31", "2026-04-01", ["AAPL"]),
        data_dir=tmp_path,
    )
    qqq_nport.write_normalized_snapshot(
        _snapshot("0001067839-26-000016", "2026-02-28", "2026-04-15", ["MSFT"]),
        data_dir=tmp_path,
    )

    strict_result = qqq_nport.resolve_qqq_snapshot("2026-04-20", mode="strict", data_dir=tmp_path)
    prototype_result = qqq_nport.resolve_qqq_snapshot(
        "2026-04-20",
        mode="prototype",
        data_dir=tmp_path,
    )

    assert strict_result.selected_filing_date == date(2026, 4, 15)
    assert strict_result.symbols == ("MSFT",)
    assert prototype_result.selected_report_date == date(2026, 3, 31)
    assert prototype_result.symbols == ("AAPL",)


def test_resolve_as_of_raises_clear_error_when_no_snapshot_available(tmp_path):
    qqq_nport.write_normalized_snapshot(
        _snapshot("0001067839-26-000016", "2025-12-31", "2026-02-27", ["AAPL"]),
        data_dir=tmp_path,
    )

    with pytest.raises(qqq_nport.NoSnapshotAvailableError, match="No QQQ snapshot available"):
        qqq_nport.resolve_qqq_snapshot("2026-01-01", mode="strict", data_dir=tmp_path)


def test_resolve_as_of_skips_malformed_json_snapshot(tmp_path):
    qqq_nport.write_normalized_snapshot(
        _snapshot("0001067839-26-000016", "2025-12-31", "2026-02-27", ["AAPL"]),
        data_dir=tmp_path,
    )
    bad_path = qqq_nport.normalized_dir(tmp_path) / "qqq_nport_2026-03-31_bad.json"
    bad_path.write_text("{not json", encoding="utf-8")

    result = qqq_nport.resolve_qqq_snapshot("2026-04-15", mode="strict", data_dir=tmp_path)

    assert result.accession_number == "0001067839-26-000016"
    assert result.symbols == ("AAPL",)


def test_resolve_as_of_skips_non_object_json_snapshot(tmp_path):
    qqq_nport.write_normalized_snapshot(
        _snapshot("0001067839-26-000016", "2025-12-31", "2026-02-27", ["AAPL"]),
        data_dir=tmp_path,
    )
    bad_path = qqq_nport.normalized_dir(tmp_path) / "qqq_nport_2026-03-31_bad.json"
    bad_path.write_text("[]", encoding="utf-8")

    result = qqq_nport.resolve_qqq_snapshot("2026-04-15", mode="strict", data_dir=tmp_path)

    assert result.accession_number == "0001067839-26-000016"
    assert result.symbols == ("AAPL",)


def test_resolve_as_of_skips_snapshot_with_invalid_accession_before_selection(tmp_path):
    qqq_nport.write_normalized_snapshot(
        _snapshot("0001067839-26-000016", "2025-12-31", "2026-02-27", ["AAPL"]),
        data_dir=tmp_path,
    )
    malformed = _snapshot("0001067839-26-000024", "2026-03-31", "2026-05-28", ["MSFT"]).to_dict()
    malformed["accession_number"] = None
    malformed["filing"]["accession_number"] = None
    qqq_nport.write_json(
        qqq_nport.normalized_dir(tmp_path) / "qqq_nport_2026-03-31_invalid.json",
        malformed,
    )

    result = qqq_nport.resolve_qqq_snapshot("2026-06-01", mode="strict", data_dir=tmp_path)

    assert result.accession_number == "0001067839-26-000016"
    assert result.symbols == ("AAPL",)


def test_resolve_as_of_skips_snapshot_with_invalid_source_before_selection(tmp_path):
    qqq_nport.write_normalized_snapshot(
        _snapshot("0001067839-26-000016", "2025-12-31", "2026-02-27", ["AAPL"]),
        data_dir=tmp_path,
    )
    malformed = _snapshot("0001067839-26-000024", "2026-03-31", "2026-05-28", ["MSFT"]).to_dict()
    malformed["source_url"] = None
    malformed["sec_xml_url"] = None
    malformed["filing"]["sec_xml_url"] = None
    qqq_nport.write_json(
        qqq_nport.normalized_dir(tmp_path) / "qqq_nport_2026-03-31_invalid_source.json",
        malformed,
    )

    result = qqq_nport.resolve_qqq_snapshot("2026-06-01", mode="strict", data_dir=tmp_path)

    assert result.accession_number == "0001067839-26-000016"
    assert result.symbols == ("AAPL",)
    assert result.source_url is not None


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
    xml_text = NPORT_FIXTURE.read_text(encoding="utf-8")
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
    assert result["filings"][0]["holding_count"] == 2
    assert result["filings"][0]["excluded_count"] == 1


def test_collect_qqq_nport_snapshots_reuses_raw_xml_without_refresh(tmp_path):
    xml_text = NPORT_FIXTURE.read_text(encoding="utf-8")
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
    assert metadata.sec_xml_url not in client.requested_urls


def test_build_validation_report_includes_snapshot_quality(tmp_path):
    snapshot_path = qqq_nport.write_normalized_snapshot(
        _snapshot("new", "2026-03-31", "2026-05-28", ["AAPL", "MSFT"]),
        data_dir=tmp_path,
    )
    result = {
        "summary": {
            "filings_discovered": 1,
            "snapshots_normalized": 1,
            "warnings": ["collection warning"],
            "collected_at": "2026-08-21T12:00:00+00:00",
        },
        "filings": [
            {
                "accession_number": "new",
                "report_date": "2026-03-31",
                "filing_date": "2026-05-28",
                "snapshot_path": str(snapshot_path),
                "holding_count": 2,
                "excluded_count": 0,
                "warnings": ["snapshot warning"],
            }
        ],
    }

    report = qqq_nport.build_validation_report(
        result,
        data_dir=tmp_path,
        example_as_of_dates=["2026-06-01"],
    )

    assert "# QQQ N-PORT Universe Collection Report" in report
    assert "2026-08-21T12:00:00+00:00" in report
    assert "2026-03-31" in report
    assert "new" in report
    assert "Holding count: 2" in report
    assert "Excluded count: 0" in report
    assert "Included value USD: 200.00" in report
    assert "Excluded value USD: 0.00" in report
    assert "AAPL" in report
    assert "MSFT" in report
    assert "snapshot warning" in report
    assert "strict" in report
    assert "prototype" in report


def test_build_validation_report_marks_malformed_as_of_example_unavailable():
    result = {"summary": {}, "filings": []}

    report = qqq_nport.build_validation_report(result, example_as_of_dates=["not-a-date"])

    assert "not-a-date" in report
    assert "unavailable" in report


def test_write_validation_report_writes_markdown_and_json(tmp_path):
    result = {
        "summary": {
            "filings_discovered": 0,
            "snapshots_normalized": 0,
            "warnings": [],
            "collected_at": "now",
        },
        "filings": [],
    }

    paths = qqq_nport.write_validation_report(
        result,
        data_dir=tmp_path,
        timestamp="20260821_120000",
    )

    assert paths["markdown"].name == "qqq_nport_collection_20260821_120000.md"
    assert paths["json"].name == "qqq_nport_collection_20260821_120000.json"
    assert paths["markdown"].exists()
    assert paths["json"].exists()
    assert paths["markdown"].parent == qqq_nport.reports_dir(tmp_path)
    assert qqq_nport.load_snapshot(paths["json"]) == result
