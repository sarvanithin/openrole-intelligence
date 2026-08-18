from collections import defaultdict, deque

import pytest

from fortune_intel.discovery import CareerSourceDiscovery, FetchFailure, FetchResponse


PUBLIC_IP = "93.184.216.34"


class StubFetcher:
    def __init__(self, responses):
        self.responses = defaultdict(deque)
        for url, values in responses.items():
            self.responses[url].extend(values)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses[url]:
            raise AssertionError(f"unexpected request: {url}")
        response = self.responses[url].popleft()
        if isinstance(response, Exception):
            raise response
        return response


def response(status, body="", **headers):
    return FetchResponse(status, headers, body.encode())


def resolver(host):
    assert host
    return [PUBLIC_IP]


def service(responses, **kwargs):
    fetcher = StubFetcher(responses)
    return CareerSourceDiscovery(fetcher=fetcher, resolver=resolver, **kwargs), fetcher


@pytest.mark.parametrize(
    "url",
    [
        "https://user@company.example/careers",
        "https://company.example:8443/careers",
        "https://127.0.0.1/careers",
        "http://company.example:8080/careers",
    ],
)
def test_rejects_non_https_credentials_ports_and_ip_literals_without_fetching(url):
    discovery, fetcher = service({})

    report = discovery.discover(url)

    assert report.disposition == "rejected_start_url"
    assert not report.candidates
    assert fetcher.calls == []


def test_upgrades_verified_http_seed_to_exact_same_https_host():
    discovery, fetcher = service(
        {
            "https://company.example/robots.txt": [response(404)],
            "https://company.example/careers": [
                response(200, "<html></html>", **{"Content-Type": "text/html"})
            ],
        }
    )

    report = discovery.discover("http://company.example/careers")

    assert report.disposition == "no_supported_ats_found"
    assert report.pages_checked == ("https://company.example/careers",)
    assert "exact same host" in report.evidence[0]
    assert [call[0] for call in fetcher.calls] == [
        "https://company.example/robots.txt",
        "https://company.example/careers",
    ]


def test_rejects_hostname_when_any_resolved_address_is_private():
    fetcher = StubFetcher({})
    discovery = CareerSourceDiscovery(
        fetcher=fetcher,
        resolver=lambda _: [PUBLIC_IP, "10.0.0.8"],
    )

    report = discovery.discover("https://company.example/careers")

    assert report.disposition == "rejected_start_url"
    assert "private or reserved" in report.evidence[0]
    assert fetcher.calls == []


def test_honors_path_specific_robots_rule_and_does_not_fetch_page():
    discovery, fetcher = service(
        {
            "https://company.example/robots.txt": [
                response(200, "User-agent: *\nDisallow: /careers", **{"Content-Type": "text/plain"})
            ]
        }
    )

    report = discovery.discover("https://company.example/careers")

    assert report.disposition == "robots_denied"
    assert len(fetcher.calls) == 1
    assert fetcher.calls[0][0].endswith("/robots.txt")


def test_finds_supported_ats_link_in_bounded_company_html():
    html = '<a href="https://job-boards.greenhouse.io/acme/jobs/123">Open roles</a>'
    discovery, fetcher = service(
        {
            "https://company.example/robots.txt": [response(404)],
            "https://company.example/careers": [
                response(200, html, **{"Content-Type": "text/html; charset=utf-8"})
            ],
        }
    )

    report = discovery.discover("https://company.example/careers")

    assert report.disposition == "candidates_found"
    assert [(item.connector_kind, item.board_token) for item in report.candidates] == [
        ("greenhouse", "acme")
    ]
    assert len(fetcher.calls) == 2
    assert all(call[1]["max_bytes"] <= 1_000_000 for call in fetcher.calls)


def test_finds_explicit_same_company_structured_manifest_without_guessing_or_fetching_it():
    html = (
        '<link rel="sitemap" type="application/xml" href="/jobs/job-sitemap.xml">'
        '<link rel="alternate" type="application/rss+xml" href="https://evil.example/jobs.rss">'
    )
    discovery, fetcher = service(
        {
            "https://company.example/robots.txt": [response(404)],
            "https://company.example/careers": [
                response(200, html, **{"Content-Type": "text/html"})
            ],
        },
        max_pages=1,
    )

    report = discovery.discover("https://company.example/careers")

    assert report.disposition == "candidates_found"
    assert [(item.connector_kind, item.board_token) for item in report.candidates] == [
        ("official_structured", "https://company.example/jobs/job-sitemap.xml")
    ]
    assert [call[0] for call in fetcher.calls] == [
        "https://company.example/robots.txt",
        "https://company.example/careers",
    ]


