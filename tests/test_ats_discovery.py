import pytest

from fortune_intel.discovery import classify_ats_url, discover_ats_sources


@pytest.mark.parametrize(
    ("url", "kind", "token", "base_url"),
    [
        (
            "https://www.amazon.jobs/en/search?base_query=data&loc_query=United%20States",
            "amazon_jobs",
            "us",
            "https://www.amazon.jobs/en/search?country=USA",
        ),
        (
            "https://jobs.apple.com/en-us/search?search=data",
            "apple_jobs",
            "us",
            "https://jobs.apple.com/en-us/search?location=united-states-USA",
        ),
        (
            "https://job-boards.greenhouse.io/Example-Co/jobs/123?gh_src=test#apply",
            "greenhouse",
            "Example-Co",
            "https://boards.greenhouse.io/Example-Co",
        ),
        (
            "https://boards.greenhouse.io/embed/job_board?for=example_inc",
            "greenhouse",
            "example_inc",
            "https://boards.greenhouse.io/example_inc",
        ),
        (
            "https://boards.greenhouse.io/embed/job_app?for=example_inc&token=123",
            "greenhouse",
            "example_inc",
            "https://boards.greenhouse.io/example_inc",
        ),
        (
            "https://boards-api.greenhouse.io/v1/boards/example/jobs?content=true",
            "greenhouse",
            "example",
            "https://boards.greenhouse.io/example",
        ),
        (
            "https://jobs.lever.co/example/01abc",
            "lever",
            "example",
            "https://jobs.lever.co/example",
        ),
        (
            "https://api.eu.lever.co/v0/postings/example?mode=json",
            "lever",
            "example",
            "https://jobs.eu.lever.co/example",
        ),
        (
            "https://jobs.ashbyhq.com/example/1234",
            "ashby",
            "example",
            "https://jobs.ashbyhq.com/example",
        ),
        (
            "https://api.ashbyhq.com/posting-api/job-board/example",
            "ashby",
            "example",
            "https://jobs.ashbyhq.com/example",
        ),
        (
            "https://careers.smartrecruiters.com/ExampleInc",
            "smartrecruiters",
            "ExampleInc",
            "https://jobs.smartrecruiters.com/ExampleInc",
        ),
        (
            "https://api.smartrecruiters.com/v1/companies/ExampleInc/postings",
            "smartrecruiters",
            "ExampleInc",
            "https://jobs.smartrecruiters.com/ExampleInc",
        ),
        (
            "https://acme.wd5.myworkdayjobs.com/External/job/US-NY/Engineer_JR-1",
            "workday",
            "acme.wd5.myworkdayjobs.com|acme|External",
            "https://acme.wd5.myworkdayjobs.com/External",
        ),
        (
            "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External/jobs",
            "workday",
            "acme.wd5.myworkdayjobs.com|acme|External",
            "https://acme.wd5.myworkdayjobs.com/External",
        ),
        (
            "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/US-CA/Role_JR1",
            "workday",
            "nvidia.wd5.myworkdayjobs.com|nvidia|NVIDIAExternalCareerSite",
            "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite",
        ),
        (
            "https://wd1.myworkdaysite.com/recruiting/snapchat/snap",
            "workday",
            "wd1.myworkdaysite.com|snapchat|snap",
            "https://wd1.myworkdaysite.com/recruiting/snapchat/snap",
        ),
        (
            "https://wd5.myworkdaysite.com/recruiting/chewy/External/"
            "job/United-States/Senior-Engineer_R123",
            "workday",
            "wd5.myworkdaysite.com|chewy|External",
            "https://wd5.myworkdaysite.com/recruiting/chewy/External",
        ),
        (
            "https://wd5.myworkdaysite.com/wday/cxs/chewy/External/jobs",
            "workday",
            "wd5.myworkdaysite.com|chewy|External",
            "https://wd5.myworkdaysite.com/recruiting/chewy/External",
        ),
        (
            "https://edxn.fa.us2.oraclecloud.com/hcmUI/"
            "CandidateExperience/en/sites/CX_4001/job/26069",
            "oracle_recruiting",
            "edxn.fa.us2.oraclecloud.com|en|CX_4001",
            "https://edxn.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_4001",
        ),
    ],
)
def test_classifies_supported_url_variants(url, kind, token, base_url):
    candidate = classify_ats_url(url)

    assert candidate is not None
    assert candidate.connector_kind == kind
    assert candidate.board_token == token
    assert candidate.normalized_base_url == base_url
    assert candidate.confidence >= 0.98
    assert candidate.evidence


