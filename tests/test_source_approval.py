from datetime import datetime, timedelta

import pytest

from fortune_intel.connectors.models import ConnectorJob, ConnectorResult
from fortune_intel.services import source_approval
from fortune_intel.storage import JobRepository


class StubConnector:
    def __init__(self, result):
        self.result = result

    def fetch(self):
        return self.result


def candidate(repository):
    company_id = repository.upsert_company("Example")
    candidate_id = repository.upsert_source_candidate(
        company_id,
        candidate_url="https://boards.greenhouse.io/example",
        kind="greenhouse",
        confidence=0.99,
        evidence={"board_token": "example"},
        robots_status="allowed",
        terms_status="review_required",
    )
    return company_id, candidate_id


UKG_BOARD = (
    "https://recruiting2.ultipro.com/ARC1026ARCOI/JobBoard/2af23579-6cf8-4926-be1a-3bc74872c197"
)


def test_ukg_approval_requires_primary_company_provenance(monkeypatch, tmp_path):
    repository = JobRepository(tmp_path / "ukg-no-provenance.db")
    repository.initialize()
    company_id = repository.upsert_company("Example")
    candidate_id = repository.upsert_source_candidate(
        company_id,
        candidate_url=UKG_BOARD,
        kind="ukg_recruiting_public",
        confidence=1,
        evidence={"board_token": "unverified lead"},
        terms_status="review_required",
    )
    monkeypatch.setattr(
        source_approval,
        "build_connector",
        lambda *_: (_ for _ in ()).throw(AssertionError("connector must not run")),
    )

    with pytest.raises(ValueError, match="official company provenance"):
        source_approval.approve_source_candidate(
            repository,
            candidate_id,
            terms_url="https://example.com/written-authorization",
            policy_approved_at="2026-08-12T12:00:00+00:00",
            actor="reviewer@example.org",
        )
    assert repository.source_status() == []


def test_official_structured_approval_requires_primary_provenance_and_robots(monkeypatch, tmp_path):
    repository = JobRepository(tmp_path / "structured-policy.db")
    repository.initialize()
    company_id = repository.upsert_company("Example")
    candidate_id = repository.upsert_source_candidate(
        company_id,
        candidate_url="https://careers.example.com/job-sitemap.xml",
        kind="official_structured",
        confidence=0.95,
        evidence={},
        terms_status="review_required",
    )
    monkeypatch.setattr(
        source_approval,
        "build_connector",
        lambda *_: (_ for _ in ()).throw(AssertionError("connector must not run")),
    )
    arguments = {
        "terms_url": "https://example.com/terms",
        "policy_approved_at": "2026-08-13T12:00:00+00:00",
        "actor": "reviewer@example.org",
    }

    with pytest.raises(ValueError, match="official company provenance"):
        source_approval.approve_source_candidate(repository, candidate_id, **arguments)

    repository.upsert_source_candidate(
        company_id,
        candidate_url="https://careers.example.com/job-sitemap.xml",
        kind="official_structured",
        confidence=0.95,
        evidence={
            "review_method": "primary_source_exact_ats_url",
            "source_url": "https://example.com/careers",
        },
        terms_status="review_required",
    )
    with pytest.raises(ValueError, match="explicit allowed robots review"):
        source_approval.approve_source_candidate(repository, candidate_id, **arguments)


def test_ukg_approval_accepts_explicit_primary_source_review(monkeypatch, tmp_path):
    repository = JobRepository(tmp_path / "ukg-primary-review.db")
    repository.initialize()
    company_id = repository.upsert_company("Example")
    candidate_id = repository.upsert_source_candidate(
        company_id,
        candidate_url=UKG_BOARD,
        kind="ukg_recruiting_public",
        confidence=1,
        evidence={
            "review_method": "primary_source_exact_ats_url",
            "source_url": "https://example.com/careers",
        },
        terms_status="review_required",
    )
    result = ConnectorResult(
        source="ukg_recruiting_public",
        source_key="exact-board",
        jobs=(
            ConnectorJob(
                "ukg_recruiting_public",
                "78ec5a6e-56b4-44a2-9dba-b3563ee71b89",
                "Engineer",
                f"{UKG_BOARD}/OpportunityDetail?opportunityId=78ec5a6e-56b4-44a2-9dba-b3563ee71b89",
                location="Austin, TX, USA",
                source_opened_at="2026-08-12T12:00:00+00:00",
            ),
        ),
        complete=True,
    )
    monkeypatch.setattr(source_approval, "build_connector", lambda *_: StubConnector(result))

    source_id = source_approval.approve_source_candidate(
        repository,
        candidate_id,
        terms_url="https://example.com/written-authorization",
        policy_approved_at="2026-08-12T12:00:00+00:00",
        actor="reviewer@example.org",
    )

    assert source_id
    assert repository.source_status()[0]["kind"] == "ukg_recruiting_public"


