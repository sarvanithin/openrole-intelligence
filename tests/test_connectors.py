from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
from email.utils import format_datetime

import pytest
import requests

from fortune_intel.connectors import (
    AmazonJobsConnector,
    AppleJobsConnector,
    AshbyConnector,
    GreenhouseConnector,
    HttpFailure,
    JsonHttpClient,
    LeverConnector,
    SmartRecruitersConnector,
    build_connector,
)


class StubClient:
    def __init__(self, responses):
        self.responses = deque(responses)
        self.calls = []

    def get_json(self, url, *, params=None):
        self.calls.append((url, params))
        if not self.responses:
            raise AssertionError(f"unexpected request: {url}")
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response

    def post_json(self, url, *, json_body):
        self.calls.append((url, json_body))
        if not self.responses:
            raise AssertionError(f"unexpected request: {url}")
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response


class StubTextClient:
    def __init__(self, responses):
        self.responses = deque(responses)
        self.calls = []

    def get_text(self, url, *, max_bytes=2_000_000):
        self.calls.append((url, max_bytes))
        if not self.responses:
            raise AssertionError(f"unexpected request: {url}")
        return self.responses.popleft()


class FakeResponse:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self.payload = payload
        self.headers = headers or {}

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = deque(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response

    def post(self, url, **kwargs):
        return self.get(url, **kwargs)


class FakeClock:
    def __init__(self, now=0.0):
        self.now = now
        self.sleeps = []

    def monotonic(self):
        return self.now

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_http_client_retries_with_bounded_exponential_backoff():
    clock = FakeClock()
    session = FakeSession(
        [
            requests.Timeout("slow"),
            FakeResponse(503),
            FakeResponse(200, {"ok": True}),
        ]
    )
    client = JsonHttpClient(
        session=session,
        max_attempts=3,
        backoff_seconds=0.25,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    assert client.get_json("https://api.example.test/jobs") == {"ok": True}
    assert clock.sleeps == [0.25, 0.5]
    assert all(call[1]["timeout"] == (5.0, 30.0) for call in session.calls)
    assert all(call[1]["allow_redirects"] is False for call in session.calls)


def test_http_client_does_not_retry_client_errors():
    session = FakeSession([FakeResponse(404)])
    client = JsonHttpClient(session=session, max_attempts=3, sleep=lambda _: None)

    with pytest.raises(HttpFailure) as captured:
        client.get_json("https://api.example.test/missing")

    assert captured.value.status_code == 404
    assert captured.value.retryable is False
    assert len(session.calls) == 1


def test_http_client_posts_json_without_following_redirects():
    session = FakeSession([FakeResponse(200, {"total": 0})])
    client = JsonHttpClient(session=session)

    payload = client.post_json(
        "https://tenant.wd5.myworkdayjobs.com/wday/cxs/tenant/site/jobs",
        json_body={"limit": 20, "offset": 0},
    )

    assert payload == {"total": 0}
    assert session.calls[0][1]["json"] == {"limit": 20, "offset": 0}
    assert session.calls[0][1]["allow_redirects"] is False


@pytest.mark.parametrize("status", [302, 303])
def test_http_client_retries_transient_redirect_at_original_fixed_url(status):
    clock = FakeClock()
    original_url = (
        f"https://redirect-{status}.wd5.myworkdayjobs.com/"
        f"wday/cxs/redirect-{status}/site/jobs"
    )
    session = FakeSession(
        [
            FakeResponse(status, headers={"Location": "https://attacker.example/jobs"}),
            FakeResponse(200, {"total": 0}),
        ]
    )
    client = JsonHttpClient(
        session=session,
        max_attempts=2,
        backoff_seconds=0.25,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    assert client.post_json(original_url, json_body={"limit": 20}) == {"total": 0}
    assert [call[0] for call in session.calls] == [original_url, original_url]
    assert all(call[1]["allow_redirects"] is False for call in session.calls)
    assert clock.sleeps == [0.25]


def test_http_client_does_not_retry_redirect_from_other_ats_hosts():
    session = FakeSession(
        [FakeResponse(302, headers={"Location": "https://attacker.example/jobs"})]
    )
    client = JsonHttpClient(session=session, max_attempts=3, sleep=lambda _: None)

    with pytest.raises(HttpFailure) as captured:
        client.get_json("https://api.example.test/jobs")

    assert captured.value.status_code == 302
    assert captured.value.retryable is False
    assert len(session.calls) == 1
    assert session.calls[0][1]["allow_redirects"] is False


@pytest.mark.parametrize("status", [429, 503])
def test_http_client_honors_bounded_retry_after(status):
    clock = FakeClock()
    session = FakeSession(
        [
            FakeResponse(status, headers={"Retry-After": "600"}),
            FakeResponse(200, {"ok": True}),
        ]
    )
    client = JsonHttpClient(
        session=session,
        max_attempts=2,
        backoff_seconds=0.25,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        wall_time=clock.time,
    )

    assert client.get_json(f"https://retry-{status}.example.test/jobs") == {"ok": True}
    assert clock.sleeps == [60.0]


def test_http_client_honors_retry_after_http_date():
    clock = FakeClock(now=1_700_000_000.0)
    retry_at = datetime.fromtimestamp(clock.now + 7, UTC)
    session = FakeSession(
        [
            FakeResponse(503, headers={"Retry-After": format_datetime(retry_at, usegmt=True)}),
            FakeResponse(200, {"ok": True}),
        ]
    )
    client = JsonHttpClient(
        session=session,
        max_attempts=2,
        backoff_seconds=0.25,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        wall_time=clock.time,
    )

    assert client.get_json("https://retry-date.example.test/jobs") == {"ok": True}
    assert clock.sleeps == [7.0]


def test_greenhouse_returns_native_id_description_and_updated_timestamp():
    client = StubClient(
        [
            {
                "jobs": [
                    {
                        "id": 127817,
                        "internal_job_id": 144381,
                        "title": "Data Engineer",
                        "updated_at": "2026-08-01T10:55:28-05:00",
                        "location": {"name": "New York, NY"},
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/127817?gh_jid=127817",
                        "content": "&lt;p&gt;Build &amp;amp; ship pipelines.&lt;/p&gt;",
                    }
                ],
                "meta": {"total": 1},
            }
        ]
    )

    result = GreenhouseConnector("acme", client=client).fetch()

    assert result.complete is True
    assert result.pages_fetched == 1
    assert result.jobs[0].external_job_id == "127817"
    assert result.jobs[0].description == "Build & ship pipelines."
    assert result.jobs[0].source_opened_at is None
    assert result.jobs[0].source_updated_at == "2026-08-01T15:55:28+00:00"
    assert result.jobs[0].metadata["source_opened_at_field"] is None
    assert result.jobs[0].metadata["source_opened_at_available"] is False
    assert client.calls[0][1] == {"content": "true"}


def test_greenhouse_schema_error_makes_manifest_incomplete_but_keeps_valid_jobs():
    client = StubClient(
        [
            {
                "jobs": [
                    {
                        "id": "good",
                        "title": "Engineer",
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/good",
                    },
                    {"id": "bad", "title": "Missing URL"},
                ]
            }
        ]
    )

    result = GreenhouseConnector("acme", client=client).fetch()

    assert result.complete is False
    assert [job.external_job_id for job in result.jobs] == ["good"]
    assert result.errors[0].external_job_id == "bad"


def test_greenhouse_maze_shape_does_not_invent_missing_or_relative_job_urls():
    """Reproduce Maze's zero-job probe without deriving undocumented URLs."""

    client = StubClient(
        [
            {
                "jobs": [
                    {
                        "id": 1001,
                        "title": "Scientist",
                        "location": {"name": "South San Francisco, CA"},
                    },
                    {
                        "id": 1002,
                        "title": "Research Associate",
                        "absolute_url": "/mazetherapeutics/jobs/1002",
                        "location": {"name": "South San Francisco, CA"},
                    },
                ],
                "meta": {"total": 2},
            }
        ]
    )

    result = GreenhouseConnector("mazetherapeutics", client=client).fetch()

    assert result.complete is False
    assert result.jobs == ()
    assert [error.external_job_id for error in result.errors] == ["1001", "1002"]
    assert all("absolute HTTPS URL" in error.message for error in result.errors)
    assert client.calls == [
        (
            "https://boards-api.greenhouse.io/v1/boards/mazetherapeutics/jobs",
            {"content": "true"},
        )
    ]


def test_greenhouse_duplicate_native_id_makes_manifest_incomplete():
    duplicate = {
        "id": "same",
        "title": "Engineer",
        "absolute_url": "https://boards.greenhouse.io/acme/jobs/same",
    }
    result = GreenhouseConnector(
        "acme", client=StubClient([{"jobs": [duplicate, duplicate]}])
    ).fetch()

    assert result.complete is False
    assert len(result.jobs) == 1
    assert "duplicate native job ID" in result.errors[0].message


def lever_job(identifier: str, *, created_at: int = 1_722_510_000_000):
    return {
        "id": identifier,
        "text": f"Engineer {identifier}",
        "hostedUrl": f"https://jobs.lever.co/acme/{identifier}",
        "applyUrl": f"https://jobs.lever.co/acme/{identifier}/apply",
        "descriptionPlain": f"Description {identifier}",
        "createdAt": created_at,
        "categories": {
            "location": "Boston, MA",
            "allLocations": ["Boston, MA"],
            "team": "Data",
        },
        "workplaceType": "hybrid",
    }


def test_lever_paginates_until_short_page_and_preserves_native_ids():
    client = StubClient([[lever_job("one"), lever_job("two")], [lever_job("three")]])

    result = LeverConnector("acme", page_size=2, client=client).fetch()

    assert result.complete is True
    assert result.pages_fetched == 2
    assert [job.external_job_id for job in result.jobs] == ["one", "two", "three"]
    assert result.jobs[0].source_opened_at == "2024-08-01T11:00:00+00:00"
    assert result.jobs[0].metadata["source_opened_at_field"] == "createdAt"
    assert result.jobs[0].metadata["source_opened_at_available"] is True
    assert client.calls[0][1]["skip"] == 0
    assert client.calls[1][1]["skip"] == 2


def test_lever_later_page_failure_returns_partial_incomplete_manifest():
    failure = HttpFailure("timeout", "timed out", "https://api.lever.co", True, 3)
    client = StubClient([[lever_job("one")], failure])

    result = LeverConnector("acme", page_size=1, client=client).fetch()

    assert result.complete is False
    assert [job.external_job_id for job in result.jobs] == ["one"]
    assert result.errors[0].code == "timeout"
    assert result.errors[0].page == 2


def test_ashby_uses_native_job_url_token_and_full_public_payload():
    client = StubClient(
        [
            {
                "apiVersion": "1",
                "jobs": [
                    {
                        "title": "Product Data Scientist",
                        "location": "Remote — United States",
                        "descriptionPlain": "Design experiments.",
                        "publishedAt": "2026-07-24T12:00:00Z",
                        "jobUrl": "https://jobs.ashbyhq.com/acme/63f35f2d-49be",
                        "applyUrl": "https://jobs.ashbyhq.com/acme/63f35f2d-49be/application",
                        "isRemote": True,
                        "workplaceType": "Remote",
                        "employmentType": "FullTime",
                        "compensation": {"scrapeableCompensationSalarySummary": "$120K"},
                    }
                ],
            }
        ]
    )

    result = AshbyConnector("acme", client=client).fetch()

    assert result.complete is True
    assert result.jobs[0].external_job_id == "63f35f2d-49be"
    assert result.jobs[0].source_opened_at == "2026-07-24T12:00:00+00:00"
    assert result.jobs[0].metadata["source_opened_at_field"] == "publishedAt"
    assert result.jobs[0].metadata["source_opened_at_available"] is True
    assert result.jobs[0].metadata["api_version"] == "1"
    assert result.jobs[0].metadata["compensation"] == {
        "scrapeableCompensationSalarySummary": "$120K"
    }


def smart_summary(identifier: str):
    return {
        "id": identifier,
        "name": f"Role {identifier}",
        "releasedDate": "2026-08-01T10:00:00Z",
        "location": {"city": "Austin", "region": "TX", "country": "US"},
    }


def smart_detail(identifier: str):
    return {
        "id": identifier,
        "uuid": f"uuid-{identifier}",
        "name": f"Role {identifier}",
        "releasedDate": "2026-08-01T10:00:00Z",
        "lastActivityOn": "2026-08-02T11:00:00Z",
        "applyUrl": f"https://jobs.smartrecruiters.com/acme/{identifier}/apply",
        "location": {"city": "Austin", "region": "TX", "country": "US", "remote": True},
        "jobAd": {
            "sections": {
                "jobDescription": {"text": "<p>Build APIs.</p>"},
                "qualifications": {"text": "<ul><li>Python</li></ul>"},
            }
        },
        "department": {"label": "Engineering"},
    }


def test_smartrecruiters_paginates_and_fetches_each_description():
    client = StubClient(
        [
            {"totalFound": 2, "content": [smart_summary("one")]},
            smart_detail("one"),
            {"totalFound": 2, "content": [smart_summary("two")]},
            smart_detail("two"),
        ]
    )

    result = SmartRecruitersConnector("acme", page_size=1, client=client).fetch()

    assert result.complete is True
    assert result.pages_fetched == 2
    assert [job.external_job_id for job in result.jobs] == ["one", "two"]
    assert result.jobs[0].description == "Build APIs.\n\nPython"
    assert result.jobs[0].location == "Remote, Austin, TX, US"
    assert result.jobs[0].source_updated_at == "2026-08-02T11:00:00+00:00"
    assert result.jobs[0].source_opened_at == "2026-08-01T10:00:00+00:00"
    assert result.jobs[0].metadata["source_opened_at_field"] == "releasedDate"
    assert result.jobs[0].metadata["source_opened_at_available"] is True
    assert client.calls[0][1] == {"limit": 1, "offset": 0}
    assert client.calls[2][1] == {"limit": 1, "offset": 1}


def test_smartrecruiters_detail_failure_is_explicitly_incomplete():
    failure = HttpFailure("http_error", "HTTP 503", "https://api.smartrecruiters.com", True, 3, 503)
    client = StubClient(
        [
            {"totalFound": 2, "content": [smart_summary("one"), smart_summary("two")]},
            smart_detail("one"),
            failure,
        ]
    )

    result = SmartRecruitersConnector("acme", client=client).fetch()

    assert result.complete is False
    assert [job.external_job_id for job in result.jobs] == ["one"]
    assert result.errors[0].external_job_id == "two"
    assert result.errors[0].retryable is True


def test_connectors_percent_encode_source_keys_without_changing_hosts():
    greenhouse = StubClient([{"jobs": []}])
    ashby = StubClient([{"jobs": []}])

    GreenhouseConnector("unsafe/name", client=greenhouse).fetch()
    AshbyConnector("unsafe/name", client=ashby).fetch()

    assert greenhouse.calls[0][0].startswith("https://boards-api.greenhouse.io/")
    assert greenhouse.calls[0][0].endswith("unsafe%2Fname/jobs")
    assert ashby.calls[0][0].startswith("https://api.ashbyhq.com/")
    assert ashby.calls[0][0].endswith("unsafe%2Fname")


def _amazon_job(identifier: str, *, country: str = "USA"):
    return {
        "id": f"uuid-{identifier}",
        "id_icims": identifier,
        "title": f"Role {identifier}",
        "job_path": f"/en/jobs/{identifier}/role-{identifier}",
        "country_code": country,
        "location": "US, WA, Seattle",
        "description": "<p>Build reliable systems.</p>",
        "posted_date": "August 16, 2026",
    }


def test_amazon_jobs_connector_paginates_the_public_us_manifest():
    client = StubClient(
        [
            {"hits": 2, "jobs": [_amazon_job("one")]},
            {"hits": 2, "jobs": [_amazon_job("two")]},
        ]
    )

    result = AmazonJobsConnector(page_size=1, client=client).fetch()

    assert result.complete is True
    assert result.pages_fetched == 2
    assert [job.external_job_id for job in result.jobs] == ["one", "two"]
    assert result.jobs[0].url == "https://www.amazon.jobs/en/jobs/one/role-one"
    assert result.jobs[0].description == "Build reliable systems."
    assert result.jobs[0].source_opened_at == "2026-08-16T00:00:00+00:00"
    assert client.calls == [
        (
            "https://www.amazon.jobs/en/search.json",
            {"country": "USA", "offset": 0, "result_limit": 1},
        ),
        (
            "https://www.amazon.jobs/en/search.json",
            {"country": "USA", "offset": 1, "result_limit": 1},
        ),
    ]


def test_amazon_jobs_connector_rejects_non_us_records_and_incomplete_manifest():
    client = StubClient([{"hits": 1, "jobs": [_amazon_job("one", country="CAN")]}])

    result = AmazonJobsConnector(client=client).fetch()

    assert result.complete is False
    assert result.jobs == ()
    assert result.errors[0].external_job_id == "one"


def _apple_page(records, total):
    import json

    payload = {"loaderData": {"search": {"searchResults": records, "totalRecords": total}}}
    return f"<script>window.__staticRouterHydrationData = JSON.parse({json.dumps(json.dumps(payload))})</script>"


def _apple_job(identifier: str, *, country="United States of America"):
    return {
        "positionId": identifier,
        "postingTitle": f"Apple Role {identifier}",
        "transformedPostingTitle": f"apple-role-{identifier}",
        "jobSummary": "Build thoughtful products.",
        "postDateInGMT": "2026-08-16T07:03:46.000Z",
        "postingDate": "Aug 16, 2026",
        "type": "Regular",
        "locations": [{"postLocationId": "postLocation-USA", "city": "Cupertino", "stateProvince": "CA", "countryName": country, "countryID": "iso-country-USA" if country.startswith("United States") else "iso-country-CAN"}],
    }


def test_apple_jobs_connector_reads_complete_public_us_pages():
    client = StubTextClient([_apple_page([_apple_job("one"), _apple_job("two")], 2)])

    result = AppleJobsConnector(client=client).fetch()

    assert result.complete is True
    assert result.pages_fetched == 1
    assert [job.external_job_id for job in result.jobs] == ["one:postLocation-USA", "two:postLocation-USA"]
    assert result.jobs[0].url == "https://jobs.apple.com/en-us/details/one/apple-role-one"
    assert result.jobs[0].source_opened_at == "2026-08-16T07:03:46+00:00"
    assert client.calls[0][0].endswith("location=united-states-USA")


def test_apple_jobs_connector_rejects_non_us_records():
    client = StubTextClient([_apple_page([_apple_job("one", country="Canada")], 1)])

    result = AppleJobsConnector(client=client).fetch()

    assert result.complete is False
    assert result.jobs == ()
    assert result.errors[0].external_job_id == "one"


@pytest.mark.parametrize(
    ("kind", "connector_type"),
    [
        ("amazon-jobs", AmazonJobsConnector),
        ("apple-jobs", AppleJobsConnector),
        ("greenhouse", GreenhouseConnector),
        ("Lever", LeverConnector),
        ("ashby", AshbyConnector),
        ("smart-recruiters", SmartRecruitersConnector),
    ],
)
def test_build_connector_supports_launch_ats_kinds(kind, connector_type):
    client = StubClient([])

    connector = build_connector(
        kind, "us" if connector_type in {AmazonJobsConnector, AppleJobsConnector} else "acme", client=client
    )

    assert isinstance(connector, connector_type)
    assert connector.client is client


def test_build_connector_rejects_unsupported_kind():
    with pytest.raises(ValueError, match="unsupported connector kind"):
        build_connector("not-a-real-ats", "acme", client=StubClient([]))