def test_extracts_iframes_and_links_from_html_and_deduplicates_sources():
    html = """
        <a href="https://jobs.lever.co/acme">Jobs</a>
        <a href="https://api.lever.co/v0/postings/acme">Jobs JSON</a>
        <iframe src="https://boards.greenhouse.io/embed/job_board?for=other"></iframe>
        <form action="https://jobs.ashbyhq.com/third"></form>
    """

    results = discover_ats_sources(html=html, page_url="https://company.example/careers")

    assert [(item.connector_kind, item.board_token) for item in results] == [
        ("lever", "acme"),
        ("ashby", "third"),
        ("greenhouse", "other"),
    ]
    assert results[0].confidence == 1.0
    assert "HTML href attribute" in results[0].evidence


def test_extracts_exact_ats_urls_from_explicit_data_attributes_and_json_state():
    html = """
        <button data-career-url="https://jobs.ashbyhq.com/acme">Open roles</button>
        <script id="__NEXT_DATA__" type="application/json">
          {"props":{"pageProps":{"jobBoard":"https://jobs.lever.co/acme"}}}
        </script>
        <script>window.not_json = 'https://jobs.lever.co/not-observed';</script>
    """

    results = discover_ats_sources(html=html, page_url="https://company.example/careers")

    assert [(item.connector_kind, item.board_token) for item in results] == [
        ("ashby", "acme"),
        ("lever", "acme"),
    ]
    assert any("HTML data-career-url attribute" in reason for reason in results[0].evidence)
    assert any("application/json" in reason for reason in results[1].evidence)


def test_resolves_relative_html_link_only_when_page_is_on_known_ats_host():
    results = discover_ats_sources(
        html='<a href="/acme/jobs/123">opening</a>',
        page_url="https://jobs.lever.co/current",
    )

    assert len(results) == 1
    assert results[0].board_token == "acme"


def test_smartrecruiters_applicant_account_route_is_never_a_company_board():
    url = "https://jobs.smartrecruiters.com/my-applications"

    assert classify_ats_url(url) is None
    assert (
        discover_ats_sources(
            html=f'<a href="{url}">My applications</a>',
            page_url="https://www.example.test/careers",
        )
        == ()
    )


def test_smartrecruiters_reserved_route_match_is_exact():
    candidate = classify_ats_url(
        "https://jobs.smartrecruiters.com/my-applications-software/jobs/123"
    )

    assert candidate is not None
    assert candidate.board_token == "my-applications-software"