def test_finds_same_company_structured_manifest_explicitly_advertised_in_robots():
    discovery, fetcher = service(
        {
            "https://company.example/robots.txt": [
                response(
                    200,
                    "User-agent: *\nAllow: /\n"
                    "Sitemap: https://company.example/jobs/job-sitemap.xml\n"
                    "Sitemap: https://evil.example/jobs.xml\n",
                    **{"Content-Type": "text/plain"},
                )
            ],
            "https://company.example/careers": [
                response(200, "<html></html>", **{"Content-Type": "text/html"})
            ],
        },
        max_pages=1,
    )

    report = discovery.discover("https://company.example/careers")

    assert report.disposition == "candidates_found"
    assert [(item.connector_kind, item.board_token) for item in report.candidates] == [
        ("official_structured", "https://company.example/jobs/job-sitemap.xml")
    ]
    # Discovery records the explicitly declared URL but never requests a
    # sitemap or job detail page.  Connector probing is a separate step.
    assert [call[0] for call in fetcher.calls] == [
        "https://company.example/robots.txt",
        "https://company.example/careers",
    ]


def test_inventories_passive_and_bounded_unknown_links_without_fetching_them():
    html = """
        <a href="https://acme.icims.com/jobs/123">iCIMS role</a>
        <a href="https://talent.vendor.example/careers/openings">External careers</a>
        <a href="https://www.indeed.com/cmp/acme/jobs">Aggregator</a>
        <a href="https://127.0.0.1/jobs">Unsafe</a>
        <a href="https://job-boards.greenhouse.io/acme/jobs/1">Supported</a>
    """
    discovery, fetcher = service(
        {
            "https://company.example/robots.txt": [response(404)],
            "https://company.example/careers": [
                response(200, html, **{"Content-Type": "text/html"})
            ],
        },
        max_pages=1,
    )

    report = discovery.discover("https://company.example/careers")

    assert [(item.family, item.observed_url) for item in report.fingerprints] == [
        ("icims", "https://acme.icims.com/jobs/123"),
        ("unknown_external", "https://talent.vendor.example/careers/openings"),
    ]
    assert report.candidates[0].connector_kind == "greenhouse"
    assert len(fetcher.calls) == 2


def test_inventories_an_operator_supplied_passive_ats_seed():
    taleo_url = "https://aarcorp.taleo.net/careersection/2/jobsearch.ftl?lang=en"
    discovery, _ = service(
        {
            "https://aarcorp.taleo.net/robots.txt": [response(404)],
            taleo_url: [response(200, "<html></html>", **{"Content-Type": "text/html"})],
        }
    )

    report = discovery.discover(taleo_url)

    assert len(report.fingerprints) == 1
    assert report.fingerprints[0].family == "taleo"
    assert report.fingerprints[0].origin_page == taleo_url


def test_follows_only_conventional_same_company_career_subdomain():
    home = """
        <a href="https://careers.company.example/openings">Careers</a>
        <a href="https://evil.example/jobs">Unrelated jobs</a>
    """
    jobs = '<a href="https://jobs.ashbyhq.com/acme">View openings</a>'
    discovery, fetcher = service(
        {
            "https://company.example/robots.txt": [response(200, "User-agent: *\nAllow: /")],
            "https://company.example/": [response(200, home, **{"Content-Type": "text/html"})],
            "https://careers.company.example/robots.txt": [response(404)],
            "https://careers.company.example/openings": [
                response(200, jobs, **{"Content-Type": "text/html"})
            ],
        }
    )

    report = discovery.discover("https://company.example")

    assert report.disposition == "candidates_found"
    assert report.candidates[0].connector_kind == "ashby"
    assert all("evil.example" not in call[0] for call in fetcher.calls)
    assert len(report.pages_checked) == 2


def test_follows_same_company_link_with_explicit_career_label_even_when_path_is_generic():
    home = '<a href="/people">Careers at Example</a>'
    people = '<a href="https://jobs.lever.co/acme">Open roles</a>'
    discovery, fetcher = service(
        {
            "https://company.example/robots.txt": [response(404)],
            "https://company.example/": [response(200, home, **{"Content-Type": "text/html"})],
            "https://company.example/people": [
                response(200, people, **{"Content-Type": "text/html"})
            ],
        }
    )

    report = discovery.discover("https://company.example")

    assert report.disposition == "candidates_found"
    assert report.candidates[0].connector_kind == "lever"
    assert report.pages_checked == ("https://company.example/", "https://company.example/people")
    assert all("jobs.lever.co" not in call[0] for call in fetcher.calls)


