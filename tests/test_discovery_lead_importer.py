import csv

import pytest

from fortune_intel.importers.discovery_leads import import_discovery_leads
from fortune_intel.cli import parser
from fortune_intel.importers.source_candidates import import_reviewed_source_candidates
from fortune_intel.storage import JobRepository


FIELDS = (
    "company_id",
    "company_name",
    "lead_url",
    "kind",
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


def _row(company_id, company_name="Example", **overrides):
    result = {
        "company_id": company_id,
        "company_name": company_name,
        "lead_url": "https://boards.greenhouse.io/example",
        "kind": "greenhouse",
        "source_dataset": "Licensed ATS directory 2026-08",
        "source_record_id": "record-42",
        "source_url": "https://directory.example/downloads/2026-08",
        "source_checksum": "a" * 64,
        "license_id": "CC-BY-4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "license_status": "permitted",
        "license_reviewed_at": "2026-08-12T08:00:00+00:00",
        "retrieved_at": "2026-08-12T09:00:00+00:00",
        "actor": "license-reviewer@example.org",
    }
    result.update(overrides)
    return result


def test_imports_supported_ats_as_unverified_passive_lead(tmp_path):
    repository = JobRepository(tmp_path / "leads.db")
    repository.initialize()
    company_id = repository.upsert_company("Example")
    registry = tmp_path / "leads.csv"
    _write(registry, [_row(company_id)])

    assert import_discovery_leads(repository, registry) == 1

    leads = repository.list_source_fingerprints(company_id)
    assert len(leads) == 1
    assert leads[0]["family"] == "unknown_external"
    evidence = leads[0]["evidence"]
    assert evidence["review_method"] == "third_party_discovery_lead"
    assert evidence["verification_status"] == "unverified"
    assert evidence["activation_allowed"] is False
    assert evidence["primary_source_verification_required"] is True
    assert evidence["proposed_kind"] == "greenhouse"
    assert evidence["source_checksum_sha256"] == "a" * 64
    assert evidence["license"]["status"] == "permitted"
    assert repository.list_source_candidates(company_id) == []
    assert repository.source_status() == []
    coverage = repository.get_company_coverage(company_id)
    assert coverage["disposition"] == "unreviewed"
    assert coverage["last_discovered_at"] is None


def test_cli_exposes_a_separate_unverified_lead_command():
    args = parser().parse_args(["import-discovery-leads", "leads.csv"])

    assert args.command == "import-discovery-leads"
    assert args.csv == "leads.csv"


def test_imports_policy_held_family_as_passive_lead(tmp_path):
    repository = JobRepository(tmp_path / "passive-leads.db")
    repository.initialize()
    company_id = repository.upsert_company("Example")
    registry = tmp_path / "leads.csv"
    _write(
        registry,
        [
            _row(
                company_id,
                lead_url="https://careers-example.icims.com/jobs/search",
                kind="icims",
            )
        ],
    )

    assert import_discovery_leads(repository, registry) == 1
    assert repository.list_source_fingerprints(company_id)[0]["family"] == "icims"
    assert repository.list_source_candidates(company_id) == []


def test_primary_source_review_is_a_separate_promotion_step(tmp_path):
    repository = JobRepository(tmp_path / "promotion.db")
    repository.initialize()
    company_id = repository.upsert_company("Example")
    leads = tmp_path / "leads.csv"
    _write(leads, [_row(company_id)])
    import_discovery_leads(repository, leads)
    reviewed = tmp_path / "reviewed.csv"
    with reviewed.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "company_name",
                "candidate_url",
                "kind",
                "source_url",
                "verified_at",
                "actor",
            ),
        )
        writer.writeheader()
        writer.writerow(
            {
                "company_name": "Example",
                "candidate_url": "https://boards.greenhouse.io/example",
                "kind": "greenhouse",
                "source_url": "https://example.com/careers",
                "verified_at": "2026-08-12T10:00:00+00:00",
                "actor": "primary-reviewer@example.org",
            }
        )

    assert import_reviewed_source_candidates(repository, reviewed) == 1
    candidates = repository.list_source_candidates(company_id)
    assert len(candidates) == 1
    assert candidates[0]["evidence"]["review_method"] == "primary_source_exact_ats_url"
    assert repository.source_status() == []
    assert repository.get_company_coverage(company_id)["disposition"] == "candidate"


def test_exact_identity_mismatch_prevents_all_writes(tmp_path):
    repository = JobRepository(tmp_path / "identity.db")
    repository.initialize()
    first_id = repository.upsert_company("First")
    second_id = repository.upsert_company("Second")
    registry = tmp_path / "leads.csv"
    _write(
        registry,
        [
            _row(first_id, "First", source_record_id="first"),
            _row(second_id, "Second LLC", source_record_id="second"),
        ],
    )

    with pytest.raises(ValueError, match="exact company identity mismatch"):
        import_discovery_leads(repository, registry)
    assert repository.list_source_fingerprints(first_id) == []
    assert repository.list_source_fingerprints(second_id) == []


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"license_status": "unknown"}, "license_status must be permitted"),
        ({"source_checksum": "not-a-checksum"}, "source_checksum must be a SHA-256"),
        ({"lead_url": "https://vendor.example/about"}, "not a recognized bounded"),
        ({"retrieved_at": "2026-08-12"}, "retrieved_at must include a timezone"),
    ],
)
def test_licensing_provenance_and_url_gates_fail_closed(tmp_path, override, message):
    repository = JobRepository(tmp_path / "invalid.db")
    repository.initialize()
    company_id = repository.upsert_company("Example")
    registry = tmp_path / "leads.csv"
    _write(registry, [_row(company_id, **override)])

    with pytest.raises(ValueError, match=message):
        import_discovery_leads(repository, registry)
    assert repository.list_source_fingerprints(company_id) == []
