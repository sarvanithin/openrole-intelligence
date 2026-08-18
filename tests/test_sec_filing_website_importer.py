from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

from fortune_intel.importers.sec_filing_websites import (
    SecFilingWebsiteClient,
    extract_declared_company_websites,
    import_sec_filing_company_websites,
)
from fortune_intel.storage import JobRepository

FIXTURES = Path(__file__).parent / "fixtures"


@dataclass
class FakeResponse:
    status_code: int
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    closed: bool = False

    def iter_content(self, chunk_size: int):
        for start in range(0, len(self.body), chunk_size):
            yield self.body[start : start + chunk_size]

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def json_response(payload, status=200, **headers):
    return FakeResponse(
        status,
        json.dumps(payload).encode(),
        {"Content-Type": "application/json", **headers},
    )


def html_response(body: bytes, status=200, **headers):
    return FakeResponse(status, body, {"Content-Type": "text/html; charset=utf-8", **headers})


def submissions(cik="0001084869", name="1-800-FLOWERS.COM, INC."):
    payload = json.loads((FIXTURES / "sec_filing_submissions.json").read_text())
    payload["cik"] = cik
    payload["name"] = name
    return payload


def client_for(*responses, **options):
    return SecFilingWebsiteClient(
        user_agent="OpenRole-FilingBot/0.1 ops@example.org",
        session=FakeSession(responses),
        requests_per_second=10,
        sleep=lambda _seconds: None,
        **options,
    )


def test_extracts_only_explicit_primary_company_declaration():
    html = (FIXTURES / "sec_filing_annual.html").read_bytes()

    evidence = extract_declared_company_websites(html, company_name="1-800-FLOWERS.COM, INC.")

    assert [item.website_url for item in evidence] == ["https://www.1800flowers.com/"]
    assert "Company’s internet address is" in evidence[0].evidence_text


def test_hidden_or_nonvisible_anchor_target_cannot_become_website_evidence():
    html = b'<p>Our website is located at <a href="https://attacker.example">here</a>.</p>'

    evidence = extract_declared_company_websites(html, company_name="ACME CORP")

    assert evidence == ()


def test_query_and_fragment_bearing_declarations_are_rejected():
    html = b"<p>Our website is https://example.com/careers?source=filing#jobs.</p>"

    evidence = extract_declared_company_websites(html, company_name="ACME CORP")

    assert evidence == ()


def test_declared_deep_page_is_reduced_to_verified_site_origin():
    html = b"<p>Our website is https://example.com/about/governance.</p>"

    evidence = extract_declared_company_websites(html, company_name="ACME CORP")

    assert [item.website_url for item in evidence] == ["https://example.com/"]


def test_hosted_investor_and_nested_ir_hosts_are_rejected():
    hosted = b"<p>Our website is https://acme.gcs-web.com/governance.</p>"
    nested = b"<p>Our website is https://www.ir.acme.example/governance.</p>"

    assert extract_declared_company_websites(hosted, company_name="ACME CORP") == ()
    assert extract_declared_company_websites(nested, company_name="ACME CORP") == ()


def test_query_uses_latest_base_annual_not_later_amendment_and_preserves_provenance():
    annual = (FIXTURES / "sec_filing_annual.html").read_bytes()
    client = client_for(json_response(submissions()), html_response(annual))

    result = client.query([1084869])

    candidate = result.candidates[0]
    assert candidate.filing_form == "10-K"
    assert candidate.filing_date == "2025-09-12"
    assert candidate.accession_number == "0001084869-25-000017"
    assert candidate.primary_document_url == (
        "https://www.sec.gov/Archives/edgar/data/1084869/000108486925000017/flws-20250629.htm"
    )
    assert candidate.website_url == "https://www.1800flowers.com/"
    assert result.requests_made == 2
    assert all(call[1]["allow_redirects"] is False for call in client.session.calls)
    assert all(call[1]["stream"] is True for call in client.session.calls)


def test_multiple_explicit_company_domains_remain_unresolved():
    html = b"""
      <p>Our corporate website is https://example.com.</p>
      <p>The Company's internet address is https://different.example.</p>
    """
    client = client_for(json_response(submissions()), html_response(html))

    result = client.query([1084869])

    assert result.candidates == ()
    assert result.conflicts == 1


def test_oversized_document_is_stopped_and_reported():
    response = html_response(b"x" * 100_001)
    client = client_for(
        json_response(submissions()),
        response,
        max_html_bytes=100_000,
    )

    result = client.query([1084869])

    assert result.oversized_documents == 1
    assert result.candidates == ()
    assert response.closed is True


