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
