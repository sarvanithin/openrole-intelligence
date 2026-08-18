from fortune_intel.services import bulk_source_approval
from fortune_intel.storage import JobRepository


def _repository(tmp_path):
    repository = JobRepository(tmp_path / "jobs.db")
    repository.initialize()
    return repository


def test_bulk_approval_activates_successes_and_preserves_probe_failures(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    company_id = repository.upsert_company("Example")
    first = repository.upsert_source_candidate(
        company_id,
        candidate_url="https://boards.greenhouse.io/example",
        kind="greenhouse",
        confidence=1,
        evidence={},
    )
    second = repository.upsert_source_candidate(
        company_id,
        candidate_url="https://boards.greenhouse.io/broken",
        kind="greenhouse",
        confidence=0.9,
        evidence={},
    )

    def fake_approve(_repository, candidate_id, **_kwargs):
        if candidate_id == second:
            raise ValueError("incomplete manifest")
        return 42

    monkeypatch.setattr(bulk_source_approval, "approve_source_candidate", fake_approve)
    report = bulk_source_approval.approve_discovered_sources(
        repository,
        policy_urls={"greenhouse": "https://developers.greenhouse.io/job-board"},
        policy_approved_at="2026-08-07T00:00:00+00:00",
        actor="operator",
        concurrency=2,
    )

    assert report["candidates_selected"] == 2
    assert report["activated"] == 1
    assert report["empty_pending_verification"] == 0
    assert report["probe_failed"] == 1
    assert report["results"] == [
        {"candidate_id": first, "kind": "greenhouse", "status": "activated", "source_id": 42},
        {
            "candidate_id": second,
            "kind": "greenhouse",
            "status": "probe_failed",
            "error": "incomplete manifest",
        },
    ]


def test_bulk_approval_distinguishes_pending_empty_verification(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    company_id = repository.upsert_company("Example")
    candidate_id = repository.upsert_source_candidate(
        company_id,
        candidate_url="https://boards.greenhouse.io/example",
        kind="greenhouse",
        confidence=1,
        evidence={},
    )
    monkeypatch.setattr(
        bulk_source_approval,
        "approve_source_candidate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            bulk_source_approval.CompleteEmptyObservationPending(1)
        ),
    )

    report = bulk_source_approval.approve_discovered_sources(
        repository,
        policy_urls={"greenhouse": "https://developers.greenhouse.io/job-board"},
        policy_approved_at="2026-08-07T00:00:00+00:00",
        actor="operator",
    )

    assert report["activated"] == 0
    assert report["probe_failed"] == 0
    assert report["empty_pending_verification"] == 1
    assert report["results"][0]["candidate_id"] == candidate_id


def test_bulk_approval_requires_explicit_supported_policy(tmp_path):
    repository = _repository(tmp_path)
    try:
        bulk_source_approval.approve_discovered_sources(
            repository,
            policy_urls={"unknown": "https://example.com/terms"},
            policy_approved_at="2026-08-07T00:00:00+00:00",
            actor="operator",
        )
    except ValueError as error:
        assert "unsupported policy" in str(error)
    else:
        raise AssertionError("expected unsupported policy to be rejected")


def test_bulk_approval_filters_policy_kinds_before_limit(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    company_id = repository.upsert_company("Example")
    for index in range(3):
        repository.upsert_source_candidate(
            company_id,
            candidate_url=f"https://example.oraclecloud.com/hcmUI/CandidateExperience/{index}",
            kind="oracle_recruiting",
            confidence=1,
            evidence={},
        )
    eligible = repository.upsert_source_candidate(
        company_id,
        candidate_url="https://boards.greenhouse.io/example",
        kind="greenhouse",
        confidence=0.5,
        evidence={},
    )

    monkeypatch.setattr(
        bulk_source_approval,
        "approve_source_candidate",
        lambda *_args, **_kwargs: 99,
    )
    report = bulk_source_approval.approve_discovered_sources(
        repository,
        policy_urls={"greenhouse": "https://developers.greenhouse.io/job-board"},
        policy_approved_at="2026-08-07T00:00:00+00:00",
        actor="operator",
        limit=1,
    )

    assert report["candidates_selected"] == 1
    assert report["results"][0]["candidate_id"] == eligible


def test_bulk_approval_can_be_restricted_to_exact_candidate_ids(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    company_id = repository.upsert_company("Example")
    first = repository.upsert_source_candidate(
        company_id,
        candidate_url="https://boards.greenhouse.io/first",
        kind="greenhouse",
        confidence=1,
        evidence={},
    )
    second = repository.upsert_source_candidate(
        company_id,
        candidate_url="https://boards.greenhouse.io/second",
        kind="greenhouse",
        confidence=1,
        evidence={},
    )
    monkeypatch.setattr(bulk_source_approval, "approve_source_candidate", lambda *a, **k: 42)

    report = bulk_source_approval.approve_discovered_sources(
        repository,
        policy_urls={"greenhouse": "https://developers.greenhouse.io/job-board"},
        policy_approved_at="2026-08-11T17:00:00+00:00",
        actor="operator",
        candidate_ids={second},
    )

    assert report["candidates_selected"] == 1
    assert report["results"][0]["candidate_id"] == second
    assert report["results"][0]["candidate_id"] != first
