from __future__ import annotations

from fortune_intel.cli import parser
from fortune_intel.discovery import AtsSourceCandidate, DiscoveryReport, PassiveSourceFingerprint
from fortune_intel.services.discovery_pipeline import discover_company_sources
from fortune_intel.services.licensed_lead_verification import LeadPage
from fortune_intel.services.registry_career_portal_runner import (
    run_registry_career_portal_verifier,
)
from fortune_intel.services.registry_career_portal_verification import (
    promote_verified_registry_career_portals,
)
from fortune_intel.storage import JobRepository


def test_runner_drains_durable_batches_and_never_activates(tmp_path):
    repository = JobRepository(tmp_path / "runner.db")
    repository.initialize()
    reports = iter(
        [
            {"scanned": 2, "verified": 1, "rejected": 1, "skipped": 0},
            {"scanned": 1, "verified": 1, "rejected": 0, "skipped": 0},
            {"scanned": 0, "verified": 0, "rejected": 0, "skipped": 0},
        ]
    )
    calls: list[dict[str, object]] = []

    def verifier(_: JobRepository, **kwargs: object) -> dict[str, int]:
        calls.append(kwargs)
        return next(reports)

    result = run_registry_career_portal_verifier(
        repository,
        actor="registry-portal-service",
        batch_size=2,
        concurrency=8,
        pace_seconds=0,
        verifier=verifier,
    )

    assert result["status"] == "drained"
    assert result["batches_completed"] == 3
    assert result["totals"] == {"scanned": 3, "verified": 2, "rejected": 1, "skipped": 0}
    assert result["activation"] == "not_performed"
    assert [call["limit"] for call in calls] == [2, 2, 2]
    assert all(call["concurrency"] == 8 for call in calls)
    assert repository.source_status() == []


def test_runner_stops_safely_when_a_batch_has_no_terminal_outcome(tmp_path):
    repository = JobRepository(tmp_path / "runner.db")
    repository.initialize()

    result = run_registry_career_portal_verifier(
        repository,
        actor="registry-portal-service",
        pace_seconds=0,
        verifier=lambda *_args, **_kwargs: {
            "scanned": 3,
            "verified": 0,
            "rejected": 0,
            "skipped": 3,
        },
    )

    assert result["status"] == "no_progress"
    assert result["batches_completed"] == 1


def test_cli_parses_durable_registry_portal_verifier_command():
    args = parser().parse_args(
        [
            "verify-registry-career-portals",
            "--actor",
            "registry-portal-service",
            "--batch-size",
            "250",
            "--concurrency",
            "8",
            "--shard-count",
            "3",
            "--shard-index",
            "1",
        ]
    )

    assert args.command == "verify-registry-career-portals"
    assert args.batch_size == 250
    assert args.concurrency == 8
    assert args.shard_count == 3
    assert args.shard_index == 1


def _registry_lead(repository: JobRepository, company_id: int, url: str) -> None:
    repository.upsert_source_fingerprint(
        company_id,
        observed_url=url,
        family="unknown_external",
        evidence={
            "review_method": "user_supplied_career_url_registry",
            "verification_status": "unverified",
            "activation_allowed": False,
            "proposed_kind": "custom_or_unrecognized",
        },
        actor="registry-import",
        mark_discovered=False,
    )


def test_fingerprint_shards_are_disjoint_and_cover_every_registry_portal(tmp_path):
    repository = JobRepository(tmp_path / "sharded-registry.db")
    repository.initialize()
    names_by_url: dict[str, str] = {}
    for number in range(9):
        name = f"Example Employer {number}, Inc."
        company_id = repository.upsert_company(name)
        url = f"https://careers-{number}.example.com/open-roles"
        names_by_url[url] = name
        _registry_lead(repository, company_id, url)

    with repository.connect() as connection:
        rows = connection.execute(
            "SELECT id, observed_url FROM career_source_fingerprints ORDER BY id"
        ).fetchall()
    expected_by_shard = {
        index: {
            str(row["observed_url"])
            for row in rows
            if int(row["id"]) % 3 == index
        }
        for index in range(3)
    }
    observed_by_shard: dict[int, set[str]] = {}

    for shard_index in range(3):
        observed: set[str] = set()

        def page_fetcher(url: str) -> LeadPage:
            observed.add(url)
            return LeadPage(
                200,
                url,
                "text/html",
                f"<title>{names_by_url[url]} careers</title>".encode(),
            )

        report = promote_verified_registry_career_portals(
            repository,
            actor=f"registry-shard-{shard_index}",
            limit=100,
            concurrency=1,
            shard_count=3,
            shard_index=shard_index,
            resolver=lambda _: ["93.184.216.34"],
            page_fetcher=page_fetcher,
        )
        assert report["scanned"] == len(expected_by_shard[shard_index])
        observed_by_shard[shard_index] = observed

    assert observed_by_shard == expected_by_shard
    assert not (observed_by_shard[0] & observed_by_shard[1])
    assert not (observed_by_shard[0] & observed_by_shard[2])
    assert not (observed_by_shard[1] & observed_by_shard[2])
    assert set().union(*observed_by_shard.values()) == {
        str(row["observed_url"]) for row in rows
    }


def test_shard_configuration_is_validated(tmp_path):
    repository = JobRepository(tmp_path / "sharded-registry.db")
    repository.initialize()

    try:
        promote_verified_registry_career_portals(
            repository,
            actor="registry-shard",
            shard_count=3,
            shard_index=3,
        )
    except ValueError as error:
        assert str(error) == "shard_index must be between 0 and shard_count - 1"
    else:
        raise AssertionError("invalid shard index must be rejected")


