from __future__ import annotations

from fortune_intel.cli import parser
from fortune_intel.services.h1b_exact_linking import bulk_link_exact_h1b_employers
from fortune_intel.storage import JobRepository


def add_employer(
    repository: JobRepository,
    name: str,
    *,
    year: int = 2026,
    source: str = "dol_lca",
    workers: int = 10,
) -> None:
    repository.upsert_h1b_employer(
        name,
        fiscal_year=year,
        lca_worker_positions=workers,
        source=source,
        source_url=f"https://dol.example/{year}",
        source_document=f"FY{year}.csv",
        source_checksum=(source[0] * 64),
        imported_at=f"{year}-08-01T00:00:00+00:00",
    )


def test_dry_run_links_only_exact_latest_year_and_preserves_suffixes(tmp_path):
    repository = JobRepository(tmp_path / "links.db")
    repository.initialize()
    exact_id = repository.upsert_company("Acme, Inc.")
    repository.upsert_company("Suffix Company LLC")
    add_employer(repository, "ACME INC", year=2025, workers=99)
    add_employer(repository, "ACME INC", year=2026, workers=12)
    add_employer(repository, "Suffix Company Inc", year=2026)

    report = bulk_link_exact_h1b_employers(repository, dry_run=True)

    assert report["latest_fiscal_year"] == 2026
    assert report["exact_one_to_one_matches"] == 1
    assert report["would_link"] == 1
    assert report["facts_written"] == 0
    assert report["links"][0]["company_id"] == exact_id
    assert report["links"][0]["lca_worker_positions"] == 12
    assert repository.get_sponsorship_fact(exact_id, source="dol_lca", fiscal_year=2026) is None


def test_company_and_employer_normalization_collisions_are_quarantined(tmp_path):
    repository = JobRepository(tmp_path / "ambiguous.db")
    repository.initialize()
    company_id = repository.upsert_company("Collision Corp")
    with repository.connect() as connection:
        connection.execute(
            """INSERT INTO companies (
                slug, name, normalized_name, career_url, ats_type, is_synthetic,
                created_at, updated_at
            ) VALUES ('collision-corp-duplicate', 'Collision Corp.', 'collision corp', '', '', 0,
                '2026-08-01T00:00:00+00:00', '2026-08-01T00:00:00+00:00')"""
        )
    add_employer(repository, "Collision Corp")
    sole_id = repository.upsert_company("Employer Collision LLC")
    add_employer(repository, "Employer Collision LLC", source="dol_lca")
    add_employer(repository, "Employer Collision LLC", source="other_official")

    report = bulk_link_exact_h1b_employers(repository)

    assert report["exact_one_to_one_matches"] == 0
    assert report["ambiguous_normalized_names"] == 2
    assert report["facts_written"] == 0
    assert repository.get_sponsorship_fact(company_id, source="dol_lca", fiscal_year=2026) is None
    assert repository.get_sponsorship_fact(sole_id, source="dol_lca", fiscal_year=2026) is None


def test_live_link_is_idempotent_and_retains_source_provenance(tmp_path):
    repository = JobRepository(tmp_path / "idempotent.db")
    repository.initialize()
    company_id = repository.upsert_company("Exact Legal Employer LLC")
    add_employer(repository, "EXACT LEGAL EMPLOYER, LLC", workers=37)

    first = bulk_link_exact_h1b_employers(repository)
    second = bulk_link_exact_h1b_employers(repository)

    assert first["facts_written"] == 1
    assert second["facts_written"] == 0
    assert second["already_linked"] == 1
    fact = repository.get_sponsorship_fact(company_id, source="dol_lca", fiscal_year=2026)
    assert fact["match_method"] == "reviewed_exact_legal_name"
    assert fact["entity_match_confidence"] == 1.0
    assert fact["lca_worker_positions"] == 37
    assert fact["source_url"] == "https://dol.example/2026"
    assert fact["source_document"] == "FY2026.csv"
    assert fact["source_checksum"] == "d" * 64
    with repository.connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM sponsorship_facts").fetchone()[0]
    assert count == 1


def test_existing_manually_reviewed_fact_is_not_overwritten(tmp_path):
    repository = JobRepository(tmp_path / "conflict.db")
    repository.initialize()
    company_id = repository.upsert_company("Manual Review Inc")
    add_employer(repository, "Manual Review Inc")
    repository.record_sponsorship_fact(
        company_id,
        source="dol_lca",
        fiscal_year=2026,
        lca_worker_positions=5,
        entity_match_confidence=1.0,
        source_url="https://review.example/evidence",
        match_method="reviewed_legal_name_domain",
    )

    report = bulk_link_exact_h1b_employers(repository)

    assert report["existing_reviewed_conflicts"] == 1
    assert report["facts_written"] == 0
    assert report["existing_reviewed_conflict_examples"][0]["company_id"] == company_id
    fact = repository.get_sponsorship_fact(company_id, source="dol_lca", fiscal_year=2026)
    assert fact["match_method"] == "reviewed_legal_name_domain"
    assert fact["source_url"] == "https://review.example/evidence"


def test_bulk_cli_is_dry_run_first_and_bounds_report_output():
    arguments = parser().parse_args(["bulk-link-exact-h1b", "--dry-run", "--report-limit", "12"])

    assert arguments.command == "bulk-link-exact-h1b"
    assert arguments.dry_run is True
    assert arguments.report_limit == 12