@pytest.mark.parametrize(
    "url",
    [
        "https://boards.greenhouse.io/embed",
        "https://boards.greenhouse.io/jobs",
        "https://boards.greenhouse.io/search",
        "https://boards.greenhouse.io/SEARCH/jobs/123",
        "https://jobs.smartrecruiters.com/MY-APPLICATIONS",
        "https://careers.smartrecruiters.com/my-applications",
    ],
)
def test_reserved_web_routes_are_not_promoted_to_board_tokens(url):
    assert classify_ats_url(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "https://jobs.lever.co.evil.example/acme",
        "https://user@jobs.lever.co/acme",
        "https://jobs.lever.co:8443/acme",
        "https://jobs.lever.co:invalid/acme",
        "https://jobs.lever.co/%2Fadmin",
        "https://boards.greenhouse.io/embed/job_app?token=123",
        "https://boards-api.greenhouse.io/v1/boards/example/not-jobs",
        "https://api.smartrecruiters.com/v1/companies/example/private",
        "https://careers-example.icims.com/jobs/123/software-engineer/job",
        "https://api.icims.com/jobs/search",
        "https://careers-example.icims.com/private/search",
        "https://careers-example.icims.com.evil.example/jobs/search",
        "http://careers-example.icims.com/jobs/search",
        "https://acme.wd5.myworkdayjobs.com/wday/cxs/other/External/jobs",
        "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/%2FExternal/jobs",
        "https://acme.wd5.myworkdayjobs.com.evil.example/External",
        "https://user@acme.wd5.myworkdayjobs.com/External",
        "https://snapchat.wd1.myworkdaysite.com/recruiting/snapchat/snap",
        "https://wd1.myworkdaysite.com.evil.example/recruiting/snapchat/snap",
        "https://user@wd1.myworkdaysite.com/recruiting/snapchat/snap",
        "http://wd1.myworkdaysite.com/recruiting/snapchat/snap",
        "https://wd1.myworkdaysite.com:443/recruiting/snapchat/snap",
        "https://wd1.myworkdaysite.com/recruiting/snapchat/snap?source=company",
        "https://wd1.myworkdaysite.com/recruiting/snapchat/snap#jobs",
        "https://wd1.myworkdaysite.com/recruiting/snapchat/snap?",
        "https://wd1.myworkdaysite.com/recruiting/snapchat/snap#",
        "https://wd1.myworkdaysite.com/recruiting/snapchat",
        "https://wd1.myworkdaysite.com/recruiting/snapchat/snap/private",
        "https://wd1.myworkdaysite.com/recruiting//snapchat/snap",
        "https://wd1.myworkdaysite.com/recruiting/snapchat/%2e%2e/job/Role_R1",
        "https://wd1.myworkdaysite.com/recruiting/snapchat/%2Fsneaky/job/Role_R1",
        "https://wd1.myworkdaysite.com/wday/cxs/snapchat/snap/jobs/private",
        "https://wd1.myworkdaysite.com/wday/cxs/snapchat/snap/job",
        "https://tenant.fa.oraclecloud.com.evil.example/hcmUI/"
        "CandidateExperience/en/sites/CX_1/jobs",
        "https://user@tenant.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs",
        "https://tenant.fa.oraclecloud.com/hcmUI/CandidateExperience/../sites/CX_1/jobs",
        "https://tenant.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/%2FCX_1/jobs",
        "https://tenant.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/private",
        "https://company.example/careers",
    ],
)
def test_rejects_unknown_or_unsafe_url_shapes(url):
    assert classify_ats_url(url) is None


def test_normalizes_http_but_reduces_confidence_and_explains_it():
    candidate = classify_ats_url("http://jobs.lever.co/acme")

    assert candidate is not None
    assert candidate.normalized_base_url == "https://jobs.lever.co/acme"
    assert candidate.confidence == 0.94
    assert any("requires verification" in reason for reason in candidate.evidence)


def test_extracts_workday_career_link_from_company_html():
    results = discover_ats_sources(
        html=('<a href="https://acme.wd5.myworkdayjobs.com/External/">Search openings</a>'),
        page_url="https://www.acme.example/careers",
    )

    assert len(results) == 1
    assert results[0].connector_kind == "workday"
    assert results[0].board_token == "acme.wd5.myworkdayjobs.com|acme|External"
    assert results[0].normalized_base_url == "https://acme.wd5.myworkdayjobs.com/External"


def test_extracts_recruiting_path_workday_link_from_company_html():
    results = discover_ats_sources(
        html=(
            '<a href="https://wd5.myworkdaysite.com/recruiting/chewy/External">'
            "Search openings</a>"
        ),
        page_url="https://www.chewy.example/careers",
    )

    assert len(results) == 1
    assert results[0].connector_kind == "workday"
    assert results[0].board_token == "wd5.myworkdaysite.com|chewy|External"
    assert results[0].normalized_base_url == (
        "https://wd5.myworkdaysite.com/recruiting/chewy/External"
    )


def test_extracts_oracle_recruiting_link_from_verified_company_html():
    results = discover_ats_sources(
        html=(
            '<a href="https://jpmc.fa.oraclecloud.com/hcmUI/'
            'CandidateExperience/en/sites/CX_1001/requisitions">Search openings</a>'
        ),
        page_url="https://www.jpmorganchase.com/careers",
    )

    assert len(results) == 1
    assert results[0].connector_kind == "oracle_recruiting"
    assert results[0].board_token == "jpmc.fa.oraclecloud.com|en|CX_1001"
    assert results[0].normalized_base_url == (
        "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001"
    )


def test_extracts_exact_unfiltered_icims_search_url_from_company_html():
    results = discover_ats_sources(
        html='<a href="https://careers-acme.icims.com/jobs/search">Search openings</a>',
        page_url="https://www.acme.example/careers",
    )

    assert len(results) == 1
    assert results[0].connector_kind == "icims_public"
    assert results[0].board_token == "careers-acme.icims.com"
    assert "Exact unfiltered iCIMS" in results[0].evidence[0]
