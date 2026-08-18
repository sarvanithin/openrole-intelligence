import csv

import pytest

from fortune_intel.importers.websites import import_company_websites
from fortune_intel.storage import JobRepository


def write_registry(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "company_name",
                "website_url",
                "career_url",
                "source_url",
                "verified_at",
                "actor",
            ),
        )
        writer.writeheader()
        writer.writerows(rows)


def test_imports_reviewed_website_seed_and_audit_event(tmp_path):
    repository = JobRepository(tmp_path / "websites.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Company")
    registry = tmp_path / "websites.csv"
    write_registry(
        registry,
        [
            {
                "company_name": "Example Company",
                "website_url": "https://www.example.com",
                "career_url": "https://www.example.com/careers",
                "source_url": "https://www.sec.gov/example",
                "verified_at": "2026-08-06T12:00:00+00:00",
                "actor": "reviewer@example.org",
            }
        ],
    )

    assert import_company_websites(repository, registry) == 1
    company = repository.find_company_by_normalized_name("Example Company")
    assert company["website_url"] == "https://www.example.com/"
    assert company["career_url"] == "https://www.example.com/careers"
    event = repository.company_coverage_events(company_id)[0]
    assert event["actor"] == "reviewer@example.org"
    assert "sec.gov" in event["reason"]
    assert "reviewed career URL https://www.example.com/careers" in event["reason"]


def test_registry_validation_completes_before_any_write(tmp_path):
    repository = JobRepository(tmp_path / "atomic-validation.db")
    repository.initialize()
    repository.upsert_company("First")
    repository.upsert_company("Second")
    registry = tmp_path / "websites.csv"
    write_registry(
        registry,
        [
            {
                "company_name": "First",
                "website_url": "https://first.example",
                "career_url": "",
                "source_url": "https://source.example/first",
                "verified_at": "2026-08-06T12:00:00+00:00",
                "actor": "reviewer@example.org",
            },
            {
                "company_name": "Second",
                "website_url": "http://127.0.0.1/internal",
                "career_url": "",
                "source_url": "https://source.example/second",
                "verified_at": "2026-08-06T12:00:00+00:00",
                "actor": "reviewer@example.org",
            },
        ],
    )

    with pytest.raises(ValueError, match="public HTTP"):
        import_company_websites(repository, registry)
    assert repository.find_company_by_normalized_name("First")["website_url"] == ""


def test_imports_website_without_a_career_url(tmp_path):
    repository = JobRepository(tmp_path / "website-only.db")
    repository.initialize()
    repository.upsert_company("Example Company")
    registry = tmp_path / "websites.csv"
    write_registry(
        registry,
        [{
            "company_name": "Example Company",
            "website_url": "https://www.example.com",
            "career_url": "",
            "source_url": "https://www.example.com",
            "verified_at": "2026-08-06T12:00:00+00:00",
            "actor": "reviewer@example.org",
        }],
    )

    assert import_company_websites(repository, registry) == 1
    assert repository.find_company_by_normalized_name("Example Company")["website_url"] == (
        "https://www.example.com/"
    )
