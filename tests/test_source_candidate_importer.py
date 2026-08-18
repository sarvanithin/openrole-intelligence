import csv

import pytest

from fortune_intel.importers.source_candidates import import_reviewed_source_candidates
from fortune_intel.storage import JobRepository


def _write(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
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
        writer.writerows(rows)


def test_imports_exact_reviewed_candidate_with_audit_evidence(tmp_path):
    repository = JobRepository(tmp_path / "candidates.db")
    repository.initialize()
    company_id = repository.upsert_company("Example")
    registry = tmp_path / "candidates.csv"
    _write(
        registry,
        [
            {
                "company_name": "Example",
                "candidate_url": "https://job-boards.greenhouse.io/example/jobs/123",
                "kind": "greenhouse",
                "source_url": "https://example.com/careers",
                "verified_at": "2026-08-11T17:00:00+00:00",
                "actor": "reviewer@example.org",
            }
        ],
    )

    assert import_reviewed_source_candidates(repository, registry) == 1
    candidate = repository.get_source_candidate(1)
    assert candidate["company_id"] == company_id
    assert candidate["candidate_url"] == "https://boards.greenhouse.io/example"
    assert candidate["kind"] == "greenhouse"
    assert candidate["evidence"]["source_url"] == "https://example.com/careers"
    assert repository.get_company_coverage(company_id)["disposition"] == "candidate"


def test_candidate_registry_validates_all_rows_before_writing(tmp_path):
    repository = JobRepository(tmp_path / "atomic-candidates.db")
    repository.initialize()
    repository.upsert_company("First")
    repository.upsert_company("Second")
    registry = tmp_path / "candidates.csv"
    base = {
        "kind": "greenhouse",
        "source_url": "https://evidence.example/careers",
        "verified_at": "2026-08-11T17:00:00+00:00",
        "actor": "reviewer@example.org",
    }
    _write(
        registry,
        [
            {
                **base,
                "company_name": "First",
                "candidate_url": "https://boards.greenhouse.io/first",
            },
            {**base, "company_name": "Second", "candidate_url": "https://evil.example/jobs"},
        ],
    )

    with pytest.raises(ValueError, match="not a recognized exact ATS URL"):
        import_reviewed_source_candidates(repository, registry)
    with repository.connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM career_source_candidates").fetchone()[0] == 0
        )


def test_icims_candidate_import_requires_and_records_exact_allowed_robots_review(tmp_path):
    repository = JobRepository(tmp_path / "icims-candidate.db")
    repository.initialize()
    repository.upsert_company("Example")
    registry = tmp_path / "icims.csv"
    fields = (
        "company_name",
        "candidate_url",
        "kind",
        "source_url",
        "verified_at",
        "actor",
        "robots_url",
        "robots_status",
        "robots_checked_at",
    )
    row = {
        "company_name": "Example",
        "candidate_url": "https://careers-example.icims.com/jobs/search",
        "kind": "icims_public",
        "source_url": "https://example.com/careers",
        "verified_at": "2026-08-12T17:00:00+00:00",
        "actor": "reviewer@example.org",
        "robots_url": "https://careers-example.icims.com/robots.txt",
        "robots_status": "allowed",
        "robots_checked_at": "2026-08-12T16:00:00+00:00",
    }
    with registry.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow(row)

    assert import_reviewed_source_candidates(repository, registry) == 1
    candidate = repository.get_source_candidate(1)
    assert candidate["kind"] == "icims_public"
    assert candidate["robots_status"] == "allowed"
    assert candidate["robots_checked_at"] == "2026-08-12T16:00:00+00:00"

    row["robots_url"] = "https://other.icims.com/robots.txt"
    with registry.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow(row)
    with pytest.raises(ValueError, match="same-host allowed robots review"):
        import_reviewed_source_candidates(repository, registry)


def test_imports_explicit_primary_source_structured_manifest(tmp_path):
    repository = JobRepository(tmp_path / "structured-candidate.db")
    repository.initialize()
    repository.upsert_company("Example")
    registry = tmp_path / "structured.csv"
    _write(
        registry,
        [{
            "company_name": "Example",
            "candidate_url": "https://careers.example.com/job-sitemap.xml",
            "kind": "official_structured",
            "source_url": "https://example.com/careers",
            "verified_at": "2026-08-13T12:00:00+00:00",
            "actor": "reviewer@example.org",
        }],
    )

    assert import_reviewed_source_candidates(repository, registry) == 1
    candidate = repository.get_source_candidate(1)
    assert candidate["kind"] == "official_structured"
    assert candidate["evidence"]["source_url"] == "https://example.com/careers"
