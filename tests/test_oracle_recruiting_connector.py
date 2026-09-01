from __future__ import annotations

import json
from collections import deque
from pathlib import Path

import pytest

from fortune_intel.connectors import (
    HttpFailure,
    OracleRecruitingConnector,
    build_connector,
    oracle_recruiting_source,
)

FIXTURES = Path(__file__).parent / "fixtures"


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


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def listing(identifier: str, offset: int, total: int):
    return {
        "items": [
            {
                "Offset": offset,
                "Limit": 1,
                "TotalJobsCount": total,
                "SiteNumber": "CX_1",
                "requisitionList": [
                    {
                        "Id": identifier,
                        "Title": f"Role {identifier}",
                        "PostedDate": "2026-08-07",
                        "PrimaryLocation": "Austin, TX, United States",
                    }
                ],
            }
        ]
    }


def detail(identifier: str):
    return {
        "items": [
            {
                "Id": identifier,
                "Title": f"Role {identifier}",
                "ExternalPostedStartDate": "2026-08-07T10:15:00+00:00",
                "ExternalDescriptionStr": f"<p>Description {identifier}</p>",
                "PrimaryLocation": "Austin, TX, United States",
            }
        ]
    }


def test_fixture_preserves_exact_opening_timestamp_and_full_description():
    client = StubClient(
        [
            load_fixture("oracle_recruiting_jobs_page.json"),
            load_fixture("oracle_recruiting_job_detail.json"),
        ]
    )
    key = oracle_recruiting_source("edxn.fa.us2.oraclecloud.com", "en", "CX_4001").key

    result = OracleRecruitingConnector(key, client=client, detail_concurrency=1).fetch()

    assert result.complete is True
    assert result.pages_fetched == 1
    assert result.jobs[0].external_job_id == "26069"
    assert result.jobs[0].source_opened_at == "2026-08-07T13:10:20+00:00"
    assert result.jobs[0].source_updated_at is None
    assert result.jobs[0].description == ("Oversee engineering operations. Build reliable systems.")
    assert result.jobs[0].location == ("Boston, MA, United States, New York, NY, United States")
    assert result.jobs[0].url == (
        "https://edxn.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_4001/job/26069"
    )
    assert result.jobs[0].metadata["source_opened_at_field"] == ("ExternalPostedStartDate")
    assert "finder=findReqs;siteNumber=CX_4001,limit=100,offset=0" in client.calls[0][0]
    assert "finder=ById;Id=26069,siteNumber=CX_4001" in client.calls[1][0]


def test_paginates_by_total_and_keeps_finder_offset_inside_api_query():
    client = StubClient(
        [
            listing("REQ-1", 0, 2),
            detail("REQ-1"),
            listing("REQ-2", 1, 2),
            detail("REQ-2"),
        ]
    )
    key = oracle_recruiting_source("tenant.fa.oraclecloud.com", "en", "CX_1").key

    result = OracleRecruitingConnector(
        key,
        page_size=1,
        client=client,
        detail_concurrency=1,
    ).fetch()

    assert result.complete is True
    assert result.pages_fetched == 2
    assert [job.external_job_id for job in result.jobs] == ["REQ-1", "REQ-2"]
    assert "limit=1,offset=0" in client.calls[0][0]
    assert "limit=1,offset=1" in client.calls[2][0]


def test_later_page_failure_preserves_partial_incomplete_manifest():
    failure = HttpFailure(
        "timeout",
        "timed out",
        "https://tenant.fa.oraclecloud.com",
        True,
        3,
    )
    client = StubClient([listing("REQ-1", 0, 2), detail("REQ-1"), failure])
    key = oracle_recruiting_source("tenant.fa.oraclecloud.com", "en", "CX_1").key

    result = OracleRecruitingConnector(
        key,
        page_size=1,
        client=client,
        detail_concurrency=1,
    ).fetch()

    assert result.complete is False
    assert [job.external_job_id for job in result.jobs] == ["REQ-1"]
    assert result.errors[0].code == "timeout"
    assert result.errors[0].page == 2


def test_marks_manifest_incomplete_if_total_changes_during_pagination():
    client = StubClient([listing("REQ-1", 0, 2), detail("REQ-1"), listing("REQ-2", 1, 3)])
    key = oracle_recruiting_source("tenant.fa.oraclecloud.com", "en", "CX_1").key

    result = OracleRecruitingConnector(
        key,
        page_size=1,
        client=client,
        detail_concurrency=1,
    ).fetch()

    assert result.complete is False
    assert "total changed" in result.errors[0].message


@pytest.mark.parametrize(
    "key",
    [
        "attacker.example|en|CX_1",
        "tenant.fa.oraclecloud.com|../en|CX_1",
        "tenant.fa.oraclecloud.com|en|../CX_1",
        "tenant.fa.oraclecloud.com.evil.example|en|CX_1",
    ],
)
def test_rejects_unsafe_source_keys(key):
    with pytest.raises(ValueError):
        OracleRecruitingConnector(key, client=StubClient([]))


def test_rejects_injected_job_identifier_without_detail_fetch():
    client = StubClient([listing("123,siteNumber=OTHER", 0, 1)])
    key = oracle_recruiting_source("tenant.fa.oraclecloud.com", "en", "CX_1").key

    result = OracleRecruitingConnector(key, client=client).fetch()

    assert result.complete is False
    assert result.jobs == ()
    assert len(client.calls) == 1
    assert "safe Oracle identifier" in result.errors[0].message


def test_factory_supports_oracle_recruiting_source_keys():
    client = StubClient([])
    key = oracle_recruiting_source("tenant.fa.oraclecloud.com", "en", "CX_1").key

    connector = build_connector("Oracle-Recruiting", key, client=client)

    assert isinstance(connector, OracleRecruitingConnector)
    assert connector.client is client