def test_permanent_http_failure_is_reported_but_not_marked_retryable():
    client = client_for(
        json_response(submissions()),
        FakeResponse(404, headers={"Content-Type": "application/xml"}),
        max_retries=0,
    )

    result = client.query([1084869])

    assert result.request_failures == 1
    assert result.retryable_ciks == ()


def test_transient_http_failure_is_marked_retryable_after_retries_exhausted():
    client = client_for(
        FakeResponse(503, headers={"Content-Type": "application/json"}),
        max_retries=0,
    )

    result = client.query([1084869])

    assert result.request_failures == 1
    assert result.retryable_ciks == ("0001084869",)


def test_import_is_exact_cik_missing_only_auditable_and_resumable(tmp_path):
    repository = JobRepository(tmp_path / "filing-websites.db")
    repository.initialize()
    repository.upsert_company("Earlier", sec_cik=100, website_url="https://already.example/")
    company_id = repository.upsert_company("Flowers", sec_cik=1084869)
    repository.upsert_company("Later", sec_cik=2000000)
    annual = (FIXTURES / "sec_filing_annual.html").read_bytes()
    client = client_for(json_response(submissions()), html_response(annual))

    stats = import_sec_filing_company_websites(
        repository,
        client,
        actor="reviewer@example.org",
        after_cik=100,
        limit=1,
    )

    assert stats["first_cik_processed"] == "0001084869"
    assert stats["last_cik_processed"] == "0001084869"
    assert stats["websites_imported"] == 1
    assert stats["ready_candidates"] == [
        {
            "company_id": company_id,
            "company_name": "Flowers",
            "sec_cik": "0001084869",
            "website_url": "https://www.1800flowers.com/",
            "filing_form": "10-K",
            "filing_date": "2025-09-12",
            "accession_number": "0001084869-25-000017",
            "source_url": (
                "https://www.sec.gov/Archives/edgar/data/1084869/"
                "000108486925000017/flws-20250629.htm"
            ),
            "evidence_text": stats["ready_candidates"][0]["evidence_text"],
        }
    ]
    assert stats["ready_candidates_truncated"] == 0
    assert repository.find_company_by_normalized_name("Flowers")["website_url"] == (
        "https://www.1800flowers.com/"
    )
    event = repository.company_coverage_events(company_id)[0]
    assert event["reason"].startswith("Canonical website seed verified from SEC filing")
    assert "exact CIK 0001084869" in event["reason"]
    assert "accession 0001084869-25-000017" in event["reason"]
    assert "flws-20250629.htm" in event["reason"]


def test_concurrency_uses_thread_sessions_and_keeps_cik_order():
    payloads = {
        "0001084869": submissions(),
        "0002000000": submissions("0002000000", "SECOND COMPANY"),
    }
    annual = (FIXTURES / "sec_filing_annual.html").read_bytes()
    lock = Lock()

    class ConcurrentSession:
        def get(self, url, **_kwargs):
            cik = "0001084869" if "1084869" in url else "0002000000"
            with lock:
                if "submissions" in url:
                    return json_response(payloads[cik])
                return html_response(annual)

    client = SecFilingWebsiteClient(
        user_agent="OpenRole-FilingBot/0.1 ops@example.org",
        requests_per_second=10,
        concurrency=2,
        session_factory=ConcurrentSession,
        sleep=lambda _seconds: None,
    )

    result = client.query([2000000, 1084869])

    assert [candidate.sec_cik for candidate in result.candidates] == [
        "0001084869",
        "0002000000",
    ]
    assert result.requests_made == 4


def test_dry_run_returns_reviewable_candidate_without_writing(tmp_path):
    repository = JobRepository(tmp_path / "filing-dry-run.db")
    repository.initialize()
    repository.upsert_company("Flowers", sec_cik=1084869)
    client = client_for(
        json_response(submissions()),
        html_response((FIXTURES / "sec_filing_annual.html").read_bytes()),
    )

    stats = import_sec_filing_company_websites(
        repository, client, actor="reviewer@example.org", dry_run=True
    )

    assert stats["websites_ready"] == 1
    assert stats["websites_imported"] == 0
    assert stats["ready_candidates"][0]["website_url"] == "https://www.1800flowers.com/"
    assert "Company’s internet address is" in stats["ready_candidates"][0]["evidence_text"]
    assert repository.find_company_by_normalized_name("Flowers")["website_url"] == ""
