import json

from fortune_intel.importers.sec_companies import import_sec_companies
from fortune_intel.storage import JobRepository


def test_sec_company_ticker_import_builds_public_company_universe(tmp_path):
    repository = JobRepository(tmp_path / "sec.db")
    repository.initialize()
    source = tmp_path / "company_tickers.json"
    source.write_text(
        json.dumps(
            {
                "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
                "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
            }
        )
    )

    result = import_sec_companies(repository, source, collection_year=2026)

    assert result == {"companies_imported": 2, "records_skipped": 0}
    assert {company["name"] for company in repository.list_companies()} == {
        "Apple Inc.",
        "Microsoft Corp",
    }
    microsoft = repository.find_company_by_normalized_name("Microsoft Corp")
    assert microsoft["sec_cik"] == "0000789019"
    assert microsoft["ticker"] == "MSFT"