def test_verified_seed_is_immediately_handed_to_bounded_discovery(tmp_path):
    repository = JobRepository(tmp_path / "runner-handoff.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Industries, Inc.")
    url = "https://careers.example.com/open-roles"
    _registry_lead(repository, company_id, url)
    candidate = AtsSourceCandidate(
        connector_kind="greenhouse",
        board_token="example-industries",
        normalized_base_url="https://boards.greenhouse.io/example-industries",
        confidence=0.98,
        evidence=("exact career-page link",),
        candidate_url="https://boards.greenhouse.io/example-industries",
    )
    fingerprint = PassiveSourceFingerprint(
        observed_url="https://jobs.example.com/roles",
        family="unknown_external",
        host="jobs.example.com",
        origin_page=url,
        evidence=("career widget endpoint",),
    )
    handed_off: list[list[int]] = []

    class StubDiscovery:
        def discover(self, start_url: str) -> DiscoveryReport:
            assert start_url == url
            return DiscoveryReport(
                start_url=url,
                disposition="candidates_found",
                candidates=(candidate,),
                evidence=("bounded verified-career crawl",),
                pages_checked=(url,),
                fingerprints=(fingerprint,),
            )

    def discovery_runner(repo, companies, *, actor, concurrency):
        handed_off.append([int(company["id"]) for company in companies])
        assert actor == "registry-portal-service:registry-seed-discovery"
        assert concurrency == 4
        return discover_company_sources(
            repo,
            companies,
            actor=actor,
            concurrency=concurrency,
            discovery_factory=StubDiscovery,
        )

    result = run_registry_career_portal_verifier(
        repository,
        actor="registry-portal-service",
        batch_size=10,
        max_batches=2,
        pace_seconds=0,
        resolver=lambda _: ["93.184.216.34"],
        page_fetcher=lambda _: LeadPage(
            200,
            url,
            "text/html",
            b"<title>Example Industries, Inc. careers</title>",
        ),
        discovery_runner=discovery_runner,
    )

    assert handed_off == [[company_id]]
    assert result["discovery_handoff"] == {"companies": 1, "candidates": 1, "fingerprints": 1}
    assert repository.list_source_candidates(company_id)[0]["kind"] == "greenhouse"
    assert repository.list_source_fingerprints(company_id)[1]["observed_url"] == fingerprint.observed_url
    assert repository.source_status() == []


def test_rejected_registry_seed_is_not_handed_to_discovery(tmp_path):
    repository = JobRepository(tmp_path / "runner-handoff.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Industries, Inc.")
    url = "https://careers.example.com/open-roles"
    _registry_lead(repository, company_id, url)
    calls = 0

    def unexpected_discovery(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("rejected seed must not be handed to discovery")

    result = run_registry_career_portal_verifier(
        repository,
        actor="registry-portal-service",
        batch_size=10,
        max_batches=1,
        pace_seconds=0,
        resolver=lambda _: ["93.184.216.34"],
        page_fetcher=lambda _: LeadPage(
            200,
            url,
            "text/html",
            b"<title>Different Employer careers</title>",
        ),
        discovery_runner=unexpected_discovery,
    )

    assert calls == 0
    assert result["discovery_handoff"] == {"companies": 0, "candidates": 0, "fingerprints": 0}
    assert repository.list_source_candidates(company_id) == []
    assert repository.source_status() == []


def test_standard_candidate_uses_existing_manifest_gate_and_failed_probe_stays_discovered(
    tmp_path, monkeypatch
):
    repository = JobRepository(tmp_path / "runner-approval.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Industries, Inc.")
    url = "https://careers.example.com/open-roles"
    _registry_lead(repository, company_id, url)
    candidate = AtsSourceCandidate(
        connector_kind="greenhouse",
        board_token="example-industries",
        normalized_base_url="https://boards.greenhouse.io/example-industries",
        confidence=0.98,
        evidence=("exact career-page link",),
        candidate_url="https://boards.greenhouse.io/example-industries",
    )

    class StubDiscovery:
        def discover(self, start_url: str) -> DiscoveryReport:
            return DiscoveryReport(
                start_url=start_url,
                disposition="candidates_found",
                candidates=(candidate,),
                evidence=("bounded verified-career crawl",),
                pages_checked=(start_url,),
            )

    def discovery_runner(repo, companies, *, actor, concurrency):
        return discover_company_sources(
            repo,
            companies,
            actor=actor,
            concurrency=concurrency,
            discovery_factory=StubDiscovery,
        )

    calls: list[int] = []

    def incomplete_manifest_gate(repo, candidate_id, **_kwargs):
        calls.append(candidate_id)
        raise ValueError("candidate probe did not return a complete manifest")

    monkeypatch.setattr(
        "fortune_intel.services.bulk_source_approval.approve_source_candidate",
        incomplete_manifest_gate,
    )
    result = run_registry_career_portal_verifier(
        repository,
        actor="registry-portal-service",
        batch_size=10,
        max_batches=1,
        pace_seconds=0,
        resolver=lambda _: ["93.184.216.34"],
        page_fetcher=lambda _: LeadPage(
            200,
            url,
            "text/html",
            b"<title>Example Industries, Inc. careers</title>",
        ),
        discovery_runner=discovery_runner,
        policy_urls={"greenhouse": "https://developers.greenhouse.io/job-board"},
        policy_approved_at="2026-08-13T01:00:00+00:00",
    )

    stored = repository.list_source_candidates(company_id)[0]
    assert calls == [stored["id"]]
    assert result["manifest_approval"]["probe_failed"] == 1
    assert stored["status"] == "discovered"
    assert repository.source_status() == []
