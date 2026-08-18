from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock

from fortune_intel.importers.sec_websites import (
    SecSubmissionsWebsiteClient,
    import_sec_company_websites,
)
from fortune_intel.storage import JobRepository


@dataclass
class FakeResponse:
    status_code: int
    payload: object = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    text: str = ""

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def client_for(*responses, sleep=None):
    return SecSubmissionsWebsiteClient(
        user_agent="OpenRole-CIKBot/0.1 ops@example.org",
        session=FakeSession(responses),
        requests_per_second=5,
        sleep=sleep or (lambda _seconds: None),
    )


def payload(cik="0000789019", **values):
    return {"cik": cik, "name": "MICROSOFT CORP", "website": "", **values}


def test_client_requests_only_exact_cik_endpoint_with_contact_headers():
    client = client_for(FakeResponse(200, payload(website="https://www.microsoft.com/")))

    result = client.query([789019])

    assert result.ciks_requested == 1
    assert result.requests_made == 1
    assert result.candidates[0].website_url == "https://www.microsoft.com/"
    url, request = client.session.calls[0]
    assert url == "https://data.sec.gov/submissions/CIK0000789019.json"
    assert request["headers"]["User-Agent"] == "OpenRole-CIKBot/0.1 ops@example.org"
    assert request["allow_redirects"] is False


def test_client_rate_limits_sequential_requests_and_retries_429():
    waits = []
    client = client_for(
        FakeResponse(429, headers={"Retry-After": "2"}),
        FakeResponse(200, payload(website="https://www.microsoft.com/")),
        FakeResponse(200, {"cik": "0001652044", "name": "Alphabet Inc."}),
        sleep=waits.append,
    )

    result = client.query([789019, 1652044])

    assert result.requests_made == 3
    assert waits == [2, 0.2]


def test_client_rejects_mismatched_cik_and_non_absolute_url():
    client = client_for(
        FakeResponse(200, payload(cik="0000320193", website="https://www.microsoft.com/")),
        FakeResponse(200, payload(cik="0001018724", website="www.microsoft.com")),
    )

    result = client.query([789019, 1018724])

    assert result.candidates == ()
    assert result.invalid_payloads == 1
    assert result.invalid_urls == 1


def test_import_records_exact_sec_provenance(tmp_path):
    repository = JobRepository(tmp_path / "sec-websites.db")
    repository.initialize()
    company_id = repository.upsert_company("Microsoft Corp", sec_cik=789019)
    client = client_for(FakeResponse(200, payload(website="https://www.microsoft.com/")))

    stats = import_sec_company_websites(repository, client, actor="website-import@example.org")

    assert stats["websites_imported"] == 1
    assert stats["sec_website_used"] == 1
    company = repository.find_company_by_normalized_name("Microsoft Corp")
    assert company["website_url"] == "https://www.microsoft.com/"
    event = repository.company_coverage_events(company_id)[0]
    assert "exact CIK 0000789019" in event["reason"]
    assert "top-level website" in event["reason"]
    assert "data.sec.gov/submissions/CIK0000789019.json" in event["reason"]


def test_import_uses_explicit_investor_website_as_labeled_fallback(tmp_path):
    repository = JobRepository(tmp_path / "sec-investor-websites.db")
    repository.initialize()
    company_id = repository.upsert_company("Microsoft Corp", sec_cik=789019)
    client = client_for(
        FakeResponse(
            200,
            payload(investorWebsite="https://www.microsoft.com/en-us/investor"),
        )
    )

    stats = import_sec_company_websites(repository, client, actor="reviewer@example.org")

    assert stats["sec_investor_website_fallback_used"] == 1
    assert repository.find_company_by_normalized_name("Microsoft Corp")["website_url"] == (
        "https://www.microsoft.com/en-us/investor"
    )
    event = repository.company_coverage_events(company_id)[0]
    assert "investor website fallback" in event["reason"]
    assert "top-level investorWebsite" in event["reason"]


def test_import_only_queries_companies_still_missing_website(tmp_path):
    repository = JobRepository(tmp_path / "sec-missing-only.db")
    repository.initialize()
    repository.upsert_company(
        "Already Seeded", sec_cik=320193, website_url="https://www.apple.com/"
    )
    repository.upsert_company("Missing Seed", sec_cik=789019)
    client = client_for(FakeResponse(200, payload(website="https://www.microsoft.com/")))

    stats = import_sec_company_websites(repository, client, actor="reviewer@example.org")

    assert stats["companies_considered"] == 1
    assert stats["ciks_queried"] == 1
    assert client.session.calls[0][0].endswith("CIK0000789019.json")


def test_dry_run_reports_without_writing(tmp_path):
    repository = JobRepository(tmp_path / "sec-dry-run.db")
    repository.initialize()
    repository.upsert_company("Microsoft Corp", sec_cik=789019)
    client = client_for(FakeResponse(200, payload(website="https://www.microsoft.com/")))

    stats = import_sec_company_websites(
        repository, client, actor="reviewer@example.org", dry_run=True
    )

    assert stats["websites_ready"] == 1
    assert stats["websites_imported"] == 0
    assert repository.find_company_by_normalized_name("Microsoft Corp")["website_url"] == ""


def test_failed_request_is_not_misreported_as_empty_sec_url(tmp_path):
    repository = JobRepository(tmp_path / "sec-request-failure.db")
    repository.initialize()
    repository.upsert_company("Microsoft Corp", sec_cik=789019)
    client = client_for(FakeResponse(403, text="denied"))

    stats = import_sec_company_websites(repository, client, actor="reviewer@example.org")

    assert stats["request_failures"] == 1
    assert stats["unresolved_query"] == 1
    assert stats["no_sec_url"] == 0


def test_bounded_concurrency_keeps_exact_cik_results_isolated():
    responses = {
        "0000789019": FakeResponse(200, payload(website="https://www.microsoft.com/")),
        "0001652044": FakeResponse(
            200,
            {
                "cik": "0001652044",
                "name": "Alphabet Inc.",
                "website": "https://abc.xyz/",
            },
        ),
    }
    lock = Lock()

    class ConcurrentSession:
        def get(self, url, **_kwargs):
            cik = url.rsplit("CIK", 1)[1].split(".", 1)[0]
            with lock:
                return responses.pop(cik)

    client = SecSubmissionsWebsiteClient(
        user_agent="OpenRole-CIKBot/0.1 ops@example.org",
        requests_per_second=10,
        concurrency=2,
        session_factory=ConcurrentSession,
    )

    result = client.query([1652044, 789019])

    assert result.requests_made == 2
    assert [item.sec_cik for item in result.candidates] == ["0000789019", "0001652044"]
    assert [item.website_url for item in result.candidates] == [
        "https://www.microsoft.com/",
        "https://abc.xyz/",
    ]
