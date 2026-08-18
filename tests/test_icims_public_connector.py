from __future__ import annotations

import json
from collections import deque

import pytest

from fortune_intel.connectors import ICIMSPublicConnector, build_connector
from fortune_intel.connectors.icims_public import icims_public_source_from_url
from fortune_intel.discovery import classify_ats_url
from fortune_intel.services.bulk_source_approval import SUPPORTED_KINDS

HOST = "careers-rambus.icims.com"
BASE = f"https://{HOST}/jobs/search"


class StubClient:
    def __init__(self, pages):
        self.pages = deque(pages)
        self.calls = []

    def get_text(self, url, *, max_bytes=2_000_000):
        self.calls.append(url)
        value = self.pages.popleft()
        if isinstance(value, Exception):
            raise value
        return value


def listing(page, total, jobs):
    cards = "".join(
        f'''<li class="iCIMS_JobCardItem"><div class="title">
        <a class="iCIMS_Anchor" href="https://{HOST}/jobs/{identifier}/{slug}/job?in_iframe=1">
        <h3>{title}</h3></a></div></li>'''
        for identifier, slug, title in jobs
    )
    return f'''<div class="iCIMS_MainWrapper iCIMS_ListingsPage">
    <h2 class="iCIMS_SubHeader_Jobs">Search Results Page {page} of {total}</h2>
    <ul class="container-fluid iCIMS_JobsTable">{cards}</ul></div>'''


def detail(identifier="23028", slug="tech-dir", title="Technical Director"):
    payload = {
        "@type": "JobPosting",
        "title": title,
        "url": f"https://{HOST}/jobs/{identifier}/{slug}/job",
        "datePosted": "2026-08-12T04:00:00.000Z",
        "description": "<p>Build <strong>safe systems</strong>.</p>",
        "employmentType": "FULL_TIME",
        "occupationalCategory": "Engineering",
        "jobLocation": [
            {
                "address": {
                    "addressLocality": "San Jose",
                    "addressRegion": "CA",
                    "addressCountry": "US",
                }
            }
        ],
    }
    return f'''<div class="iCIMS_MainWrapper iCIMS_JobPage"></div>
    <script type="application/ld+json">{json.dumps(payload)}</script>'''


def manifest(*jobs):
    urls = "".join(
        f"<url><loc>https://{HOST}/jobs/{identifier}/{slug}/job</loc>"
        "<lastmod>2026-08-12T17:18:18-04:00</lastmod></url>"
        for identifier, slug, _title in jobs
    )
    return [
        f"User-agent: *\nSitemap: https://{HOST}/sitemap.xml\n",
        (
            "<?xml version='1.0' encoding='utf-8'?>"
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<url><loc>{BASE}</loc></url>{urls}</urlset>"
        ),
    ]


def test_source_requires_exact_unfiltered_public_search_url():
    assert icims_public_source_from_url(f"{BASE}?ss=1&in_iframe=1").key == HOST
    assert classify_ats_url(BASE).connector_kind == "icims_public"


@pytest.mark.parametrize(
    "url",
    [
        f"http://{HOST}/jobs/search",
        f"https://user@{HOST}/jobs/search",
        f"https://{HOST}:443/jobs/search",
        f"https://{HOST}.evil.test/jobs/search",
        f"https://{HOST}/jobs/search?searchKeyword=data",
        f"https://{HOST}/jobs/search#jobs",
        f"https://{HOST}/jobs/23028/role/job",
    ],
)
def test_source_rejects_unsafe_filtered_and_detail_urls(url):
    with pytest.raises(ValueError):
        icims_public_source_from_url(url)
    assert classify_ats_url(url) is None


def test_fetches_every_page_and_validates_each_native_detail():
    first = ("23028", "tech-dir", "Technical Director")
    second = ("23027", "logic-design", "Logic Designer")
    client = StubClient(
        manifest(first, second)
        + [listing(1, 2, [first]), detail(*first), listing(2, 2, [second]), detail(*second)]
    )
    result = ICIMSPublicConnector(HOST, client=client, detail_concurrency=1).fetch()

    assert result.complete is True
    assert result.pages_fetched == 2
    assert [job.external_job_id for job in result.jobs] == ["23028", "23027"]
    job = result.jobs[0]
    assert job.title == "Technical Director"
    assert job.source_opened_at == "2026-08-12T04:00:00+00:00"
    assert job.source_updated_at == "2026-08-12T21:18:18+00:00"
    assert job.location == "San Jose, CA, US"
    assert job.description == "Build safe systems ."
    assert job.metadata["additional_locations"][0]["country"] == "US"
    assert client.calls[2].endswith("?ss=1&pr=0&in_iframe=1")
    assert client.calls[4].endswith("?ss=1&pr=1&in_iframe=1")


@pytest.mark.parametrize(
    "pages, message",
    [
        ([listing(2, 2, [("23028", "tech-dir", "Technical Director")])], "unexpected page"),
        ([listing(1, 1, [])], "no job cards"),
        (
            [listing(1, 2, [("23028", "tech-dir", "Technical Director")]), detail()],
            "pagination exceeded",
        ),
    ],
)
def test_incomplete_pagination_fails_closed(pages, message):
    one = ("23028", "tech-dir", "Technical Director")
    result = ICIMSPublicConnector(
        HOST, client=StubClient(manifest(one) + pages), max_pages=1, detail_concurrency=1
    ).fetch()
    assert result.complete is False
    assert message in result.errors[-1].message


def test_detail_identity_title_date_and_shape_fail_closed():
    summary = ("23028", "tech-dir", "Technical Director")
    invalid = detail(identifier="23029")
    result = ICIMSPublicConnector(
        HOST,
        client=StubClient(manifest(summary) + [listing(1, 1, [summary]), invalid]),
        detail_concurrency=1,
    ).fetch()
    assert result.complete is False
    assert result.jobs == ()
    assert "identity" in result.errors[0].message


def test_factory_and_reviewed_approval_registry_use_explicit_kind():
    connector = build_connector("icims-public", HOST, client=StubClient([]))
    assert isinstance(connector, ICIMSPublicConnector)
    assert "icims_public" in SUPPORTED_KINDS


def test_zero_job_sitemap_is_a_complete_manifest_without_listing_guesses():
    result = ICIMSPublicConnector(HOST, client=StubClient(manifest())).fetch()
    assert result.complete is True
    assert result.jobs == ()
    assert result.pages_fetched == 0


def test_listing_and_robots_sitemap_must_contain_the_same_native_ids():
    first = ("23028", "tech-dir", "Technical Director")
    other = ("23027", "logic-design", "Logic Designer")
    result = ICIMSPublicConnector(
        HOST,
        client=StubClient(manifest(first, other) + [listing(1, 1, [first]), detail(*first)]),
        detail_concurrency=1,
    ).fetch()
    assert result.complete is False
    assert "sitemap manifest" in result.errors[-1].message
