from __future__ import annotations

import pytest

from fortune_intel.discovery import (
    classify_passive_ats_url,
    classify_unknown_external_career_url,
)


@pytest.mark.parametrize(
    ("family", "url"),
    [
        ("icims", "https://careers-acme.icims.com/jobs/search"),
        ("successfactors", "https://career5.successfactors.eu/career?company=acme"),
        ("successfactors", "https://career41.sapsf.com/careers?company=QUANTUMP"),
        ("dayforce", "https://jobs.dayforcehcm.com/en-US/acme/CANDIDATEPORTAL"),
        ("taleo", "https://aarcorp.taleo.net/careersection/2/jobsearch.ftl?lang=en"),
        ("eightfold", "https://acme.eightfold.ai/careers/job/1"),
        ("phenom", "https://acme.phenompeople.com/us/en/job/1"),
        ("avature", "https://ally.avature.net/careers"),
        ("jobvite", "https://jobs.jobvite.com/acme/job/1"),
        ("adp", "https://myjobs.adp.com/kaisercareers/cx/job-listing"),
        ("ukg", "https://recruiting.ultipro.com/SOU1058STHFB"),
        (
            "paycom",
            "https://www.paycomonline.net/v4/ats/web.php/jobs?clientkey=434D8E11B53DE940A5456677337F30F5",
        ),
        (
            "paylocity",
            "https://recruiting.paylocity.com/recruiting/jobs/List/3961/acme",
        ),
        (
            "paycor",
            "https://recruitingbypaycor.com/career/CareerHome.action?clientId=8a7883d080d90be90180dd1b6d6300d7",
        ),
        ("rippling", "https://ats.rippling.com/d-wave-quantum/jobs"),
        ("gr8_people", "https://acme.gr8people.com/jobs/1"),
        ("directemployers", "https://acme.dejobs.org/jobs/1"),
    ],
)
def test_classifies_allow_listed_passive_families(family: str, url: str) -> None:
    fingerprint = classify_passive_ats_url(url, origin_page="https://acme.example/careers")

    assert fingerprint is not None
    assert fingerprint.family == family
    assert fingerprint.observed_url == url
    assert fingerprint.origin_page == "https://acme.example/careers"


@pytest.mark.parametrize(
    "url",
    [
        "https://fakeicims.com/jobs/1",
        "https://www.adp.com/careers",
        "https://jobs.ukg.com/careers",
        "http://acme.icims.com/jobs/1",
        "https://user@acme.taleo.net/jobs/1",
        "https://127.0.0.1/jobs/1",
        "https://cdn.phenompeople.com/resources/right-to-work.pdf",
        "https://acme.avature.net/careers/logo.png",
        "https://career4.sapsf.com/careers?company=acme",
        "https://www.paycomonline.net/v4/ats/web.php/jobs",
        "https://recruiting.paylocity.com/recruiting/jobs/Details/not-numeric",
        "https://recruitingbypaycor.com/career/CareerHome.action?clientId=not-hex",
        "https://ats.rippling.com/acme/jobs/",
    ],
)
def test_passive_family_classifier_rejects_lookalikes_and_unsafe_urls(url: str) -> None:
    assert classify_passive_ats_url(url, origin_page="https://acme.example/") is None


def test_unknown_external_requires_clear_url_marker_and_excludes_aggregators() -> None:
    fingerprint = classify_unknown_external_career_url(
        "https://talent.vendor.example/company/jobs",
        origin_page="https://acme.example/careers",
    )

    assert fingerprint is not None
    assert fingerprint.family == "unknown_external"
    assert "not fetched" in fingerprint.evidence[-1]
    assert (
        classify_unknown_external_career_url(
            "https://talent.vendor.example/company/about",
            origin_page="https://acme.example/careers",
        )
        is None
    )
    assert (
        classify_unknown_external_career_url(
            "https://www.linkedin.com/company/acme/jobs",
            origin_page="https://acme.example/careers",
        )
        is None
    )
