from __future__ import annotations

import json
from collections import deque

import pytest

from fortune_intel.connectors import (
    HttpFailure,
    UKGRecruitingPublicConnector,
    build_connector,
    ukg_recruiting_public_source,
    ukg_recruiting_public_source_from_url,
)
from fortune_intel.discovery import classify_ats_url
from fortune_intel.services.bulk_source_approval import SUPPORTED_KINDS

HOST = "recruiting2.ultipro.com"
TENANT = "ARC1026ARCOI"
BOARD_ID = "2af23579-6cf8-4926-be1a-3bc74872c197"
FIRST_ID = "78ec5a6e-56b4-44a2-9dba-b3563ee71b89"
SECOND_ID = "68ec5a6e-56b4-44a2-9dba-b3563ee71b80"
BOARD_URL = f"https://{HOST}/{TENANT}/JobBoard/{BOARD_ID}"


class StubClient:
    def __init__(self, listings=(), details=()):
        self.listings = deque(listings)
        self.details = deque(details)
        self.post_calls = []
        self.get_calls = []

    def post_json(self, url, *, json_body):
        self.post_calls.append((url, json_body))
        if not self.listings:
            raise AssertionError(f"unexpected POST: {url}")
        response = self.listings.popleft()
        if isinstance(response, Exception):
            raise response
        return response

    def get_text(self, url, *, max_bytes=2_000_000):
        self.get_calls.append((url, max_bytes))
        if not self.details:
            raise AssertionError(f"unexpected GET: {url}")
        response = self.details.popleft()
        if isinstance(response, Exception):
            raise response
        return response


def source_key():
    return ukg_recruiting_public_source(HOST, TENANT, BOARD_ID).key


def summary(identifier: str, title: str = "Weld Supervisor"):
    return {
        "Id": identifier,
        "Title": title,
        "PostedDate": "2026-08-12T19:34:10.807Z",
        "RequisitionNumber": "WELDI014803",
        "Locations": [],
    }


def listing(*records, total=None):
    return {
        "opportunities": list(records),
        "locations": [],
        "totalCount": len(records) if total is None else total,
    }


def detail(identifier: str, title: str = "Weld Supervisor"):
    model = {
        "Id": identifier,
        "Title": title,
        "PostedDate": "2026-08-12T19:34:10.81Z",
        "UpdatedDate": "2026-08-10T13:27:48.431Z",
        "RequisitionNumber": "WELDI014803",
        "Description": "<p>Build <strong>safe infrastructure</strong>.</p>",
        "FullTime": True,
        "JobCategoryName": "Welding",
        "JobLocationType": 1,
        "Locations": [
            {
                "LocalizedDescription": "Newton, IA",
                "Address": {
                    "City": "Newton",
                    "State": {"Code": "IA", "Name": "Iowa"},
                    "Country": {"Code": "USA", "Name": "United States"},
                },
            }
        ],
        "JobBoardMemberships": [
            {
                "JobBoardId": BOARD_ID,
                "PublishedExternal": True,
                "ExternalPostedDate": "2026-08-12T19:34:10.807Z",
            },
            {
                "JobBoardId": "ab193b3b-c725-4266-8a4a-1f85044e37fc",
                "PublishedExternal": False,
                "ExternalPostedDate": None,
            },
        ],
        "CompensationAnnualMinimum": 80000,
        "CompensationAnnualMaximum": 100000,
        "PayRangeCurrencyCode": "USD",
    }
    return (
        "<html><script>\nvar opportunity = new "
        f"US.Opportunity.CandidateOpportunityDetail({json.dumps(model)});\n"
        "</script></html>"
    )


def test_parses_exact_observed_board_and_detail_urls_without_guessing():
    board = ukg_recruiting_public_source_from_url(
        f"{BOARD_URL}/?q=&o=postedDateDesc&w=&wc=&we=&wpst="
    )
    detail_source = ukg_recruiting_public_source_from_url(
        f"{BOARD_URL}/Opportunity/OpportunityDetail?opportunityId={FIRST_ID}"
    )

    assert board == detail_source
    assert board.key == f"{HOST}|{TENANT}|{BOARD_ID}"
    assert board.public_base_url == BOARD_URL


@pytest.mark.parametrize(
    "url",
    [
        f"http://{HOST}/{TENANT}/JobBoard/{BOARD_ID}",
        f"https://user@{HOST}/{TENANT}/JobBoard/{BOARD_ID}",
        f"https://{HOST}:443/{TENANT}/JobBoard/{BOARD_ID}",
        f"https://{HOST}.evil.test/{TENANT}/JobBoard/{BOARD_ID}",
        f"https://{HOST}/{TENANT}",
        f"https://{HOST}/{TENANT}/JobBoard/not-a-uuid",
        f"https://{HOST}/{TENANT}/JobBoard/{BOARD_ID}#",
        f"https://{HOST}/{TENANT}/JobBoard/{BOARD_ID}/private",
    ],
)
def test_rejects_unsafe_incomplete_and_ambiguous_urls(url):
    with pytest.raises(ValueError):
        ukg_recruiting_public_source_from_url(url)
    assert classify_ats_url(url) is None