def test_icims_approval_requires_allowed_robots_review(monkeypatch, tmp_path):
    repository = JobRepository(tmp_path / "icims-robots.db")
    repository.initialize()
    company_id = repository.upsert_company("Example")
    candidate_id = repository.upsert_source_candidate(
        company_id,
        candidate_url="https://careers-example.icims.com/jobs/search",
        kind="icims_public",
        confidence=1,
        evidence={
            "review_method": "primary_source_exact_ats_url",
            "source_url": "https://example.com/careers",
        },
        robots_status="unknown",
        terms_status="review_required",
    )
    monkeypatch.setattr(
        source_approval,
        "build_connector",
        lambda *_: (_ for _ in ()).throw(AssertionError("connector must not run")),
    )

    with pytest.raises(ValueError, match="allowed robots review"):
        source_approval.approve_source_candidate(
            repository,
            candidate_id,
            terms_url="https://example.com/written-authorization",
            policy_approved_at="2026-08-12T12:00:00+00:00",
            actor="reviewer@example.org",
        )


def test_approval_ingests_probe_without_second_fetch_and_records_success(monkeypatch, tmp_path):
    repository = JobRepository(tmp_path / "approval.db")
    repository.initialize()
    company_id, candidate_id = candidate(repository)
    result = ConnectorResult(
        source="greenhouse",
        source_key="example",
        jobs=(
            ConnectorJob(
                "greenhouse",
                "1",
                "Engineer",
                "https://example.test/1",
                location="Austin, TX",
                source_opened_at="2026-08-01T09:30:00+00:00",
            ),
        ),
        complete=True,
    )
    connector = StubConnector(result)
    connector.fetch_count = 0
    original_fetch = connector.fetch

    def counted_fetch():
        connector.fetch_count += 1
        return original_fetch()

    connector.fetch = counted_fetch
    monkeypatch.setattr(source_approval, "build_connector", lambda *_: connector)

    source_id = source_approval.approve_source_candidate(
        repository,
        candidate_id,
        terms_url="https://example.com/terms",
        policy_approved_at="2026-08-06T12:00:00+00:00",
        actor="reviewer@example.org",
    )

    assert source_id
    assert repository.source_status()[0]["sync_interval_minutes"] == 60
    stored = repository.get_source_candidate(candidate_id)
    assert stored["status"] == "approved"
    assert stored["terms_status"] == "permitted"
    assert repository.get_company_coverage(company_id)["disposition"] == "supported"
    assert connector.fetch_count == 1
    assert repository.due_career_sources() == []
    persisted = repository.list_jobs(us_eligibility="eligible")[0]
    assert persisted["source_opened_at"] == "2026-08-01T09:30:00+00:00"
    assert persisted["date_provenance"] == "source_opened_at"
    assert persisted["first_seen_at"] != persisted["source_opened_at"]
    status = repository.source_status()[0]
    assert datetime.fromisoformat(status["next_sync_at"]) - datetime.fromisoformat(
        status["last_success_at"]
    ) == timedelta(minutes=60)
    with repository.connect() as connection:
        run = connection.execute("SELECT * FROM sync_runs").fetchone()
    assert run["status"] == "success"
    assert run["complete"] == 1
    assert run["jobs_seen"] == 1
    audit = repository.company_coverage_audit()[0]
    assert audit["complete_manifest_approved"] is True
    assert audit["successful_platform_ingestion"] is True
    assert audit["fresh"] is True


def test_incomplete_probe_cannot_enable_source(monkeypatch, tmp_path):
    repository = JobRepository(tmp_path / "rejected-probe.db")
    repository.initialize()
    _, candidate_id = candidate(repository)
    result = ConnectorResult(source="greenhouse", source_key="example", jobs=(), complete=False)
    monkeypatch.setattr(source_approval, "build_connector", lambda *_: StubConnector(result))

    with pytest.raises(ValueError, match="did not return a complete manifest"):
        source_approval.approve_source_candidate(
            repository,
            candidate_id,
            terms_url="https://example.com/terms",
            policy_approved_at="2026-08-06T12:00:00+00:00",
            actor="reviewer@example.org",
        )
    assert repository.source_status() == []


