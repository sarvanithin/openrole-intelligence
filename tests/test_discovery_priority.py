from fortune_intel.cli import parser
from fortune_intel.services.discovery_priority import build_discovery_priority_report
from fortune_intel.storage import JobRepository


def add_fact(repository, company_id, *, method, positions=10, confidence=1.0):
    repository.record_sponsorship_fact(
        company_id,
        source="DOL LCA",
        fiscal_year=2026,
        lca_worker_positions=positions,
        entity_match_confidence=confidence,
        source_url="https://dol.example/data",
        source_document="LCA_FY2026.xlsx",
        source_checksum=f"checksum-{company_id}",
        match_method=method,
    )


def test_priority_requires_exact_review_and_is_auditable(tmp_path):
    repository = JobRepository(tmp_path / "priority.db")
    repository.initialize()
    exact_sec = repository.upsert_company("Exact SEC", sec_cik="123", ticker="EXS")
    exact_private = repository.upsert_company("Exact Private")
    provisional_sec = repository.upsert_company("Provisional SEC", sec_cik="456")
    repository.upsert_company("General Company")
    add_fact(repository, exact_sec, method="reviewed_legal_name_domain", positions=50)
    add_fact(repository, exact_private, method="reviewed_exact_legal_name", positions=75)
    add_fact(repository, provisional_sec, method="provisional_normalized_name", positions=5000)

    report = build_discovery_priority_report(repository, batch_size=10)

    assert [target["name"] for target in report["targets"]] == [
        "Exact SEC",
        "Exact Private",
        "Provisional SEC",
        "General Company",
    ]
    assert report["targets"][0]["priority_band"] == "h1b_sec"
    assert report["targets"][0]["h1b_evidence_urls"] == "https://dol.example/data"
    assert report["targets"][1]["priority_band"] == "h1b"
    assert report["targets"][2]["priority_band"] == "sec"
    assert report["targets"][2]["exact_reviewed_h1b"] is False
    assert report["overview"] == {
        "total_targets": 4,
        "exact_reviewed_h1b": 2,
        "sec_identified": 2,
        "h1b_and_sec": 1,
        "websites_missing": 4,
        "by_action": {"acquire_verified_website": 4},
        "by_priority_band": {"h1b_sec": 1, "h1b": 1, "sec": 1, "general": 1},
        "by_coverage": {"unreviewed": 4},
    }
    assert "sec_cik:0000000123" in report["targets"][0]["priority_reasons"]
    assert report["ranking_policy"]["url_policy"].startswith("No URL")


def test_priority_uses_verified_workflow_state_and_excludes_approved_sources(tmp_path):
    repository = JobRepository(tmp_path / "workflow.db")
    repository.initialize()
    candidate_company = repository.upsert_company(
        "Candidate Company", website_url="https://candidate.example"
    )
    repository.upsert_company("Discovery Company", website_url="https://discovery.example")
    covered_company = repository.upsert_company("Covered Company", sec_cik="789")
    repository.upsert_source_candidate(
        candidate_company,
        candidate_url="https://jobs.candidate.example",
        kind="greenhouse",
        confidence=0.95,
        evidence={"method": "official_site_link"},
    )
    repository.upsert_career_source(
        covered_company,
        kind="greenhouse",
        board_token="covered",
        base_url="https://boards.greenhouse.io/covered",
        terms_url="https://covered.example/terms",
        policy_approved_at="2026-08-07T12:00:00+00:00",
        owner_contact="reviewer@example.org",
    )

    report = build_discovery_priority_report(repository, batch_size=1, batch_number=2)

    assert report["overview"]["total_targets"] == 2
    assert report["returned"] == 1
    assert report["targets"][0]["name"] == "Discovery Company"
    assert report["targets"][0]["rank"] == 2
    assert report["targets"][0]["next_action"] == "discover_career_source"
    assert report["overview"]["by_action"] == {
        "discover_career_source": 1,
        "review_source_candidate": 1,
    }


def test_priority_report_validates_batch_bounds(tmp_path):
    repository = JobRepository(tmp_path / "bounds.db")
    repository.initialize()

    for values in ({"batch_size": 0}, {"batch_size": 1001}, {"batch_number": 0}):
        try:
            build_discovery_priority_report(repository, **values)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid batch bounds should fail")


def test_discovery_priority_cli_accepts_batch_coordinates():
    args = parser().parse_args(
        [
            "--database",
            "work.db",
            "discovery-priority",
            "--batch-size",
            "250",
            "--batch-number",
            "3",
        ]
    )

    assert args.command == "discovery-priority"
    assert args.batch_size == 250
    assert args.batch_number == 3


def test_source_discovery_cli_accepts_resumable_coverage_cohorts():
    args = parser().parse_args(
        [
            "--database",
            "work.db",
            "discover-sources",
            "--all",
            "--coverage-status",
            "unsupported",
            "--coverage-status",
            "blocked",
            "--after-company-id",
            "500",
            "--limit",
            "250",
            "--actor",
            "operator@example.test",
        ]
    )

    assert args.coverage_status == ["unsupported", "blocked"]
    assert args.after_company_id == 500
    assert args.limit == 250
