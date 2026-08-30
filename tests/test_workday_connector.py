from __future__ import annotations

import json
from collections import deque
from pathlib import Path

import pytest

from fortune_intel.connectors import (
    HttpFailure,
    WorkdayConnector,
    build_connector,
    workday_source,
)

FIXTURES = Path(__file__).parent / "fixtures"


class StubClient:
    def __init__(self, responses):
        self.responses = deque(responses)
        self.calls = []

    def _response(self, url, arguments):
        self.calls.append((url, arguments))
        if not self.responses:
            raise AssertionError(f"unexpected request: {url}")
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response

    def get_json(self, url, *, params=None):
        return self._response(url, params)

    def post_json(self, url, *, json_body):
        return self._response(url, json_body)


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def workday_summary(identifier: str):
    return {
        "title": f"Role {identifier}",
        "externalPath": f"/job/US-NY/Role-{identifier}_{identifier}",
        "postedOn": "Posted Today",
        "bulletFields": [identifier],
    }


def workday_detail(identifier: str):
    return {
        "jobPostingInfo": {
            "id": f"opaque-{identifier}",
            "title": f"Role {identifier}",
            "jobDescription": f"<p>Description {identifier}</p>",
            "location": "United States, NY",
            "additionalLocations": [],
            "startDate": "2026-08-07",
            "jobReqId": identifier,
            "jobPostingId": f"Role-{identifier}_{identifier}",
            "jobPostingSiteId": "External",
            "externalUrl": (
                "https://acme.wd5.myworkdayjobs.com/External/"
                f"job/US-NY/Role-{identifier}_{identifier}"
            ),
        }
    }


def workdaysite_detail(identifier: str):
    detail = workday_detail(identifier)
    detail["jobPostingInfo"]["externalUrl"] = (
        "https://wd1.myworkdaysite.com/recruiting/snapchat/snap/"
        f"job/US-NY/Role-{identifier}_{identifier}"
    )
    return detail


def test_recruiting_path_source_builds_exact_public_and_cxs_urls():
    source = workday_source("wd1.myworkdaysite.com", "snapchat", "snap")

    assert source.key == "wd1.myworkdaysite.com|snapchat|snap"
    assert source.public_base_url == ("https://wd1.myworkdaysite.com/recruiting/snapchat/snap")
    assert source.cxs_base_url == ("https://wd1.myworkdaysite.com/wday/cxs/snapchat/snap")
    assert source.public_job_path_prefix == "/recruiting/snapchat/snap/job/"


def test_recruiting_path_source_fetches_manifest_and_detail_from_same_fixed_host():
    client = StubClient(
        [
            {"total": 1, "jobPostings": [workday_summary("JR-1")]},
            workdaysite_detail("JR-1"),
        ]
    )
    key = workday_source("wd1.myworkdaysite.com", "snapchat", "snap").key

    result = WorkdayConnector(key, client=client).fetch()

    assert result.complete is True
    assert result.source_key == "wd1.myworkdaysite.com|snapchat|snap"
    assert result.jobs[0].url.startswith(
        "https://wd1.myworkdaysite.com/recruiting/snapchat/snap/job/"
    )
    assert client.calls[0][0] == ("https://wd1.myworkdaysite.com/wday/cxs/snapchat/snap/jobs")
    assert client.calls[1][0] == (
        "https://wd1.myworkdaysite.com/wday/cxs/snapchat/snap/job/US-NY/Role-JR-1_JR-1"
    )


def test_accepts_workday_canonical_public_alias_for_same_tenant_and_site():
    """A tenant API host can return the same board's canonical apply URL."""

    detail = workday_detail("JR-1")
    detail["jobPostingInfo"]["externalUrl"] = (
        "https://wd5.myworkdaysite.com/recruiting/acme/External/job/US-NY/Role-JR-1_JR-1"
    )
    client = StubClient([{"total": 1, "jobPostings": [workday_summary("JR-1")]}, detail])
    key = workday_source("acme.wd5.myworkdayjobs.com", "acme", "External").key

    result = WorkdayConnector(key, client=client).fetch()

    assert result.complete is True
    assert result.jobs[0].url.startswith(
        "https://wd5.myworkdaysite.com/recruiting/acme/External/job/"
    )