def test_two_complete_empty_probes_can_approve_zero_opening_board(monkeypatch, tmp_path):
    repository = JobRepository(tmp_path / "empty-approval.db")
    repository.initialize()
    company_id, candidate_id = candidate(repository)
    result = ConnectorResult(source="greenhouse", source_key="example", jobs=(), complete=True)
    monkeypatch.setattr(source_approval, "build_connector", lambda *_: StubConnector(result))
    arguments = {
        "terms_url": "https://example.com/terms",
        "policy_approved_at": "2026-08-06T12:00:00+00:00",
        "actor": "reviewer@example.org",
    }

    with pytest.raises(source_approval.CompleteEmptyObservationPending, match="1 of 2"):
        source_approval.approve_source_candidate(repository, candidate_id, **arguments)

    first = repository.get_source_candidate(candidate_id)
    assert first["status"] == "discovered"
    assert first["consecutive_complete_empty_observations"] == 1
    assert repository.source_status() == []

    source_id = source_approval.approve_source_candidate(repository, candidate_id, **arguments)

    approved = repository.get_source_candidate(candidate_id)
    assert source_id
    assert approved["status"] == "approved"
    assert approved["consecutive_complete_empty_observations"] == 2
    assert "zero-opening board" in approved["review_notes"]
    assert repository.source_status()[0]["consecutive_complete_empty_observations"] == 0
    assert repository.get_company_coverage(company_id)["disposition"] == "supported"
    assert repository.due_career_sources() == []
    with repository.connect() as connection:
        run = connection.execute("SELECT complete, jobs_seen FROM sync_runs").fetchone()
    assert tuple(run) == (1, 0)


def test_incomplete_probe_preserves_empty_streak_and_nonempty_resets_it(monkeypatch, tmp_path):
    repository = JobRepository(tmp_path / "approval-reset.db")
    repository.initialize()
    _, candidate_id = candidate(repository)
    results = iter(
        (
            ConnectorResult("greenhouse", "example", (), complete=True),
            ConnectorResult("greenhouse", "example", (), complete=False),
            ConnectorResult(
                "greenhouse",
                "example",
                (ConnectorJob("greenhouse", "1", "Engineer", "https://example.test/1"),),
                complete=True,
            ),
        )
    )
    monkeypatch.setattr(
        source_approval,
        "build_connector",
        lambda *_: StubConnector(next(results)),
    )
    arguments = {
        "terms_url": "https://example.com/terms",
        "policy_approved_at": "2026-08-06T12:00:00+00:00",
        "actor": "reviewer@example.org",
    }

    with pytest.raises(source_approval.CompleteEmptyObservationPending):
        source_approval.approve_source_candidate(repository, candidate_id, **arguments)
    with pytest.raises(ValueError, match="did not return a complete manifest"):
        source_approval.approve_source_candidate(repository, candidate_id, **arguments)
    assert (
        repository.get_source_candidate(candidate_id)["consecutive_complete_empty_observations"]
        == 1
    )

    source_approval.approve_source_candidate(repository, candidate_id, **arguments)
    assert (
        repository.get_source_candidate(candidate_id)["consecutive_complete_empty_observations"]
        == 0
    )


def test_workday_candidate_approval_uses_exact_discovered_source_key(monkeypatch, tmp_path):
    repository = JobRepository(tmp_path / "workday-approval.db")
    repository.initialize()
    company_id = repository.upsert_company("Example")
    candidate_id = repository.upsert_source_candidate(
        company_id,
        candidate_url="https://acme.wd5.myworkdayjobs.com/External/job/US-NY/Role_JR-1",
        kind="workday",
        confidence=0.99,
        evidence={"origin": "verified company careers link"},
        robots_status="allowed",
        terms_status="review_required",
    )
    calls = []
    result = ConnectorResult(
        source="workday",
        source_key="acme.wd5.myworkdayjobs.com|acme|External",
        jobs=(ConnectorJob("workday", "opaque-1", "Engineer", "https://example.test/1"),),
        complete=True,
    )

    def connector(kind, source_key):
        calls.append((kind, source_key))
        return StubConnector(result)

    monkeypatch.setattr(source_approval, "build_connector", connector)

    source_approval.approve_source_candidate(
        repository,
        candidate_id,
        terms_url="https://example.com/terms",
        policy_approved_at="2026-08-07T12:00:00+00:00",
        actor="reviewer@example.org",
    )

    assert calls == [("workday", "acme.wd5.myworkdayjobs.com|acme|External")]
    source = repository.source_status()[0]
    assert source["base_url"] == "https://acme.wd5.myworkdayjobs.com/External"


