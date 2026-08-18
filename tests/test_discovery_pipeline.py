from fortune_intel.discovery import AtsSourceCandidate, DiscoveryReport, PassiveSourceFingerprint
from fortune_intel.services.discovery_pipeline import discover_company_sources
from fortune_intel.storage import JobRepository


class StubDiscovery:
    def __init__(self, report):
        self.report = report

    def discover(self, start_url):
        assert start_url == "https://www.example.com/"
        return self.report


class ReportsByUrlDiscovery:
    def __init__(self, reports, calls):
        self.reports = reports
        self.calls = calls

    def discover(self, start_url):
        self.calls.append(start_url)
        report = self.reports[start_url]
        if isinstance(report, Exception):
            raise report
        return report


def report(start_url, disposition="no_supported_ats_found", candidates=(), pages_checked=()):
    return DiscoveryReport(
        start_url=start_url,
        disposition=disposition,
        candidates=candidates,
        evidence=(f"checked {start_url}",),
        pages_checked=pages_checked,
    )


def test_discovery_persists_candidate_without_approving_it(tmp_path):
    repository = JobRepository(tmp_path / "discovery.db")
    repository.initialize()
    repository.upsert_company("Example", website_url="https://www.example.com")
    company = repository.list_companies()[0]
    candidate = AtsSourceCandidate(
        connector_kind="greenhouse",
        board_token="example",
        normalized_base_url="https://boards.greenhouse.io/example",
        confidence=0.99,
        evidence=("official company link",),
        candidate_url="https://job-boards.greenhouse.io/example/jobs/1",
    )
    report = DiscoveryReport(
        start_url="https://www.example.com/",
        disposition="candidates_found",
        candidates=(candidate,),
        evidence=("bounded crawl",),
        pages_checked=("https://www.example.com/",),
    )

    results = discover_company_sources(
        repository,
        [company],
        actor="discovery@example.org",
        discovery_factory=lambda: StubDiscovery(report),
    )

    assert results[0]["disposition"] == "candidate"
    stored = repository.list_source_candidates(company["id"])[0]
    assert stored["status"] == "discovered"
    assert stored["robots_status"] == "unknown"
    assert stored["terms_status"] == "review_required"
    assert stored["evidence"]["board_token"] == "example"
    assert repository.source_status() == []


def test_discovery_marks_robots_denial_as_blocked(tmp_path):
    repository = JobRepository(tmp_path / "blocked.db")
    repository.initialize()
    repository.upsert_company("Example", website_url="https://www.example.com")
    company = repository.list_companies()[0]
    report = DiscoveryReport(
        start_url="https://www.example.com/",
        disposition="robots_denied",
        candidates=(),
        evidence=("robots disallows URL",),
        pages_checked=("https://www.example.com/",),
    )

    discover_company_sources(
        repository,
        [company],
        actor="discovery@example.org",
        discovery_factory=lambda: StubDiscovery(report),
    )

    assert repository.get_company_coverage(company["id"])["disposition"] == "blocked"


def test_discovery_checks_distinct_career_and_website_seeds(tmp_path):
    repository = JobRepository(tmp_path / "fallback.db")
    repository.initialize()
    repository.upsert_company(
        "Example",
        career_url="https://careers.example.com/openings",
        website_url="https://www.example.com/",
    )
    company = repository.list_companies()[0]
    candidate = AtsSourceCandidate(
        connector_kind="ashby",
        board_token="example",
        normalized_base_url="https://jobs.ashbyhq.com/example",
        confidence=0.99,
        evidence=("official homepage link",),
        candidate_url="https://jobs.ashbyhq.com/example/jobs/1",
    )
    calls = []
    reports = {
        "https://careers.example.com/openings": report(
            "https://careers.example.com/openings",
            pages_checked=("https://careers.example.com/openings",),
        ),
        "https://www.example.com/": report(
            "https://www.example.com/",
            disposition="candidates_found",
            candidates=(candidate,),
            pages_checked=("https://www.example.com/",),
        ),
    }

    results = discover_company_sources(
        repository,
        [company],
        actor="discovery@example.org",
        concurrency=1,
        discovery_factory=lambda: ReportsByUrlDiscovery(reports, calls),
    )

    assert calls == ["https://careers.example.com/openings", "https://www.example.com/"]
    assert results[0]["seed_urls_checked"] == 2
    assert results[0]["pages_checked"] == 2
    assert results[0]["disposition"] == "candidate"
    evidence = repository.list_source_candidates(company["id"])[0]["evidence"]
    assert evidence["seed_urls_checked"] == calls
    assert evidence["pages_checked"] == calls


def test_direct_supported_ats_seed_is_classified_without_fetching_it(tmp_path):
    repository = JobRepository(tmp_path / "direct.db")
    repository.initialize()
    repository.upsert_company("Example", career_url="https://jobs.lever.co/example")
    company = repository.list_companies()[0]

    def unexpected_factory():
        raise AssertionError("direct ATS seed must not be fetched during discovery")

    results = discover_company_sources(
        repository,
        [company],
        actor="discovery@example.org",
        discovery_factory=unexpected_factory,
    )

    assert results[0]["disposition"] == "candidate"
    assert results[0]["pages_checked"] == 0
    stored = repository.list_source_candidates(company["id"])[0]
    assert stored["kind"] == "lever"
    assert "external host was not fetched" in stored["evidence"]["discovery_evidence"][0]


