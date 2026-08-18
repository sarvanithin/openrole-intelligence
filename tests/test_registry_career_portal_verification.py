from __future__ import annotations

import threading
import time

from fortune_intel.services.licensed_lead_verification import LeadPage
from fortune_intel.services.registry_career_portal_verification import (
    promote_verified_registry_career_portals,
)
from fortune_intel.services.source_provenance import verified_company_seed_evidence
from fortune_intel.storage import JobRepository


def _registry_portal(repository: JobRepository, company_id: int, url: str) -> None:
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


def test_verified_registry_career_page_becomes_discovery_seed_not_a_source(tmp_path):
    repository = JobRepository(tmp_path / "registry-career.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Industries, Inc.")
    url = "https://careers.example.com/open-roles"
    _registry_portal(repository, company_id, url)

    report = promote_verified_registry_career_portals(
        repository,
        actor="registry-career-verifier",
        resolver=lambda _: ["93.184.216.34"],
        page_fetcher=lambda _: LeadPage(
            200,
            url,
            "text/html; charset=utf-8",
            b"<title>Careers at Example Industries, Inc.</title>",
        ),
    )

    assert report == {"scanned": 1, "verified": 1, "rejected": 0, "skipped": 0}
    company = repository.find_company_by_normalized_name("Example Industries, Inc.")
    assert company["career_url"] == url
    website, career, events = verified_company_seed_evidence(repository, company)
    assert website == ""
    assert career == url
    assert events
    assert repository.list_source_candidates(company_id) == []
    assert repository.source_status() == []
    fingerprint = repository.list_source_fingerprints(company_id)[0]
    assert fingerprint["evidence"]["verification_status"] == "verified"
    assert fingerprint["evidence"]["verification_attempt"]["seed_persisted"] is True


def test_private_or_reserved_resolution_is_rejected_before_fetch(tmp_path):
    repository = JobRepository(tmp_path / "registry-career.db")
    repository.initialize()
    company_id = repository.upsert_company("Example Industries, Inc.")
    _registry_portal(repository, company_id, "https://careers.example.com/open-roles")
    fetched = False

    def fetch(_: str) -> LeadPage:
        nonlocal fetched
        fetched = True
        raise AssertionError("unsafe destination must not be fetched")

    report = promote_verified_registry_career_portals(
        repository,
        actor="registry-career-verifier",
        resolver=lambda _: ["127.0.0.1"],
        page_fetcher=fetch,
    )

    assert report == {"scanned": 1, "verified": 0, "rejected": 1, "skipped": 0}
    assert fetched is False
    company = repository.find_company_by_normalized_name("Example Industries, Inc.")
    assert company["career_url"] == ""


def test_unknown_external_registry_row_is_verified_but_known_ats_is_not(tmp_path):
    repository = JobRepository(tmp_path / "registry-career.db")
    repository.initialize()
    legacy_company_id = repository.upsert_company("Legacy Example Industries, Inc.")
    legacy_url = "https://careers.legacy-example.com/open-roles"
    repository.upsert_source_fingerprint(
        legacy_company_id,
        observed_url=legacy_url,
        family="unknown_external",
        evidence={
            "review_method": "user_supplied_career_url_registry",
            "verification_status": "unverified",
            "activation_allowed": False,
            "proposed_kind": "unknown_external",
        },
        actor="legacy-registry-import",
        mark_discovered=False,
    )
    ats_company_id = repository.upsert_company("Known ATS Industries, Inc.")
    _registry_portal(repository, ats_company_id, "https://boards.greenhouse.io/knownats")
    with repository.connect() as connection:
        evidence = connection.execute(
            "SELECT evidence_json FROM career_source_fingerprints WHERE company_id = ?",
            (ats_company_id,),
        ).fetchone()["evidence_json"]
        connection.execute(
            "UPDATE career_source_fingerprints SET evidence_json = ? WHERE company_id = ?",
            (str(evidence).replace('custom_or_unrecognized', 'greenhouse'), ats_company_id),
        )

    report = promote_verified_registry_career_portals(
        repository,
        actor="registry-career-verifier",
        resolver=lambda _: ["93.184.216.34"],
        page_fetcher=lambda url: LeadPage(
            200,
            url,
            "text/html",
            b"<title>Legacy Example Industries, Inc. careers</title>",
        ),
    )

    assert report == {"scanned": 1, "verified": 1, "rejected": 0, "skipped": 0}
    legacy = repository.find_company_by_normalized_name("Legacy Example Industries, Inc.")
    known_ats = repository.find_company_by_normalized_name("Known ATS Industries, Inc.")
    assert legacy["career_url"] == legacy_url
    assert known_ats["career_url"] == ""


def test_redirect_or_different_existing_career_url_is_never_overwritten(tmp_path):
    repository = JobRepository(tmp_path / "registry-career.db")
    repository.initialize()
    company_id = repository.upsert_company(
        "Example Industries, Inc.", career_url="https://existing.example.com/careers"
    )
    url = "https://careers.example.com/open-roles"
    _registry_portal(repository, company_id, url)

    report = promote_verified_registry_career_portals(
        repository,
        actor="registry-career-verifier",
        resolver=lambda _: ["93.184.216.34"],
        page_fetcher=lambda _: LeadPage(301, "https://elsewhere.example/", "text/html", b""),
    )

    assert report == {"scanned": 1, "verified": 0, "rejected": 1, "skipped": 0}
    company = repository.find_company_by_normalized_name("Example Industries, Inc.")
    assert company["career_url"] == "https://existing.example.com/careers"


def test_bounded_concurrency_keeps_portal_writes_deterministic(tmp_path):
    repository = JobRepository(tmp_path / "registry-career.db")
    repository.initialize()
    urls: dict[str, str] = {}
    for index in range(6):
        name = f"Example Industries {index:02d}"
        company_id = repository.upsert_company(name)
        url = f"https://careers-{index}.example.com/open-roles"
        urls[url] = name
        _registry_portal(repository, company_id, url)

    lock = threading.Lock()
    active = 0
    maximum_active = 0

    def fetch(url: str) -> LeadPage:
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        return LeadPage(200, url, "text/html", f"<title>{urls[url]} careers</title>".encode())

    report = promote_verified_registry_career_portals(
        repository,
        actor="registry-career-verifier",
        concurrency=3,
        resolver=lambda _: ["93.184.216.34"],
        page_fetcher=fetch,
    )

    assert report == {"scanned": 6, "verified": 6, "rejected": 0, "skipped": 0}
    assert maximum_active == 3
    assert [
        repository.find_company_by_normalized_name(name)["career_url"] for name in urls.values()
    ] == list(urls)
