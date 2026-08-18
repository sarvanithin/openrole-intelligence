from __future__ import annotations

from argparse import Namespace

import pytest

from fortune_intel import cli_sec_filings


class FakeRepository:
    def __init__(self) -> None:
        self.companies = [
            {"id": 1, "name": "New Seed", "website_url": "", "career_url": ""},
            {
                "id": 2,
                "name": "Existing Seed",
                "website_url": "https://existing.example/",
                "career_url": "",
            },
        ]

    def list_companies(self, *, include_synthetic: bool):
        assert include_synthetic is False
        return [dict(company) for company in self.companies]


def arguments(**overrides) -> Namespace:
    values = {
        "user_agent": "OpenRole-CIKBot/0.1 ops@example.org",
        "rate_per_second": 5.0,
        "concurrency": 1,
        "actor": "ops@example.org",
        "limit": 100,
        "after_cik": None,
        "dry_run": False,
        "discover_new": False,
        "discovery_concurrency": 4,
    }
    values.update(overrides)
    return Namespace(**values)


def test_import_only_is_default_and_passes_resume_cursor(monkeypatch):
    repository = FakeRepository()
    calls = []
    monkeypatch.setattr(cli_sec_filings, "SecFilingWebsiteClient", lambda **values: values)

    def import_websites(repo, client, **values):
        calls.append((repo, client, values))
        return {"websites_imported": 0}

    monkeypatch.setattr(cli_sec_filings, "import_sec_filing_company_websites", import_websites)
    monkeypatch.setattr(
        cli_sec_filings,
        "discover_company_sources",
        lambda *_args, **_kwargs: pytest.fail("discovery must be opt-in"),
    )

    result = cli_sec_filings.run_sec_filing_website_command(
        arguments(after_cik="0000123456"), repository
    )

    assert result == {"import": {"websites_imported": 0}}
    assert calls[0][1]["requests_per_second"] == 5.0
    assert calls[0][1]["concurrency"] == 1
    assert calls[0][2]["after_cik"] == "0000123456"


def test_opt_in_discovery_targets_only_seeds_changed_by_import(monkeypatch):
    repository = FakeRepository()
    monkeypatch.setattr(cli_sec_filings, "SecFilingWebsiteClient", lambda **_values: object())

    def import_websites(repo, _client, **_values):
        repo.companies[0]["website_url"] = "https://new.example/"
        return {"websites_imported": 1}

    discovery_calls = []

    def discover(repo, targets, **values):
        discovery_calls.append((repo, list(targets), values))
        return [{"company_id": 1, "candidate_ids": [10]}]

    monkeypatch.setattr(cli_sec_filings, "import_sec_filing_company_websites", import_websites)
    monkeypatch.setattr(cli_sec_filings, "discover_company_sources", discover)

    result = cli_sec_filings.run_sec_filing_website_command(
        arguments(discover_new=True), repository
    )

    assert [company["id"] for company in discovery_calls[0][1]] == [1]
    assert discovery_calls[0][2] == {"actor": "ops@example.org", "concurrency": 4}
    assert result["discovery"]["approval"] == "not_performed"
    assert result["discovery"]["new_verified_seeds"] == 1


def test_discovery_rejects_dry_run_and_out_of_range_concurrency():
    repository = FakeRepository()

    with pytest.raises(ValueError, match="cannot be combined"):
        cli_sec_filings.run_sec_filing_website_command(
            arguments(discover_new=True, dry_run=True), repository
        )
    with pytest.raises(ValueError, match="between 1 and 8"):
        cli_sec_filings.run_sec_filing_website_command(
            arguments(discovery_concurrency=9), repository
        )
    with pytest.raises(ValueError, match="concurrency must be between"):
        cli_sec_filings.run_sec_filing_website_command(arguments(concurrency=9), repository)
