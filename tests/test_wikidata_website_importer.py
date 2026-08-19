from __future__ import annotations

from dataclasses import dataclass, field

from fortune_intel.cli import parser
from fortune_intel.importers.wikidata_websites import (
    WikidataWebsiteClient,
    import_wikidata_company_websites,
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

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def binding(cik, item, website):
    return {
        "cik": {"type": "literal", "value": cik},
        "item": {"type": "uri", "value": item},
        "website": {"type": "uri", "value": website},
    }


def jobs_binding(cik, item, career):
    return {
        "cik": {"type": "literal", "value": cik},
        "item": {"type": "uri", "value": item},
        "career": {"type": "uri", "value": career},
    }


def sparql_payload(*bindings):
    return {"results": {"bindings": list(bindings)}}


def client_for(*responses, batch_size=100, sleep=None):
    return WikidataWebsiteClient(
        user_agent="OpenRole-CIKBot/0.1 (ops@example.org)",
        session=FakeSession(responses),
        batch_size=batch_size,
        page_delay_seconds=0,
        sleep=sleep or (lambda _seconds: None),
    )


def test_client_pages_exact_ciks_and_sends_identifying_headers():
    session = FakeSession(
        [
            FakeResponse(200, sparql_payload()),
            FakeResponse(200, sparql_payload()),
        ]
    )
    client = WikidataWebsiteClient(
        user_agent="OpenRole-CIKBot/0.1 (ops@example.org)",
        session=session,
        batch_size=2,
        page_delay_seconds=0,
    )

    result = client.query([320193, "789019", "1652044"])

    assert result.pages_requested == 2
    assert len(session.calls) == 2
    first = session.calls[0][1]
    assert 'VALUES ?cik { "0000320193" "0000789019" }' in first["data"]["query"]
    assert "P5531" in first["data"]["query"]
    assert "P856" in first["data"]["query"]
    assert "P10311" in first["data"]["query"]
    assert first["headers"]["User-Agent"] == "OpenRole-CIKBot/0.1 (ops@example.org)"
    assert first["headers"]["Accept-Encoding"] == "gzip"


def test_client_retries_429_using_retry_after():
    waits = []
    session = FakeSession(
        [
            FakeResponse(429, text="slow down", headers={"Retry-After": "7"}),
            FakeResponse(
                200,
                sparql_payload(
                    binding(
                        "0000789019",
                        "http://www.wikidata.org/entity/Q2283",
                        "https://www.microsoft.com/",
                    )
                ),
            ),
        ]
    )
    client = WikidataWebsiteClient(
        user_agent="OpenRole-CIKBot/0.1 (ops@example.org)",
        session=session,
        page_delay_seconds=0,
        sleep=waits.append,
    )

    result = client.query([789019])

    assert waits == [7]
    assert result.candidates[0].item_url == "https://www.wikidata.org/entity/Q2283"
    assert result.candidates[0].website_url == "https://www.microsoft.com/"


def test_import_uses_exact_cik_and_records_wikidata_provenance(tmp_path):
    repository = JobRepository(tmp_path / "wikidata.db")
    repository.initialize()
    company_id = repository.upsert_company("Microsoft Corp", sec_cik=789019, ticker="MSFT")
    client = client_for(
        FakeResponse(
            200,
            sparql_payload(
                binding(
                    "0000789019",
                    "http://www.wikidata.org/entity/Q2283",
                    "https://www.microsoft.com/",
                )
            ),
        )
    )

    stats = import_wikidata_company_websites(repository, client, actor="website-import@example.org")

    assert stats["websites_imported"] == 1
    assert stats["websites_ready"] == 1
    company = repository.find_company_by_normalized_name("Microsoft Corp")
    assert company["website_url"] == "https://www.microsoft.com/"
    event = repository.company_coverage_events(company_id)[0]
    assert event["actor"] == "website-import@example.org"
    assert "exact SEC CIK 0000789019" in event["reason"]
    assert "Q2283 P5531 -> P856" in event["reason"]


def test_import_does_not_guess_between_multiple_wikidata_websites(tmp_path):
    repository = JobRepository(tmp_path / "ambiguous.db")
    repository.initialize()
    repository.upsert_company("Apple Inc.", sec_cik=320193, ticker="AAPL")
    client = client_for(
        FakeResponse(
            200,
            sparql_payload(
                binding(
                    "0000320193",
                    "http://www.wikidata.org/entity/Q312",
                    "https://www.apple.com/",
                ),
                binding(
                    "0000320193",
                    "http://www.wikidata.org/entity/Q312",
                    "https://www.apple.com.cn/",
                ),
            ),
        )
    )

    stats = import_wikidata_company_websites(repository, client, actor="reviewer@example.org")

    assert stats["ambiguous_wikidata_website"] == 1
    assert stats["websites_imported"] == 0
    assert repository.find_company_by_normalized_name("Apple Inc.")["website_url"] == ""


def test_import_never_overwrites_conflicting_existing_website(tmp_path):
    repository = JobRepository(tmp_path / "conflict.db")
    repository.initialize()
    repository.upsert_company(
        "Microsoft Corp", sec_cik=789019, website_url="https://microsoft.example/"
    )
    client = client_for(
        FakeResponse(
            200,
            sparql_payload(
                binding(
                    "0000789019",
                    "http://www.wikidata.org/entity/Q2283",
                    "https://www.microsoft.com/",
                )
            ),
        )
    )

    stats = import_wikidata_company_websites(repository, client, actor="reviewer@example.org")

    assert stats["existing_website_conflict"] == 1
    assert stats["websites_imported"] == 0
    assert (
        repository.find_company_by_normalized_name("Microsoft Corp")["website_url"]
        == "https://microsoft.example/"
    )


def test_import_prefers_exact_cik_official_jobs_url_for_career_seed(tmp_path):
    repository = JobRepository(tmp_path / "jobs-url.db")
    repository.initialize()
    company_id = repository.upsert_company("Apple Inc.", sec_cik=320193)
    client = client_for(
        FakeResponse(
            200,
            sparql_payload(
                jobs_binding(
                    "0000320193",
                    "http://www.wikidata.org/entity/Q312",
                    "https://www.apple.com/careers/us/",
                )
            ),
        )
    )

    stats = import_wikidata_company_websites(repository, client, actor="website-import@example.org")

    assert stats["career_urls_ready"] == 1
    assert stats["career_urls_imported"] == 1
    company = repository.find_company_by_normalized_name("Apple Inc.")
    assert company["career_url"] == "https://www.apple.com/careers/us"
    event = repository.company_coverage_events(company_id)[0]
    assert "P5531 -> P10311 (https://www.apple.com/careers/us)" in event["reason"]


def test_import_does_not_choose_between_multiple_official_jobs_urls(tmp_path):
    repository = JobRepository(tmp_path / "ambiguous-jobs.db")
    repository.initialize()
    repository.upsert_company("Example Inc.", sec_cik=1234)
    client = client_for(
        FakeResponse(
            200,
            sparql_payload(
                jobs_binding(
                    "0000001234",
                    "http://www.wikidata.org/entity/Q42",
                    "https://jobs.example.com/us/",
                ),
                jobs_binding(
                    "0000001234",
                    "http://www.wikidata.org/entity/Q42",
                    "https://jobs.example.com/gb/",
                ),
            ),
        )
    )

    stats = import_wikidata_company_websites(repository, client, actor="reviewer@example.org")

    assert stats["ambiguous_wikidata_career_url"] == 1
    assert stats["career_urls_imported"] == 0
    assert repository.find_company_by_normalized_name("Example Inc.")["career_url"] == ""


def test_dry_run_reports_ready_website_without_writing(tmp_path):
    repository = JobRepository(tmp_path / "dry-run.db")
    repository.initialize()
    repository.upsert_company("Microsoft Corp", sec_cik=789019)
    client = client_for(
        FakeResponse(
            200,
            sparql_payload(
                binding(
                    "0000789019",
                    "http://www.wikidata.org/entity/Q2283",
                    "https://www.microsoft.com/",
                )
            ),
        )
    )

    stats = import_wikidata_company_websites(
        repository, client, actor="reviewer@example.org", dry_run=True
    )

    assert stats["websites_ready"] == 1
    assert stats["websites_imported"] == 0
    assert repository.find_company_by_normalized_name("Microsoft Corp")["website_url"] == ""


def test_company_id_cursor_processes_missing_websites_without_skipping(tmp_path):
    repository = JobRepository(tmp_path / "cursor.db")
    repository.initialize()
    first_id = repository.upsert_company("First Inc", sec_cik=101)
    repository.upsert_company("Already Known Inc", sec_cik=202, website_url="https://known.test/")
    third_id = repository.upsert_company("Third Inc", sec_cik=303)

    first_client = client_for(FakeResponse(200, sparql_payload()))
    first = import_wikidata_company_websites(
        repository,
        first_client,
        actor="reviewer@example.org",
        limit=1,
        dry_run=True,
        missing_websites_only=True,
    )
    second_client = client_for(FakeResponse(200, sparql_payload()))
    second = import_wikidata_company_websites(
        repository,
        second_client,
        actor="reviewer@example.org",
        limit=1,
        dry_run=True,
        after_company_id=first["safe_resume_after_company_id"],
        missing_websites_only=True,
    )

    assert first["first_company_id_processed"] == first_id
    assert first["safe_resume_after_company_id"] == first_id
    assert first["has_more"] is True
    assert second["first_company_id_processed"] == third_id
    assert second["safe_resume_after_company_id"] == third_id
    assert second["has_more"] is False
    assert 'VALUES ?cik { "0000000101" }' in first_client.session.calls[0][1]["data"]["query"]
    assert 'VALUES ?cik { "0000000303" }' in second_client.session.calls[0][1]["data"]["query"]


def test_cursor_batch_rejects_cik_collision_outside_the_batch(tmp_path):
    repository = JobRepository(tmp_path / "global-cik.db")
    repository.initialize()
    repository.upsert_company("First Legal Entity Inc", sec_cik=101)
    repository.upsert_company("Duplicate CIK Entity LLC", sec_cik=101)
    client = client_for(
        FakeResponse(
            200,
            sparql_payload(
                binding(
                    "0000000101",
                    "http://www.wikidata.org/entity/Q101",
                    "https://first.example/",
                )
            ),
        )
    )

    stats = import_wikidata_company_websites(
        repository, client, actor="reviewer@example.org", limit=1, dry_run=True
    )

    assert stats["companies_considered"] == 1
    assert stats["ambiguous_company_cik"] == 1
    assert stats["websites_ready"] == 0


def test_wikidata_cli_accepts_exclusive_resume_cursor():
    arguments = parser().parse_args(
        [
            "import-wikidata-websites",
            "--actor",
            "reviewer@example.org",
            "--user-agent",
            "OpenRole-CIKBot/0.1 reviewer@example.org",
            "--after-company-id",
            "42",
            "--dry-run",
        ]
    )

    assert arguments.after_company_id == 42
    assert arguments.dry_run is True