def test_accepts_case_variant_of_same_workday_site_path():
    """Workday can capitalize a site path differently from its board URL."""

    detail = workday_detail("JR-1")
    detail["jobPostingInfo"]["externalUrl"] = (
        "https://acme.wd5.myworkdayjobs.com/EXTERNAL/job/US-NY/Role-JR-1_JR-1"
    )
    client = StubClient([{"total": 1, "jobPostings": [workday_summary("JR-1")]}, detail])
    key = workday_source("acme.wd5.myworkdayjobs.com", "acme", "External").key

    result = WorkdayConnector(key, client=client).fetch()

    assert result.complete is True
    assert result.jobs[0].url.endswith("/EXTERNAL/job/US-NY/Role-JR-1_JR-1")


def test_fixture_preserves_exact_posting_date_and_native_id():
    client = StubClient(
        [
            load_fixture("workday_jobs_page.json"),
            load_fixture("workday_job_detail.json"),
        ]
    )
    key = workday_source("workday.wd5.myworkdayjobs.com", "workday", "Workday").key

    result = WorkdayConnector(key, client=client).fetch()

    assert result.complete is True
    assert result.pages_fetched == 1
    assert result.jobs[0].external_job_id == "37948d47057610009d0b1063dfd20000"
    assert result.jobs[0].source_opened_at == "2026-08-07T00:00:00+00:00"
    assert result.jobs[0].source_updated_at is None
    assert result.jobs[0].description == "Build & ship reliable services."
    assert result.jobs[0].location == "Canada, BC, Vancouver, Canada, ON, Toronto"
    assert result.jobs[0].metadata["source_opened_at_field"] == "startDate"
    assert result.jobs[0].metadata["job_requisition_id"] == "JR-0108831"
    assert result.jobs[0].metadata["hiring_organization"] == "Canada Workday ULC"
    assert client.calls[0][1] == {
        "appliedFacets": {},
        "limit": 20,
        "offset": 0,
        "searchText": "",
    }


def test_paginates_by_reported_total_and_fetches_each_detail():
    first = workday_summary("JR-1")
    second = workday_summary("JR-2")
    client = StubClient(
        [
            {"total": 2, "jobPostings": [first]},
            {"total": 2, "jobPostings": [second]},
            workday_detail("JR-1"),
            workday_detail("JR-2"),
        ]
    )
    key = workday_source("acme.wd5.myworkdayjobs.com", "acme", "External").key

    result = WorkdayConnector(key, page_size=1, client=client).fetch()

    assert result.complete is True
    assert result.pages_fetched == 2
    assert [job.external_job_id for job in result.jobs] == ["opaque-JR-1", "opaque-JR-2"]
    assert client.calls[0][1]["offset"] == 0
    assert client.calls[1][1]["offset"] == 1


def test_probes_a_large_reported_total_boundary_before_marking_complete():
    client = StubClient(
        [
            {"total": 2, "jobPostings": [workday_summary("JR-1")]},
            {"total": 2, "jobPostings": [workday_summary("JR-2")]},
            {"total": 2, "jobPostings": []},
            workday_detail("JR-1"),
            workday_detail("JR-2"),
        ]
    )
    key = workday_source("acme.wd5.myworkdayjobs.com", "acme", "External").key

    result = WorkdayConnector(
        key,
        page_size=1,
        reported_total_probe_threshold=2,
        detail_concurrency=1,
        client=client,
    ).fetch()

    assert result.complete is True
    assert result.pages_fetched == 3
    assert [call[1]["offset"] for call in client.calls[:3]] == [0, 1, 2]


