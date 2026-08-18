from __future__ import annotations

import json

import pytest

from fortune_intel.connectors import (
    OfficialStructuredConnector,
    TextResponse,
    build_connector,
    official_structured_source,
)
from fortune_intel.discovery import classify_official_structured_url
from fortune_intel.services.bulk_source_approval import SUPPORTED_KINDS

MANIFEST = "https://careers.example.com/job-sitemap.xml"
JOB_1 = "https://careers.example.com/jobs/engineer"
JOB_2 = "https://careers.example.com/jobs/analyst"


class StubClient:
    def __init__(self, responses):
        self.responses = dict(responses)
        self.calls = []

    def get_text(self, url, *, max_bytes):
        self.calls.append((url, max_bytes))
        value = self.responses[url]
        if isinstance(value, Exception):
            raise value
        content_type = "application/xml" if url.endswith((".xml", ".rss")) else "text/html"
        return TextResponse(url, content_type, value)


def sitemap(*urls):
    body = "".join(f"<url><loc>{url}</loc></url>" for url in urls)
    return f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'


def sitemap_index(*urls):
    body = "".join(f"<sitemap><loc>{url}</loc></sitemap>" for url in urls)
    return f'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</sitemapindex>'


def page(url, identifier="REQ-1", title="Platform Engineer"):
    posting = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "identifier": {"value": identifier},
        "title": title,
        "url": url,
        "description": "<p>Build reliable systems.</p>",
        "datePosted": "2026-08-01T12:00:00-04:00",
        "dateModified": "2026-08-02T16:00:00Z",
        "validThrough": "2026-09-01T23:59:59Z",
        "employmentType": ["FULL_TIME"],
        "hiringOrganization": {"name": "Example"},
        "jobLocation": {
            "address": {
                "addressLocality": "Boston",
                "addressRegion": "MA",
                "addressCountry": "US",
            }
        },
    }
    return f'<html><script type="application/ld+json">{json.dumps(posting)}</script></html>'


def test_exact_source_and_contextual_classifier_reject_unsafe_or_non_manifest_urls():
    source = official_structured_source(MANIFEST)
    candidate = classify_official_structured_url(MANIFEST, origin="official careers page")

    assert source.key == MANIFEST
    assert candidate is not None
    assert candidate.connector_kind == "official_structured"
    assert candidate.board_token == MANIFEST
    assert "official_structured" in SUPPORTED_KINDS
    assert isinstance(build_connector("official_structured", MANIFEST), OfficialStructuredConnector)
    assert classify_official_structured_url(JOB_1, origin="page") is None
    with pytest.raises(ValueError):
        official_structured_source("https://127.0.0.1/jobs.xml")
    with pytest.raises(ValueError):
        official_structured_source("https://careers.example.com/../private/jobs.xml")


def test_fetches_complete_sitemap_manifest_and_native_jobposting_fields():
    client = StubClient({MANIFEST: sitemap(JOB_1), JOB_1: page(JOB_1)})

    result = OfficialStructuredConnector(MANIFEST, client=client).fetch()

    assert result.complete is True
    assert result.pages_fetched == 2
    assert result.errors == ()
    job = result.jobs[0]
    assert job.external_job_id == "REQ-1"
    assert job.title == "Platform Engineer"
    assert job.location == "Boston, MA, US"
    assert job.description == "Build reliable systems."
    assert job.source_opened_at == "2026-08-01T16:00:00+00:00"
    assert job.source_updated_at == "2026-08-02T16:00:00+00:00"
    assert job.metadata["valid_through"] == "2026-09-01T23:59:59+00:00"
    assert job.metadata["manifest_url"] == MANIFEST


def test_exhaustively_traverses_sitemap_index():
    child_1 = "https://careers.example.com/job-sitemap-1.xml"
    child_2 = "https://careers.example.com/job-sitemap-2.xml"
    client = StubClient(
        {
            MANIFEST: sitemap_index(child_1, child_2),
            child_1: sitemap(JOB_1),
            child_2: sitemap(JOB_2),
            JOB_1: page(JOB_1, "REQ-1"),
            JOB_2: page(JOB_2, "REQ-2", "Data Analyst"),
        }
    )

    result = OfficialStructuredConnector(MANIFEST, client=client).fetch()

    assert result.complete is True
    assert result.pages_fetched == 5
    assert {job.external_job_id for job in result.jobs} == {"REQ-1", "REQ-2"}


@pytest.mark.parametrize(
    "manifest,error",
    [
        (sitemap(JOB_1, JOB_1), "duplicate job URLs"),
        (sitemap("https://attacker.example/jobs/1"), "outside the exact official host"),
        ("<!DOCTYPE foo [<!ENTITY x 'bad'>]><urlset/>", "DTD or entities"),
    ],
)
def test_manifest_integrity_failures_fail_closed(manifest, error):
    result = OfficialStructuredConnector(MANIFEST, client=StubClient({MANIFEST: manifest})).fetch()

    assert result.complete is False
    assert error in result.errors[0].message


def test_job_ceiling_and_record_errors_fail_closed_without_discarding_valid_rows():
    client = StubClient(
        {
            MANIFEST: sitemap(JOB_1, JOB_2),
            JOB_1: page(JOB_1),
            JOB_2: "<html><p>No structured job</p></html>",
        }
    )
    result = OfficialStructuredConnector(MANIFEST, client=client).fetch()
    capped = OfficialStructuredConnector(
        MANIFEST, client=StubClient({MANIFEST: sitemap(JOB_1, JOB_2)}), max_jobs=1
    ).fetch()

    assert result.complete is False
    assert len(result.jobs) == 1
    assert "exactly one JobPosting" in result.errors[0].message
    assert capped.complete is False
    assert "completeness ceiling" in capped.errors[0].message


def test_rss_is_parsed_but_never_claimed_complete():
    feed = "https://careers.example.com/jobs.rss"
    xml = f"<rss><channel><item><link>{JOB_1}</link></item></channel></rss>"
    result = OfficialStructuredConnector(
        feed, client=StubClient({feed: xml, JOB_1: page(JOB_1)})
    ).fetch()

    assert len(result.jobs) == 1
    assert result.complete is False
    assert "cannot prove" in result.errors[0].message


def test_duplicate_native_identifier_fails_closed():
    result = OfficialStructuredConnector(
        MANIFEST,
        client=StubClient(
            {
                MANIFEST: sitemap(JOB_1, JOB_2),
                JOB_1: page(JOB_1, "SAME"),
                JOB_2: page(JOB_2, "SAME"),
            }
        ),
    ).fetch()

    assert result.complete is False
    assert len(result.jobs) == 1
    assert "duplicate native job ID" in result.errors[0].message
