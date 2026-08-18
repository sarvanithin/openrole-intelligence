from __future__ import annotations

from fortune_intel import cli_fingerprints
from fortune_intel.cli import parser
from fortune_intel.services.fingerprint_reclassification import (
    reclassify_passive_fingerprints,
)
from fortune_intel.storage import JobRepository

PAYCOM_URL = (
    "https://www.paycomonline.net/v4/ats/web.php/jobs?clientkey=434D8E11B53DE940A5456677337F30F5"
)
SAPSF_URL = "https://career41.sapsf.com/careers?company=QUANTUMP"
INVALID_URL = "https://ats.rippling.com/acme/jobs/"


def _repository(tmp_path) -> tuple[JobRepository, int]:
    repository = JobRepository(tmp_path / "fingerprints.db")
    repository.initialize()
    return repository, repository.upsert_company("Example")


def _observe(
    repository: JobRepository,
    company_id: int,
    url: str,
    family: str,
    actor: str,
    at: str,
) -> None:
    repository.upsert_source_fingerprint(
        company_id,
        observed_url=url,
        family=family,
        evidence={"actor_evidence": actor},
        actor=actor,
        observed_at=at,
    )


def test_reclassification_merges_without_losing_provenance_or_creating_sources(tmp_path) -> None:
    repository, company_id = _repository(tmp_path)
    _observe(
        repository,
        company_id,
        PAYCOM_URL,
        "unknown_external",
        "unknown-1",
        "2026-01-01T00:00:00+00:00",
    )
    _observe(
        repository,
        company_id,
        PAYCOM_URL,
        "unknown_external",
        "unknown-2",
        "2026-01-02T00:00:00+00:00",
    )
    for day in (3, 4, 5):
        _observe(
            repository,
            company_id,
            PAYCOM_URL,
            "paycom",
            f"known-{day}",
            f"2026-01-0{day}T00:00:00+00:00",
        )
    _observe(
        repository, company_id, SAPSF_URL, "unknown_external", "sap", "2026-01-06T00:00:00+00:00"
    )
    _observe(
        repository,
        company_id,
        INVALID_URL,
        "unknown_external",
        "invalid",
        "2026-01-07T00:00:00+00:00",
    )

    report = reclassify_passive_fingerprints(repository, actor="schema-v10-test")

    assert report == {
        "scanned": 3,
        "reclassified": 2,
        "merged": 1,
        "unchanged": 1,
        "by_family": {"paycom": 1, "successfactors": 1},
        "dry_run": False,
    }
    fingerprints = repository.list_source_fingerprints(company_id)
    paycom = next(row for row in fingerprints if row["family"] == "paycom")
    assert paycom["observation_count"] == 5
    assert paycom["first_seen_at"] == "2026-01-01T00:00:00+00:00"
    assert paycom["last_seen_at"] == "2026-01-05T00:00:00+00:00"
    assert paycom["last_observed_by"] == "known-5"
    assert paycom["evidence"]["original_evidence"] == {"actor_evidence": "unknown-2"}
    assert paycom["evidence"]["existing_target_evidence"] == {"actor_evidence": "known-5"}
    assert paycom["evidence"]["reclassification"]["actor"] == "schema-v10-test"
    assert {row["family"] for row in fingerprints} == {
        "paycom",
        "successfactors",
        "unknown_external",
    }
    with repository.connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM career_source_candidates").fetchone()[0] == 0
        )
        assert connection.execute("SELECT COUNT(*) FROM career_sources").fetchone()[0] == 0

    second = reclassify_passive_fingerprints(repository, actor="schema-v10-test")
    assert second["reclassified"] == second["merged"] == 0
    assert second["scanned"] == second["unchanged"] == 1
    assert (
        next(
            row
            for row in repository.list_source_fingerprints(company_id)
            if row["family"] == "paycom"
        )["observation_count"]
        == 5
    )


def test_cli_dry_run_reports_matches_without_mutating_rows(tmp_path) -> None:
    repository, company_id = _repository(tmp_path)
    _observe(
        repository, company_id, SAPSF_URL, "unknown_external", "sap", "2026-01-01T00:00:00+00:00"
    )
    args = parser().parse_args(
        ["reclassify-source-fingerprints", "--actor", "operator", "--dry-run"]
    )

    report = cli_fingerprints.run_fingerprint_command(args, repository)

    assert report["dry_run"] is True
    assert report["by_family"] == {"successfactors": 1}
    assert repository.list_source_fingerprints(company_id)[0]["family"] == "unknown_external"
