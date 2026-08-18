from __future__ import annotations

from collections import deque

import pytest

from fortune_intel.connectors import (
    ADPWorkforceNowConnector,
    HttpFailure,
    adp_workforce_now_source,
    adp_workforce_now_source_from_url,
    build_connector,
)
from fortune_intel.discovery import classify_ats_url
from fortune_intel.services.bulk_source_approval import SUPPORTED_KINDS

CLIENT_ID = "be841b2c-7fe9-4b77-bda5-63263ad0f62b"
CAREER_CENTER_ID = "19000101_000001"
PORTAL_URL = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?"
    f"cid={CLIENT_ID}&ccId={CAREER_CENTER_ID}&type=MP&lang=en_US"
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


def custom_fields(identifier: str):
    return {
        "stringFields": [
            {
                "stringValue": identifier,
                "nameCode": {"codeValue": "ExternalJobID"},
            }
        ]
    }


def summary(identifier: str, item_id: str):
    return {
        "itemID": item_id,
        "requisitionTitle": f"Role {identifier}",
        "postDate": "2026-08-06T12:41:00.000-04:00",
        "customFieldGroup": custom_fields(identifier),
        "requisitionLocations": [
            {
                "nameCode": {"shortName": "Austin, TX, US"},
                "address": {
                    "cityName": "Austin",
                    "countrySubdivisionLevel1": {"codeValue": "TX"},
                },
            }
        ],
    }


def listing(*records, offset: int = 0, total: int | None = None):
    return {
        "jobRequisitions": list(records),
        "meta": {
            "startSequence": offset,
            "totalNumber": len(records) if total is None else total,
        },
    }


def detail(identifier: str, item_id: str):
    return {
        **summary(identifier, item_id),
        "requisitionDescription": (
            f"<div><p>Description for <strong>{identifier}</strong>.</p></div>"
        ),
        "clientRequisitionID": f"CLIENT-{identifier}",
        "workLevelCode": {"shortName": "Individual Contributor"},
        "sponsoredVisaTypeCodes": [{"codeValue": "H1B"}],
    }


def source_key() -> str:
    return adp_workforce_now_source(CLIENT_ID, CAREER_CENTER_ID, "en_US").key


def test_parses_exact_observed_url_and_discards_navigation_parameters():
    source = adp_workforce_now_source_from_url(
        f"{PORTAL_URL}&jobId=603648&source=CC2&selectedMenuKey=CareerCenter"
    )

    assert source.key == f"{CLIENT_ID}|{CAREER_CENTER_ID}|en_US"
    assert source.public_base_url == (
        "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
        f"recruitment.html?cid={CLIENT_ID}&ccId={CAREER_CENTER_ID}&lang=en_US"
    )


def test_accepts_exact_locale_parameter_when_lang_is_absent():
    source = adp_workforce_now_source_from_url(
        "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
        f"recruitment.html?cid={CLIENT_ID}&ccId=19000101_000002&locale=en_US"
    )

    assert source.locale == "en_US"
    assert source.career_center_id == "19000101_000002"


@pytest.mark.parametrize(
    "url",
    [
        f"http://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid={CLIENT_ID}&ccId={CAREER_CENTER_ID}&lang=en_US",
        f"https://user@workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid={CLIENT_ID}&ccId={CAREER_CENTER_ID}&lang=en_US",
        f"https://workforcenow.adp.com:443/mascsr/default/mdf/recruitment/recruitment.html?cid={CLIENT_ID}&ccId={CAREER_CENTER_ID}&lang=en_US",
        f"https://workforcenow.adp.com.evil.test/mascsr/default/mdf/recruitment/recruitment.html?cid={CLIENT_ID}&ccId={CAREER_CENTER_ID}&lang=en_US",
        f"https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid={CLIENT_ID}&lang=en_US",
        f"https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid={CLIENT_ID}&ccId={CAREER_CENTER_ID}&lang=en_US&locale=en_US",
        f"https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid={CLIENT_ID}&ccId={CAREER_CENTER_ID}&lang=en_US%2F%3Futm_source%3Dcompany",
        f"https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid={CLIENT_ID}&ccId={CAREER_CENTER_ID}&lang=en_US#",
        "https://workforcenow.adp.com/jobs/apply/posting.html?client=unitil&ccId=19000101_000001&lang=en_US",
    ],
)
def test_rejects_unsafe_incomplete_and_legacy_url_shapes(url):
    with pytest.raises(ValueError):
        adp_workforce_now_source_from_url(url)
    assert classify_ats_url(url) is None


def test_rejects_unbounded_query_field_counts():
    url = f"{PORTAL_URL}&" + "&".join(f"extra{index}=1" for index in range(65))

    with pytest.raises(ValueError, match="field limit"):
        adp_workforce_now_source_from_url(url)
    assert classify_ats_url(url) is None