def test_continues_when_a_large_workday_reported_total_is_capped():
    client = StubClient(
        [
            {"total": 2, "jobPostings": [workday_summary("JR-1")]},
            {"total": 2, "jobPostings": [workday_summary("JR-2")]},
            {"total": 2, "jobPostings": [workday_summary("JR-3")]},
            {"total": 2, "jobPostings": []},
            workday_detail("JR-1"),
            workday_detail("JR-2"),
            workday_detail("JR-3"),
        ]
    )
    key = workday_source("acme.wd5.myworkdayjobs.com", "acme", "External").key

    result = WorkdayConnector(
        key,
        page_size=1,
        reported_total_probe_threshold=2,
        detail_concurrency=1,
        client=client,
    ).fetch()

    assert result.complete is True
    assert result.pages_fetched == 4
    assert [job.external_job_id for job in result.jobs] == [
        "opaque-JR-1",
        "opaque-JR-2",
        "opaque-JR-3",
    ]
    assert [call[1]["offset"] for call in client.calls[:4]] == [0, 1, 2, 3]


def test_later_page_failure_stops_before_fetching_details():
    failure = HttpFailure("timeout", "timed out", "https://acme.wd5.myworkdayjobs.com", True, 3)
    client = StubClient(
        [
            {"total": 2, "jobPostings": [workday_summary("JR-1")]},
            failure,
        ]
    )
    key = workday_source("acme.wd5.myworkdayjobs.com", "acme", "External").key

    result = WorkdayConnector(key, page_size=1, client=client).fetch()

    assert result.complete is False
    assert result.jobs == ()
    assert result.errors[0].code == "timeout"
    assert result.errors[0].page == 2


@pytest.mark.parametrize("status", [404, 410, 422])
def test_rejected_listing_endpoint_requires_official_source_reverification(status):
    failure = HttpFailure(
        "http_error",
        f"ATS endpoint returned HTTP {status}",
        "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External/jobs",
        False,
        1,
        status,
    )
    key = workday_source("acme.wd5.myworkdayjobs.com", "acme", "External").key

    result = WorkdayConnector(key, client=StubClient([failure])).fetch()

    assert result.complete is False
    assert result.jobs == ()
    assert result.errors[0].code == "source_configuration_error"
    assert result.errors[0].retryable is False
    assert result.errors[0].page == 1
    assert "official company page" in result.errors[0].message


def test_marks_manifest_incomplete_if_total_changes_during_pagination():
    client = StubClient(
        [
            {"total": 2, "jobPostings": [workday_summary("JR-1")]},
            {"total": 3, "jobPostings": [workday_summary("JR-2")]},
        ]
    )
    key = workday_source("acme.wd5.myworkdayjobs.com", "acme", "External").key

    result = WorkdayConnector(key, page_size=1, client=client).fetch()

    assert result.complete is False
    assert "total changed" in result.errors[0].message


def test_accepts_zero_total_sentinel_after_first_page():
    client = StubClient(
        [
            {"total": 2, "jobPostings": [workday_summary("JR-1")]},
            {"total": 0, "jobPostings": [workday_summary("JR-2")]},
            workday_detail("JR-1"),
            workday_detail("JR-2"),
        ]
    )
    key = workday_source("acme.wd5.myworkdayjobs.com", "acme", "External").key

    result = WorkdayConnector(key, page_size=1, client=client).fetch()

    assert result.complete is True
    assert result.pages_fetched == 2
    assert [job.external_job_id for job in result.jobs] == ["opaque-JR-1", "opaque-JR-2"]


def test_counts_requisition_only_placeholder_without_inventing_a_job_url():
    client = StubClient(
        [
            {
                "total": 2,
                "jobPostings": [workday_summary("JR-1"), {"bulletFields": ["JR-GHOST"]}],
            },
            workday_detail("JR-1"),
        ]
    )
    key = workday_source("acme.wd5.myworkdayjobs.com", "acme", "External").key

    result = WorkdayConnector(key, client=client).fetch()

    assert result.complete is True
    assert [job.external_job_id for job in result.jobs] == ["opaque-JR-1"]
    assert result.errors == ()