def test_follows_same_company_career_widget_data_url_when_path_is_generic():
    home = '<button data-career-url="/work">Find your role</button>'
    work = '<a href="https://jobs.lever.co/acme">Open roles</a>'
    discovery, fetcher = service(
        {
            "https://company.example/robots.txt": [response(404)],
            "https://company.example/": [response(200, home, **{"Content-Type": "text/html"})],
            "https://company.example/work": [
                response(200, work, **{"Content-Type": "text/html"})
            ],
        }
    )

    report = discovery.discover("https://company.example")

    assert report.disposition == "candidates_found"
    assert report.candidates[0].connector_kind == "lever"
    assert report.pages_checked == ("https://company.example/", "https://company.example/work")
    assert all("jobs.lever.co" not in call[0] for call in fetcher.calls)


def test_does_not_follow_generic_data_url_without_career_signal():
    home = '<button data-url="/work">Learn more</button>'
    discovery, fetcher = service(
        {
            "https://company.example/robots.txt": [response(404)],
            "https://company.example/": [response(200, home, **{"Content-Type": "text/html"})],
        }
    )

    report = discovery.discover("https://company.example")

    assert report.disposition == "no_supported_ats_found"
    assert report.pages_checked == ("https://company.example/",)
    assert [call[0] for call in fetcher.calls] == [
        "https://company.example/robots.txt",
        "https://company.example/",
    ]


def test_classifies_external_ats_redirect_without_fetching_redirect_target():
    discovery, fetcher = service(
        {
            "https://company.example/robots.txt": [response(404)],
            "https://company.example/careers": [
                response(302, Location="https://jobs.eu.lever.co/acme")
            ],
        }
    )

    report = discovery.discover("https://company.example/careers")

    assert report.disposition == "candidates_found"
    assert report.candidates[0].normalized_base_url == "https://jobs.eu.lever.co/acme"
    assert "resolved to public IP" in report.evidence[-2]
    assert all("jobs.eu.lever.co" not in call[0] for call in fetcher.calls)


def test_promotes_exact_icims_search_redirect_to_review_required_candidate_without_fetching_target():
    discovery, fetcher = service(
        {
            "https://company.example/robots.txt": [response(404)],
            "https://company.example/careers": [
                response(302, Location="https://acme.icims.com/jobs/search")
            ],
        }
    )

    report = discovery.discover("https://company.example/careers")

    assert report.disposition == "candidates_found"
    assert report.candidates[0].connector_kind == "icims_public"
    assert report.candidates[0].board_token == "acme.icims.com"
    assert report.fingerprints == ()
    assert all("acme.icims.com" not in call[0] for call in fetcher.calls)


def test_rejects_private_redirect_without_requesting_target():
    discovery, fetcher = service(
        {
            "https://company.example/robots.txt": [response(404)],
            "https://company.example/careers": [
                response(302, Location="https://internal.company.example/jobs")
            ],
        }
    )
    discovery.resolver = lambda host: ["10.0.0.7"] if host.startswith("internal.") else [PUBLIC_IP]

    report = discovery.discover("https://company.example/careers")

    assert report.disposition == "unsafe_redirect"
    assert all("internal.company.example" not in call[0] for call in fetcher.calls)


def test_rejects_supported_ats_redirect_when_target_does_not_resolve_publicly():
    discovery, fetcher = service(
        {
            "https://company.example/robots.txt": [response(404)],
            "https://company.example/careers": [
                response(302, Location="https://jobs.lever.co/acme")
            ],
        }
    )
    discovery.resolver = lambda host: ["10.0.0.7"] if host == "jobs.lever.co" else [PUBLIC_IP]

    report = discovery.discover("https://company.example/careers")

    assert report.disposition == "unsafe_redirect"
    assert report.candidates == ()
    assert "private or reserved" in report.evidence[-1]
    assert all("jobs.lever.co" not in call[0] for call in fetcher.calls)


def test_records_missing_redirect_location_as_unsafe_without_following_it():
    discovery, fetcher = service(
        {
            "https://company.example/robots.txt": [response(404)],
            "https://company.example/careers": [response(302)],
        }
    )

    report = discovery.discover("https://company.example/careers")

    assert report.disposition == "unsafe_redirect"
    assert "Location header" in report.evidence[-1]
    assert [call[0] for call in fetcher.calls] == [
        "https://company.example/robots.txt",
        "https://company.example/careers",
    ]


def test_fails_closed_when_robots_fetch_fails():
    discovery, fetcher = service(
        {
            "https://company.example/robots.txt": [
                FetchFailure("response_too_large", "response exceeded the byte limit")
            ]
        }
    )

    report = discovery.discover("https://company.example/careers")

    assert report.disposition == "robots_denied"
    assert "failed closed" in report.evidence[-1]
    assert len(fetcher.calls) == 1