def test_fetches_every_page_and_detail_with_exact_dates_locations_and_description():
    client = StubClient(
        [
            listing(summary("603648", "9205213989359_1"), offset=0, total=2),
            detail("603648", "9205213989359_1"),
            listing(summary("603649", "9205213989360_1"), offset=1, total=2),
            detail("603649", "9205213989360_1"),
        ]
    )

    result = ADPWorkforceNowConnector(
        source_key(),
        page_size=1,
        client=client,
        detail_concurrency=1,
    ).fetch()

    assert result.complete is True
    assert result.pages_fetched == 2
    assert [job.external_job_id for job in result.jobs] == ["603648", "603649"]
    job = result.jobs[0]
    assert job.source_opened_at == "2026-08-06T16:41:00+00:00"
    assert job.source_updated_at is None
    assert job.location == "Austin, TX, US"
    assert job.description == "Description for 603648 ."
    assert job.url.endswith(f"cid={CLIENT_ID}&ccId={CAREER_CENTER_ID}&lang=en_US&jobId=603648")
    assert job.metadata["source_opened_at_field"] == "postDate"
    assert job.metadata["sponsored_visa_types"] == ("H1B",)
    assert "%24skip=0&%24top=1" in client.calls[0][0]
    assert "%24skip=1&%24top=1" in client.calls[2][0]
    assert "/job-requisitions/603648?" in client.calls[1][0]


def test_complete_empty_manifest_is_explicitly_complete():
    result = ADPWorkforceNowConnector(
        source_key(),
        client=StubClient([listing(offset=0, total=0)]),
    ).fetch()

    assert result.complete is True
    assert result.jobs == ()
    assert result.errors == ()


def test_manifest_is_incomplete_if_total_changes():
    client = StubClient(
        [
            listing(summary("1", "100_1"), offset=0, total=2),
            detail("1", "100_1"),
            listing(summary("2", "101_1"), offset=1, total=3),
        ]
    )

    result = ADPWorkforceNowConnector(
        source_key(), page_size=1, client=client, detail_concurrency=1
    ).fetch()

    assert result.complete is False
    assert [job.external_job_id for job in result.jobs] == ["1"]
    assert "total changed" in result.errors[0].message


def test_manifest_is_incomplete_if_pagination_stops_before_total():
    client = StubClient(
        [
            listing(summary("1", "100_1"), offset=0, total=2),
            detail("1", "100_1"),
            listing(offset=1, total=2),
        ]
    )

    result = ADPWorkforceNowConnector(
        source_key(), page_size=1, client=client, detail_concurrency=1
    ).fetch()

    assert result.complete is False
    assert "stopped before total" in result.errors[0].message


def test_manifest_is_incomplete_if_detail_fails():
    failure = HttpFailure(
        "timeout",
        "timed out",
        "https://workforcenow.adp.com",
        True,
        3,
    )
    client = StubClient([listing(summary("603648", "9205213989359_1"), total=1), failure])

    result = ADPWorkforceNowConnector(source_key(), client=client, detail_concurrency=1).fetch()

    assert result.complete is False
    assert result.jobs == ()
    assert result.errors[0].external_job_id == "603648"
    assert result.errors[0].page == 1


def test_manifest_is_incomplete_if_detail_identity_does_not_match_summary():
    client = StubClient(
        [
            listing(summary("603648", "9205213989359_1"), total=1),
            detail("OTHER", "9205213989359_1"),
        ]
    )

    result = ADPWorkforceNowConnector(source_key(), client=client, detail_concurrency=1).fetch()

    assert result.complete is False
    assert result.jobs == ()
    assert "did not match" in result.errors[0].message


def test_manifest_is_incomplete_if_native_job_id_is_duplicated():
    client = StubClient(
        [
            listing(
                summary("603648", "9205213989359_1"),
                summary("603648", "9205213989360_1"),
                total=2,
            ),
            detail("603648", "9205213989359_1"),
            detail("603648", "9205213989360_1"),
        ]
    )

    result = ADPWorkforceNowConnector(source_key(), client=client, detail_concurrency=1).fetch()

    assert result.complete is False
    assert len(result.jobs) == 1
    assert "duplicate native job ID" in result.errors[0].message


def test_discovery_and_factory_register_only_explicit_workforce_now_kind():
    candidate = classify_ats_url(PORTAL_URL)

    assert candidate is not None
    assert candidate.connector_kind == "adp_workforce_now"
    assert candidate.board_token == source_key()
    assert (
        candidate.normalized_base_url
        == adp_workforce_now_source(CLIENT_ID, CAREER_CENTER_ID, "en_US").public_base_url
    )
    client = StubClient([])
    connector = build_connector("ADP-Workforce-Now", source_key(), client=client)
    assert isinstance(connector, ADPWorkforceNowConnector)
    assert connector.client is client
    assert "adp_workforce_now" in SUPPORTED_KINDS
