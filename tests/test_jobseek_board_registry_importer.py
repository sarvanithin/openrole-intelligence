import csv
import json

import pytest

from fortune_intel.cli import parser
from fortune_intel.importers.jobseek_board_registry import (
    JOBSEEK_REPOSITORY_URL,
    import_jobseek_board_registry,
)
from fortune_intel.storage import JobRepository


REVISION = "3d4ae9baff7ec615f19c898eb098079e56838fc6"


def _write(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _registry(tmp_path, boards):
    companies = tmp_path / "companies.csv"
    _write(
        companies,
        ("slug", "name", "website"),
        [
            {"slug": "example", "name": "Example", "website": "https://example.test"},
            {"slug": "unmatched", "name": "Not In OpenRole", "website": "https://missing.test"},
        ],
    )
    registry = tmp_path / "boards.csv"
    _write(
        registry,
        (
            "company_slug",
            "board_slug",
            "board_url",
            "monitor_type",
            "monitor_config",
            "scraper_type",
            "scraper_config",
        ),
        boards,
    )
    return registry, companies


def _import(repository, registry, companies):
    return import_jobseek_board_registry(
        repository,
        boards_csv=registry,
        companies_csv=companies,
        source_revision=REVISION,
        retrieved_at="2026-08-16T17:00:00+00:00",
        actor="operator@example.org",
        permission_basis="Direct permission from the upstream owner, recorded by operator",
    )


def test_imports_only_canonical_existing_company_boards_as_passive_attributed_inventory(tmp_path):
    repository = JobRepository(tmp_path / "jobseek.db")
    repository.initialize()
    company_id = repository.upsert_company("Example")
    registry, companies = _registry(
        tmp_path,
        [
            {
                "company_slug": "example",
                "board_slug": "example-greenhouse",
                "board_url": "https://job-boards.greenhouse.io/example",
                "monitor_type": "greenhouse",
                "monitor_config": json.dumps({"token": "example"}),
                "scraper_type": "skip",
                "scraper_config": "",
            },
            {
                "company_slug": "example",
                "board_slug": "example-workday",
                "board_url": "https://example.test/careers",
                "monitor_type": "workday",
                "monitor_config": json.dumps(
                    {"company": "example", "wd_instance": "wd5", "site": "External"}
                ),
                "scraper_type": "workday",
                "scraper_config": "",
            },
            {
                "company_slug": "example",
                "board_slug": "example-eightfold",
                "board_url": "https://example.eightfold.ai/careers",
                "monitor_type": "eightfold",
                "monitor_config": "",
                "scraper_type": "eightfold",
                "scraper_config": "",
            },
            {
                "company_slug": "example",
                "board_slug": "example-unsupported",
                "board_url": "https://careers.example.test/jobs",
                "monitor_type": "dom",
                "monitor_config": "",
                "scraper_type": "dom",
                "scraper_config": "",
            },
            {
                "company_slug": "unmatched",
                "board_slug": "unmatched-greenhouse",
                "board_url": "https://boards.greenhouse.io/unmatched",
                "monitor_type": "greenhouse",
                "monitor_config": "",
                "scraper_type": "skip",
                "scraper_config": "",
            },
        ],
    )

    report = _import(repository, registry, companies)

    assert report.rows_read == 5
    assert report.imported == 3
    assert report.policy_held == 1
    assert report.unsupported == 1
    assert report.unmatched_companies == 1
    assert repository.source_status() == []
    assert repository.list_source_candidates(company_id) == []
    fingerprints = repository.list_source_fingerprints(company_id)
    assert len(fingerprints) == 3
    greenhouse = next(
        item
        for item in fingerprints
        if item["observed_url"] == "https://job-boards.greenhouse.io/example"
    )
    evidence = greenhouse["evidence"]
    assert evidence["review_method"] == "jobseek_board_registry"
    assert evidence["verification_status"] == "unverified"
    assert evidence["activation_allowed"] is False
    assert evidence["primary_source_verification_required"] is True
    assert evidence["proposed_kind"] == "greenhouse"
    assert evidence["source"]["repository_url"] == JOBSEEK_REPOSITORY_URL
    assert evidence["source"]["source_revision"] == REVISION
    assert evidence["source"]["permission_basis"].startswith("Direct permission")
    workday = next(item for item in fingerprints if item["evidence"]["proposed_kind"] == "workday")
    assert workday["evidence"]["normalized_base_url_lead"] == (
        "https://example.wd5.myworkdayjobs.com/External"
    )
    eightfold = next(
        item for item in fingerprints if item["evidence"]["proposed_kind"] == "eightfold"
    )
    assert eightfold["evidence"]["policy_held"] is True
    assert repository.get_company_coverage(company_id)["disposition"] == "unreviewed"


def test_rejects_invalid_registry_rows_before_writing_any_inventory(tmp_path):
    repository = JobRepository(tmp_path / "atomic.db")
    repository.initialize()
    company_id = repository.upsert_company("Example")
    registry, companies = _registry(
        tmp_path,
        [
            {
                "company_slug": "example",
                "board_slug": "first",
                "board_url": "https://boards.greenhouse.io/example",
                "monitor_type": "greenhouse",
                "monitor_config": "",
                "scraper_type": "skip",
                "scraper_config": "",
            },
            {
                "company_slug": "example",
                "board_slug": "invalid-json",
                "board_url": "https://jobs.ashbyhq.com/example",
                "monitor_type": "ashby",
                "monitor_config": "{not-json}",
                "scraper_type": "skip",
                "scraper_config": "",
            },
        ],
    )

    with pytest.raises(ValueError, match="invalid monitor_config JSON"):
        _import(repository, registry, companies)
    assert repository.list_source_fingerprints(company_id) == []
    assert repository.source_status() == []


def test_cli_requires_immutable_revision_and_explicit_permission_context():
    args = parser().parse_args(
        [
            "import-jobseek-board-registry",
            "boards.csv",
            "companies.csv",
            "--source-revision",
            REVISION,
            "--retrieved-at",
            "2026-08-16T17:00:00+00:00",
            "--actor",
            "operator@example.org",
            "--permission-basis",
            "upstream owner permission",
        ]
    )

    assert args.command == "import-jobseek-board-registry"
    assert args.source_revision == REVISION
    assert args.permission_basis == "upstream owner permission"