@pytest.mark.parametrize(
    "malformed_summary",
    [
        {"title": "Real role", "bulletFields": ["JR-1"]},
        {"bulletFields": []},
        {},
    ],
)
def test_does_not_treat_other_missing_path_rows_as_placeholders(malformed_summary):
    client = StubClient([{"total": 1, "jobPostings": [malformed_summary]}])
    key = workday_source("acme.wd5.myworkdayjobs.com", "acme", "External").key

    result = WorkdayConnector(key, client=client).fetch()

    assert result.complete is False
    assert result.jobs == ()
    assert "externalPath" in result.errors[0].message


@pytest.mark.parametrize(
    "key",
    [
        "attacker.example|acme|External",
        "acme.wd5.myworkdayjobs.com|other|External",
        "acme.wd5.myworkdayjobs.com|acme|../External",
        "snapchat.wd1.myworkdaysite.com|snapchat|snap",
        "wd1.myworkdaysite.com|../snapchat|snap",
        "wd1.myworkdaysite.com|snapchat|../snap",
    ],
)
def test_rejects_unsafe_or_mismatched_source_keys(key):
    with pytest.raises(ValueError):
        WorkdayConnector(key, client=StubClient([]))


def test_rejects_cross_host_job_urls_without_following_them():
    detail = workday_detail("JR-1")
    detail["jobPostingInfo"]["externalUrl"] = "https://attacker.example/External/job/JR-1"
    client = StubClient([{"total": 1, "jobPostings": [workday_summary("JR-1")]}, detail])
    key = workday_source("acme.wd5.myworkdayjobs.com", "acme", "External").key

    result = WorkdayConnector(key, client=client).fetch()

    assert result.complete is False
    assert result.jobs == ()
    assert "configured Workday site" in result.errors[0].message


@pytest.mark.parametrize(
    "external_url",
    [
        "https://wd1.myworkdaysite.com/recruiting/other/snap/job/US-NY/Role-JR-1_JR-1",
        "https://wd1.myworkdaysite.com/recruiting/snapchat/other/job/US-NY/Role-JR-1_JR-1",
        "https://wd1.myworkdaysite.com:443/recruiting/snapchat/snap/job/Role-JR-1",
        "https://wd1.myworkdaysite.com/recruiting/snapchat/snap/job/Role-JR-1?source=x",
        "https://wd1.myworkdaysite.com/recruiting/snapchat/snap/job/Role-JR-1#apply",
        "https://wd1.myworkdaysite.com/recruiting/snapchat/snap/job/Role-JR-1?",
        "https://wd1.myworkdaysite.com/recruiting/snapchat/snap/job/Role-JR-1#",
        "https://wd1.myworkdaysite.com/recruiting/snapchat/snap/job//Role-JR-1",
        "https://wd1.myworkdaysite.com/recruiting/snapchat/snap/job/%2e%2e/private",
    ],
)
def test_recruiting_path_source_rejects_mismatched_or_ambiguous_job_urls(external_url):
    detail = workdaysite_detail("JR-1")
    detail["jobPostingInfo"]["externalUrl"] = external_url
    client = StubClient([{"total": 1, "jobPostings": [workday_summary("JR-1")]}, detail])
    key = workday_source("wd1.myworkdaysite.com", "snapchat", "snap").key

    result = WorkdayConnector(key, client=client).fetch()

    assert result.complete is False
    assert result.jobs == ()
    assert "configured Workday site" in result.errors[0].message


def test_factory_supports_workday_source_keys():
    client = StubClient([])
    key = workday_source("acme.wd5.myworkdayjobs.com", "acme", "External").key

    connector = build_connector("Work-day", key, client=client)

    assert isinstance(connector, WorkdayConnector)
    assert connector.client is client