def test_fetches_all_pages_and_details_with_native_board_date():
    client = StubClient(
        listings=[listing(summary(FIRST_ID), total=2), listing(summary(SECOND_ID), total=2)],
        details=[detail(FIRST_ID), detail(SECOND_ID)],
    )

    result = UKGRecruitingPublicConnector(
        source_key(), page_size=1, client=client, detail_concurrency=1
    ).fetch()

    assert result.complete is True
    assert result.pages_fetched == 2
    assert [job.external_job_id for job in result.jobs] == [FIRST_ID, SECOND_ID]
    job = result.jobs[0]
    assert job.title == "Weld Supervisor"
    assert job.source_opened_at == "2026-08-12T19:34:10.807000+00:00"
    assert job.source_updated_at == "2026-08-10T13:27:48.431000+00:00"
    assert job.location == "Newton, IA, USA"
    assert job.description == "Build safe infrastructure ."
    assert job.metadata["source_opened_at_field"] == (
        "JobBoardMemberships.ExternalPostedDate"
    )
    assert job.metadata["pay_range_minimum"] == 80000
    assert job.metadata["additional_locations"] == [
        {
            "location": "Newton, IA, USA",
            "city": "Newton",
            "region": "IA",
            "country": "USA",
        }
    ]
    first_search = client.post_calls[0][1]["opportunitySearch"]
    second_search = client.post_calls[1][1]["opportunitySearch"]
    assert first_search["Skip"] == 0
    assert second_search["Skip"] == 1
    assert first_search["Top"] == 1
    assert first_search["OrderBy"][0]["PropertyName"] == "PostedDate"
    assert client.get_calls[0][0].endswith(f"OpportunityDetail?opportunityId={FIRST_ID}")


def test_zero_job_manifest_is_complete():
    result = UKGRecruitingPublicConnector(
        source_key(), client=StubClient(listings=[listing(total=0)])
    ).fetch()

    assert result.complete is True
    assert result.jobs == ()
    assert result.errors == ()


def test_total_change_makes_manifest_incomplete():
    client = StubClient(
        listings=[listing(summary(FIRST_ID), total=2), listing(summary(SECOND_ID), total=3)],
        details=[detail(FIRST_ID)],
    )

    result = UKGRecruitingPublicConnector(
        source_key(), page_size=1, client=client, detail_concurrency=1
    ).fetch()

    assert result.complete is False
    assert len(result.jobs) == 1
    assert "total changed" in result.errors[0].message


def test_premature_empty_page_makes_manifest_incomplete():
    client = StubClient(
        listings=[listing(summary(FIRST_ID), total=2), listing(total=2)],
        details=[detail(FIRST_ID)],
    )

    result = UKGRecruitingPublicConnector(
        source_key(), page_size=1, client=client, detail_concurrency=1
    ).fetch()

    assert result.complete is False
    assert "stopped before total" in result.errors[0].message


def test_detail_failure_makes_manifest_incomplete():
    failure = HttpFailure("timeout", "timed out", BOARD_URL, True, 3)
    client = StubClient(listings=[listing(summary(FIRST_ID))], details=[failure])

    result = UKGRecruitingPublicConnector(
        source_key(), client=client, detail_concurrency=1
    ).fetch()

    assert result.complete is False
    assert result.jobs == ()
    assert result.errors[0].external_job_id == FIRST_ID
    assert result.errors[0].page == 1


@pytest.mark.parametrize(
    "changed",
    [
        detail(SECOND_ID),
        detail(FIRST_ID, title="Different title"),
        "<html>missing model</html>",
    ],
)
def test_detail_identity_and_model_mismatches_fail_closed(changed):
    client = StubClient(listings=[listing(summary(FIRST_ID))], details=[changed])

    result = UKGRecruitingPublicConnector(
        source_key(), client=client, detail_concurrency=1
    ).fetch()

    assert result.complete is False
    assert result.jobs == ()
    assert result.errors


def test_missing_native_board_posting_date_makes_manifest_incomplete():
    page = detail(FIRST_ID).replace(
        '"ExternalPostedDate": "2026-08-12T19:34:10.807Z"',
        '"ExternalPostedDate": null',
    )
    client = StubClient(listings=[listing(summary(FIRST_ID))], details=[page])

    result = UKGRecruitingPublicConnector(
        source_key(), client=client, detail_concurrency=1
    ).fetch()

    assert result.complete is False
    assert result.jobs == ()
    assert "native board posting date" in result.errors[0].message


def test_duplicate_native_id_makes_manifest_incomplete():
    client = StubClient(
        listings=[listing(summary(FIRST_ID), summary(FIRST_ID), total=2)],
        details=[detail(FIRST_ID), detail(FIRST_ID)],
    )

    result = UKGRecruitingPublicConnector(
        source_key(), client=client, detail_concurrency=1
    ).fetch()

    assert result.complete is False
    assert len(result.jobs) == 1
    assert "duplicate native job ID" in result.errors[0].message


def test_discovery_factory_and_reviewed_approval_registry_use_explicit_kind():
    candidate = classify_ats_url(f"{BOARD_URL}/?q=&o=postedDateDesc")

    assert candidate is not None
    assert candidate.connector_kind == "ukg_recruiting_public"
    assert candidate.board_token == source_key()
    assert candidate.normalized_base_url == BOARD_URL
    client = StubClient()
    connector = build_connector("UKG-Recruiting-Public", source_key(), client=client)
    assert isinstance(connector, UKGRecruitingPublicConnector)
    assert connector.client is client
    assert "ukg_recruiting_public" in SUPPORTED_KINDS
