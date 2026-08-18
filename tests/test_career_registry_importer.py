import csv

import pytest

from fortune_intel.cli import parser
from fortune_intel.importers.career_registry import import_career_url_registry
from fortune_intel.storage import JobRepository


FIELDS = (
    "company_id",
    "company_name",
    "verified_website_url",
    "career_url",
    "source",
    "confidence",
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
        "verified_website_url": "https://example.com/",
        "career_url": "https://boards.greenhouse.io/example",
        "source": "user-review",
        "confidence": "95",
    }
    result.update(overrides)
    return result


def test_imports_every_nonblank_url_as_passive_inventory(tmp_path):
    repository = JobRepository(tmp_path / "registry.db")
    repository.initialize()
    example = repository.upsert_company("Example")
    policy = repository.upsert_company("Policy")
    custom = repository.upsert_company("Custom")
    registry = tmp_path / "registry.csv"
    _write(
        registry,
        [
            _row(example),
            _row(
                policy,
                "Policy",
                career_url="https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=12345678-1234-1234-1234-123456789abc&ccId=19000101_000001&lang=en_US",
            ),
            _row(custom, "Custom", career_url="https://custom.example/careers"),
            _row(custom, "Custom", career_url=""),
        ],
    )

    report = import_career_url_registry(
        repository,
        registry,
        actor="operator@example.org",
        observed_at="2026-08-15T12:00:00+00:00",
    )

    assert report.rows_read == 4
    assert report.rows_without_career_url == 1
    assert report.imported == 3
    assert (report.standard_ats, report.policy_held_ats, report.custom_or_unrecognized) == (1, 1, 1)
    leads = repository.list_source_fingerprints(example)
    assert leads[0]["family"] == "unknown_external"
    evidence = leads[0]["evidence"]
    assert evidence["review_method"] == "user_supplied_career_url_registry"
    assert evidence["activation_allowed"] is False
    assert evidence["registry"]["row_confidence"] == "95"
    assert repository.list_source_candidates(example) == []
    assert repository.source_status() == []
    assert repository.list_source_fingerprints(policy)[0]["family"] == "adp"
    assert repository.list_source_fingerprints(custom)[0]["family"] == "unknown_external"


def test_exact_identity_mismatch_prevents_all_writes(tmp_path):
    repository = JobRepository(tmp_path / "identity.db")
    repository.initialize()
    company_id = repository.upsert_company("Example")
    registry = tmp_path / "registry.csv"
    _write(registry, [_row(company_id, "Wrong")])

    with pytest.raises(ValueError, match="exact company identity mismatch"):
        import_career_url_registry(
            repository,
            registry,
            actor="operator@example.org",
            observed_at="2026-08-15T12:00:00+00:00",
        )
    assert repository.list_source_fingerprints(company_id) == []


def test_invalid_url_prevents_partial_import(tmp_path):
    repository = JobRepository(tmp_path / "url.db")
    repository.initialize()
    first = repository.upsert_company("First")
    second = repository.upsert_company("Second")
    registry = tmp_path / "registry.csv"
    _write(
        registry,
        [_row(first, "First"), _row(second, "Second", career_url="https://localhost/jobs")],
    )

    with pytest.raises(ValueError, match="career_url must be an absolute public"):
        import_career_url_registry(
            repository,
            registry,
            actor="operator@example.org",
            observed_at="2026-08-15T12:00:00+00:00",
        )
    assert repository.list_source_fingerprints(first) == []


def test_http_registry_url_is_retained_as_an_https_only_passive_fingerprint(tmp_path):
    repository = JobRepository(tmp_path / "http.db")
    repository.initialize()
    company_id = repository.upsert_company("Example")
    registry = tmp_path / "registry.csv"
    _write(registry, [_row(company_id, career_url="http://example.com/careers")])

    import_career_url_registry(
        repository,
        registry,
        actor="operator@example.org",
        observed_at="2026-08-15T12:00:00+00:00",
    )

    fingerprint = repository.list_source_fingerprints(company_id)[0]
    assert fingerprint["observed_url"] == "https://example.com/careers"
    assert fingerprint["evidence"]["registry"]["career_url_original"] == "http://example.com/careers"


def test_refresh_preserves_completed_registry_verification(tmp_path):
    repository = JobRepository(tmp_path / "refresh.db")
    repository.initialize()
    company_id = repository.upsert_company("Example")
    registry = tmp_path / "registry.csv"
    _write(registry, [_row(company_id)])

    import_career_url_registry(
        repository,
        registry,
        actor="operator@example.org",
        observed_at="2026-08-15T12:00:00+00:00",
    )
    repository.upsert_source_fingerprint(
        company_id,
        observed_url="https://boards.greenhouse.io/example",
        family="unknown_external",
        evidence={
            "review_method": "user_supplied_career_url_registry",
            "verification_status": "rejected",
            "verification_attempt": {"reason": "identity mismatch"},
        },
        actor="verifier@example.org",
        observed_at="2026-08-15T12:01:00+00:00",
        mark_discovered=False,
    )

    import_career_url_registry(
        repository,
        registry,
        actor="operator@example.org",
        observed_at="2026-08-15T12:02:00+00:00",
    )

    evidence = repository.list_source_fingerprints(company_id)[0]["evidence"]
    assert evidence["verification_status"] == "rejected"
    assert evidence["verification_attempt"] == {"reason": "identity mismatch"}
    assert evidence["registry"]["filename"] == "registry.csv"


def test_cli_exposes_registry_import_command():
    args = parser().parse_args(
        [
            "import-career-url-registry",
            "registry.csv",
            "--actor",
            "operator@example.org",
            "--observed-at",
            "2026-08-15T12:00:00+00:00",
        ]
    )

    assert args.command == "import-career-url-registry"
    assert args.csv == "registry.csv"