def test_equivalent_seeds_are_not_crawled_twice(tmp_path):
    repository = JobRepository(tmp_path / "duplicate-seeds.db")
    repository.initialize()
    repository.upsert_company(
        "Example",
        career_url="https://www.example.com/",
        website_url="https://www.example.com",
    )
    company = repository.list_companies()[0]
    calls = []
    reports = {"https://www.example.com/": report("https://www.example.com/")}

    results = discover_company_sources(
        repository,
        [company],
        actor="discovery@example.org",
        concurrency=1,
        discovery_factory=lambda: ReportsByUrlDiscovery(reports, calls),
    )

    assert calls == ["https://www.example.com/"]
    assert results[0]["seed_urls_checked"] == 1


def test_duplicate_candidate_from_two_seeds_is_persisted_once_with_combined_evidence(tmp_path):
    repository = JobRepository(tmp_path / "duplicate-candidate.db")
    repository.initialize()
    repository.upsert_company(
        "Example",
        career_url="https://careers.example.com/",
        website_url="https://www.example.com/",
    )
    company = repository.list_companies()[0]
    first = AtsSourceCandidate(
        connector_kind="greenhouse",
        board_token="Example",
        normalized_base_url="https://boards.greenhouse.io/Example",
        confidence=0.98,
        evidence=("career evidence",),
        candidate_url="https://job-boards.greenhouse.io/Example/jobs/1",
    )
    second = AtsSourceCandidate(
        connector_kind="greenhouse",
        board_token="example",
        normalized_base_url="https://boards.greenhouse.io/example",
        confidence=0.99,
        evidence=("homepage evidence",),
        candidate_url="https://boards.greenhouse.io/example",
    )
    calls = []
    reports = {
        "https://careers.example.com/": report(
            "https://careers.example.com/", "candidates_found", (first,)
        ),
        "https://www.example.com/": report(
            "https://www.example.com/", "candidates_found", (second,)
        ),
    }

    results = discover_company_sources(
        repository,
        [company],
        actor="discovery@example.org",
        concurrency=1,
        discovery_factory=lambda: ReportsByUrlDiscovery(reports, calls),
    )

    assert len(results[0]["candidate_ids"]) == 1
    stored = repository.list_source_candidates(company["id"])
    assert len(stored) == 1
    assert stored[0]["confidence"] == 0.99
    assert stored[0]["evidence"]["candidate_evidence"] == [
        "career evidence",
        "homepage evidence",
    ]
    assert len(stored[0]["evidence"]["candidate_urls"]) == 2


def test_unexpected_failure_isolated_to_one_company(tmp_path):
    repository = JobRepository(tmp_path / "failure-isolation.db")
    repository.initialize()
    repository.upsert_company("Broken", website_url="https://broken.example/")
    repository.upsert_company("Healthy", website_url="https://healthy.example/")
    companies = repository.list_companies()
    calls = []
    reports = {
        "https://broken.example/": RuntimeError("third-party response body"),
        "https://healthy.example/": report("https://healthy.example/"),
    }

    results = discover_company_sources(
        repository,
        companies,
        actor="discovery@example.org",
        concurrency=1,
        discovery_factory=lambda: ReportsByUrlDiscovery(reports, calls),
    )

    assert [result["disposition"] for result in results] == ["blocked", "unsupported"]
    broken = repository.get_company_coverage(companies[0]["id"])
    assert "fetch_failed" in broken["reason"]
    assert "third-party response body" not in broken["reason"]


def test_passive_fingerprint_is_inventoried_but_never_becomes_a_candidate(tmp_path):
    repository = JobRepository(tmp_path / "passive.db")
    repository.initialize()
    repository.upsert_company("Example", website_url="https://www.example.com/")
    company = repository.list_companies()[0]
    fingerprint = PassiveSourceFingerprint(
        family="successfactors",
        observed_url="https://career5.successfactors.eu/career?company=example",
        host="career5.successfactors.eu",
        origin_page="https://www.example.com/careers",
        evidence=("exact SuccessFactors host", "passive only"),
    )
    passive_report = DiscoveryReport(
        start_url="https://www.example.com/",
        disposition="no_supported_ats_found",
        candidates=(),
        evidence=("bounded crawl",),
        pages_checked=("https://www.example.com/",),
        fingerprints=(fingerprint,),
    )

    for _ in range(2):
        results = discover_company_sources(
            repository,
            [company],
            actor="inventory@example.org",
            discovery_factory=lambda: StubDiscovery(passive_report),
        )

    assert results[0]["disposition"] == "unsupported"
    assert len(results[0]["fingerprint_ids"]) == 1
    assert results[0]["candidate_ids"] == []
    assert repository.list_source_candidates(company["id"]) == []
    assert repository.source_status() == []
    stored = repository.list_source_fingerprints(company["id"])[0]
    assert stored["family"] == "successfactors"
    assert stored["observed_url"] == fingerprint.observed_url
    assert stored["observation_count"] == 2
    assert stored["last_observed_by"] == "inventory@example.org"
    assert stored["evidence"]["origin_page"] == fingerprint.origin_page
    assert repository.source_fingerprint_inventory()[0]["companies"] == 1
    assert repository.source_fingerprint_inventory()[0]["observations"] == 2