def test_recruiting_path_workday_approval_preserves_exact_host_tenant_and_site(
    monkeypatch, tmp_path
):
    repository = JobRepository(tmp_path / "workdaysite-approval.db")
    repository.initialize()
    company_id = repository.upsert_company("Example")
    candidate_id = repository.upsert_source_candidate(
        company_id,
        candidate_url=("https://wd1.myworkdaysite.com/recruiting/snapchat/snap/job/US-CA/Role_R1"),
        kind="workday",
        confidence=0.99,
        evidence={"origin": "verified company careers link"},
        robots_status="allowed",
        terms_status="review_required",
    )
    calls = []
    result = ConnectorResult(
        source="workday",
        source_key="wd1.myworkdaysite.com|snapchat|snap",
        jobs=(ConnectorJob("workday", "opaque-1", "Engineer", "https://example.test/1"),),
        complete=True,
    )

    def connector(kind, source_key):
        calls.append((kind, source_key))
        return StubConnector(result)

    monkeypatch.setattr(source_approval, "build_connector", connector)

    source_approval.approve_source_candidate(
        repository,
        candidate_id,
        terms_url="https://example.com/terms",
        policy_approved_at="2026-08-11T12:00:00+00:00",
        actor="reviewer@example.org",
    )

    assert calls == [("workday", "wd1.myworkdaysite.com|snapchat|snap")]
    source = repository.source_status()[0]
    assert source["base_url"] == ("https://wd1.myworkdaysite.com/recruiting/snapchat/snap")


def test_initial_manifest_failure_rolls_back_source_jobs_policy_and_success_state(
    monkeypatch, tmp_path
):
    repository = JobRepository(tmp_path / "approval-rollback.db")
    repository.initialize()
    company_id, candidate_id = candidate(repository)
    result = ConnectorResult(
        "greenhouse",
        "example",
        (
            ConnectorJob("greenhouse", "1", "One", "https://example.test/1"),
            ConnectorJob("greenhouse", "2", "Two", "https://example.test/2"),
        ),
        complete=True,
    )
    monkeypatch.setattr(source_approval, "build_connector", lambda *_: StubConnector(result))
    original_upsert = repository._upsert_job_with_connection
    calls = 0

    def fail_second_job(connection, company, job, assessment, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated initial ingestion failure")
        return original_upsert(connection, company, job, assessment, **kwargs)

    monkeypatch.setattr(repository, "_upsert_job_with_connection", fail_second_job)

    with pytest.raises(RuntimeError, match="simulated initial ingestion failure"):
        source_approval.approve_source_candidate(
            repository,
            candidate_id,
            terms_url="https://example.com/terms",
            policy_approved_at="2026-08-06T12:00:00+00:00",
            actor="reviewer@example.org",
        )

    candidate_state = repository.get_source_candidate(candidate_id)
    assert candidate_state["status"] == "discovered"
    assert candidate_state["terms_status"] == "review_required"
    assert repository.source_status() == []
    assert repository.list_jobs() == []
    assert repository.get_company_coverage(company_id)["disposition"] != "supported"
    with repository.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM sync_runs").fetchone()[0] == 0


def test_initial_approval_manifest_classifies_us_and_non_us_jobs(monkeypatch, tmp_path):
    repository = JobRepository(tmp_path / "approval-geography.db")
    repository.initialize()
    _, candidate_id = candidate(repository)
    result = ConnectorResult(
        "greenhouse",
        "example",
        (
            ConnectorJob(
                "greenhouse", "us", "US role", "https://example.test/us", location="New York, NY"
            ),
            ConnectorJob(
                "greenhouse",
                "ca",
                "Canada role",
                "https://example.test/ca",
                location="Toronto, Ontario, Canada",
            ),
        ),
        complete=True,
    )
    monkeypatch.setattr(source_approval, "build_connector", lambda *_: StubConnector(result))

    source_approval.approve_source_candidate(
        repository,
        candidate_id,
        terms_url="https://example.com/terms",
        policy_approved_at="2026-08-06T12:00:00+00:00",
        actor="reviewer@example.org",
    )

    assert repository.count_jobs() == 2
    assert repository.count_jobs(us_eligibility="eligible") == 1
    assert repository.count_jobs(us_eligibility="ineligible") == 1
