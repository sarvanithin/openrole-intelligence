import csv

import pytest

from fortune_intel.cli import parser
from fortune_intel.importers.website_leads import import_website_leads
from fortune_intel.storage import JobRepository


FIELDS = (
    "company_id",
    "company_name",
    "website_url",
    "source_dataset",
    "source_record_id",
    "source_url",
    "source_checksum",
    "license_id",
    "license_url",
    "license_status",
    "license_reviewed_at",
    "retrieved_at",
    "actor",
)


def _write(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _row(company_id, company_name="Example Industries, Inc.", **overrides):
    result = {
        "company_id": company_id,
        "company_name": company_name,
        "website_url": "https://www.example-industries.com/about",
        "source_dataset": "Licensed company directory 2026-08",
        "source_record_id": "record-42",
        "source_url": "https://directory.example/downloads/2026-08",
        "source_checksum": "b" * 64,
        "license_id": "MIT",
        "license_url": "https://directory.example/license",
        "license_status": "permitted",
        "license_reviewed_at": "2026-08-14T08:00:00+00:00",
        "retrieved_at": "2026-08-14T09:00:00+00:00",
        "actor": "license-reviewer@example.org",
    }
    result.update(overrides)
    return result


def test_import_keeps_licensed_website_as_passive_unverified_inventory(tmp_path):
    repository = JobRepository(tmp_path / "website-leads.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Industries, Inc.")
    registry = tmp_path / "website-leads.csv"
    _write(registry, [_row(company_id)])

    assert import_website_leads(repository, registry) == 1

    company = repository.find_company_by_normalized_name("Example Industries, Inc.")
    assert company["website_url"] == ""
    fingerprint = repository.list_source_fingerprints(company_id)[0]
    assert fingerprint["observed_url"] == "https://www.example-industries.com/about"
    assert fingerprint["evidence"]["review_method"] == "licensed_company_website_lead"
    assert fingerprint["evidence"]["verification_status"] == "unverified"
    assert fingerprint["evidence"]["website_seed_promotion_allowed"] is False
    assert fingerprint["evidence"]["primary_site_identity_confirmation_required"] is True


def test_website_lead_registry_fails_before_writing_when_identity_is_not_exact(tmp_path):
    repository = JobRepository(tmp_path / "website-leads.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Industries, Inc.")
    registry = tmp_path / "website-leads.csv"
    _write(registry, [_row(company_id, company_name="Example Industries LLC")])

    with pytest.raises(ValueError, match="exact company identity mismatch"):
        import_website_leads(repository, registry)
    assert repository.list_source_fingerprints(company_id) == []


def test_cli_exposes_separate_website_lead_commands():
    imported = parser().parse_args(["import-website-leads", "website-leads.csv"])
    verified = parser().parse_args(["verify-website-leads", "--actor", "scheduler"])

    assert imported.command == "import-website-leads"
    assert verified.command == "verify-website-leads"
    assert verified.limit == 100
