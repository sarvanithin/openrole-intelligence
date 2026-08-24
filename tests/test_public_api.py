from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from fortune_intel.api import create_app
from fortune_intel.config import Settings
from fortune_intel.domain import SPONSORSHIP_RULE_VERSION, JobRecord, SponsorshipTier
from fortune_intel.seed import seed_demo
from fortune_intel.services.sponsorship import assess_sponsorship


def make_client(tmp_path, *, rate_limit=120):
    settings = Settings(
        database_path=tmp_path / "public.db",
        environment="test",
        allowed_hosts=("testserver",),
        rate_limit_per_minute=rate_limit,
        public_base_url="https://jobs.example.test",
    )
    app = create_app(settings=settings)
    seed_demo(app.state.repository)
    return TestClient(app), app


def test_public_responses_have_security_headers_and_pagination(tmp_path):
    client, _ = make_client(tmp_path)
    response = client.get("/api/jobs", params={"limit": 2})
    assert response.status_code == 200
    assert response.json()["count"] == 2
    assert response.json()["total"] == 4
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-request-id"]


def test_job_api_can_filter_by_employer_posted_date_without_using_first_seen(tmp_path):
    client, app = make_client(tmp_path)
    company_id = app.state.repository.upsert_company("Opening Date Check")
    today = datetime.now(UTC).date()
    for external_id, source_opened_at in (
        ("fresh", today.isoformat()),
        ("old", (today - timedelta(days=8)).isoformat()),
        ("missing", None),
    ):
        app.state.repository.upsert_job(
            company_id,
            JobRecord(
                company_name="Opening Date Check",
                title=f"Opening window {external_id}",
                url=f"https://jobs.example.test/opening-window/{external_id}",
                source="opening-date-test",
                external_job_id=external_id,
                location="Austin, TX",
                source_opened_at=source_opened_at,
            ),
            assess_sponsorship(""),
        )

    response = client.get(
        "/api/jobs",
        params={"q": "Opening window", "opened_within_days": 7},
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["title"] == "Opening window fresh"
    assert client.get("/api/jobs", params={"opened_within_days": 366}).status_code == 422


def test_methodology_reports_the_active_sponsorship_rule_version(tmp_path):
    client, _ = make_client(tmp_path)

    response = client.get("/api/methodology")

    assert response.status_code == 200
    assert response.json()["assessment_version"] == SPONSORSHIP_RULE_VERSION
    assert "job-specific" in response.json()["tiers"]["A"]


def test_tier_a_api_returns_only_job_specific_offers(tmp_path):
    client, app = make_client(tmp_path)
    company_id = app.state.repository.upsert_company("Evidence Check Inc.")
    for external_id, description in (
        ("offer", "Visa sponsorship is available for this position."),
        ("denial", "This position is not currently eligible for visa sponsorship."),
        ("clearance", "We will sponsor a security clearance for this position."),
    ):
        app.state.repository.upsert_job(
            company_id,
            JobRecord(
                company_name="Evidence Check Inc.",
                title=f"Evidence {external_id}",
                location="Austin, TX",
                url=f"https://jobs.example.test/evidence/{external_id}",
                source="evidence:test",
                external_job_id=external_id,
            ),
            assess_sponsorship(description),
        )

    records = client.get("/api/jobs", params={"q": "Evidence", "tier": "A"}).json()

    assert records["total"] == 1
    assert records["items"][0]["title"] == "Evidence offer"
    assert records["items"][0]["sponsorship_tier"] == SponsorshipTier.EXPLICIT_YES


def test_job_detail_is_explainable_and_does_not_mirror_full_description(tmp_path):
    client, _ = make_client(tmp_path)
    job_id = client.get("/api/jobs?tier=A").json()["items"][0]["id"]
    response = client.get(f"/api/jobs/{job_id}")
    assert response.status_code == 200
    detail = response.json()
    assert "description" not in detail
    assert detail["description_excerpt"]
    assert detail["sponsorship_rule_version"] == SPONSORSHIP_RULE_VERSION
    assert "employer_evidence" in detail
    assert detail["versions"]
    assert "posted_at" not in detail
    assert detail["source_opened_at"]
    assert detail["display_date"] == detail["source_opened_at"]
    assert detail["date_provenance"] == "source_opened_at"


def test_job_api_explicitly_labels_first_observation_fallback(tmp_path):
    client, app = make_client(tmp_path)
    company_id = app.state.repository.upsert_company("Observed Only")
    job_id = app.state.repository.upsert_job(
        company_id,
        JobRecord(
            company_name="Observed Only",
            title="Platform Engineer",
            url="https://jobs.example.test/observed-only",
            source="greenhouse:observed",
            external_job_id="observed-only",
            location="Remote — United States",
        ),
        assess_sponsorship(""),
    )

    listing = client.get("/api/jobs", params={"q": "Platform Engineer"}).json()["items"][0]
    detail = client.get(f"/api/jobs/{job_id}").json()

    for record in (listing, detail):
        assert "posted_at" not in record
        assert record["source_opened_at"] is None
        assert record["display_date"] == record["first_seen_at"]
        assert record["date_provenance"] == "first_seen_at"


def test_public_api_fails_closed_for_non_us_and_ambiguous_locations(tmp_path):
    client, app = make_client(tmp_path)
    repository = app.state.repository
    company_id = repository.upsert_company("Geo Company")
    ids = {}
    for external_id, location in (
        ("us", "Austin, TX"),
        ("canada", "Toronto, Ontario, Canada"),
        ("unknown", "Remote"),
    ):
        ids[external_id] = repository.upsert_job(
            company_id,
            JobRecord(
                company_name="Geo Company",
                title=f"Geo Fence {external_id}",
                location=location,
                url=f"https://jobs.example.test/geo/{external_id}",
                source="greenhouse:geo",
                external_job_id=external_id,
            ),
            assess_sponsorship(""),
        )

    payload = client.get("/api/jobs", params={"q": "Geo Fence"}).json()

    assert payload["total"] == 1
    assert payload["items"][0]["id"] == ids["us"]
    assert payload["country_scope"].startswith("United States")
    assert client.get(f"/api/jobs/{ids['canada']}").status_code == 404
    assert client.get(f"/api/jobs/{ids['unknown']}").status_code == 404
    company = next(
        item
        for item in client.get("/api/companies?q=Geo Company").json()["items"]
        if item["name"] == "Geo Company"
    )
    assert company["active_jobs"] == 1


def test_dashboard_uses_provenance_aware_date_labels(tmp_path):
    client, _ = make_client(tmp_path)

    script = client.get("/assets/app.js").text

    assert "Opened ${value}" in script
    assert "First observed ${value}" in script


def test_dashboard_hidden_empty_state_cannot_display_with_results(tmp_path):
    client, _ = make_client(tmp_path)

    dashboard = client.get("/").text
    stylesheet = client.get("/assets/styles.css").text
    script = client.get("/assets/app.js").text

    assert 'id="empty-state" class="empty" hidden' in dashboard
    assert "[hidden] { display: none !important; }" in stylesheet
    assert "empty.hidden = true;" in script
    assert "empty.hidden = data.items.length !== 0;" in script
    assert "/assets/styles.css?v=8" in dashboard
    assert 'id="opened-within"' in dashboard
    assert "/assets/app.js?v=8" in dashboard
    assert 'params.set("opened_within_days", openedWithin);' in script


def test_source_status_does_not_leak_internal_urls_or_errors(tmp_path):
    client, app = make_client(tmp_path)
    company = app.state.repository.list_companies()[0]
    source_id = app.state.repository.upsert_career_source(
        company["id"],
        kind="greenhouse",
        board_token="private-tenant-name",
        base_url="https://boards.greenhouse.io/private-tenant-name",
    )
    app.state.repository.mark_source_finished(
        source_id, success=False, error="sensitive debug path"
    )
    item = client.get("/api/sources/status").json()["items"][0]
    assert "last_error" not in item
    assert "base_url" not in item
    assert item["healthy"] is False


def test_rate_limit_rejects_excess_api_requests(tmp_path):
    client, _ = make_client(tmp_path, rate_limit=10)
    for _ in range(10):
        assert client.get("/api/stats").status_code == 200
    response = client.get("/api/stats")
    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"


def test_production_configuration_fails_closed():
    try:
        Settings(environment="production", allowed_hosts=("*",)).validate()
    except ValueError as error:
        assert "explicit" in str(error)
    else:
        raise AssertionError("unsafe production settings should fail")


def test_trusted_host_rejects_unknown_host(tmp_path):
    client, _ = make_client(tmp_path)
    response = client.get("/api/health", headers={"Host": "attacker.example"})
    assert response.status_code == 400


def test_api_guide_is_self_hosted_under_strict_csp(tmp_path):
    client, _ = make_client(tmp_path)
    response = client.get("/docs")
    assert response.status_code == 200
    assert "cdn.jsdelivr" not in response.text
    assert "/api/openapi.json" in response.text
    assert "script-src 'self'" in response.headers["content-security-policy"]


def test_h1b_employer_directory_is_searchable_and_explains_limits(tmp_path):
    client, app = make_client(tmp_path)
    app.state.repository.upsert_h1b_employer(
        "Example Legal Employer LLC",
        fiscal_year=2026,
        lca_worker_positions=42,
        source="dol_lca",
        source_url="https://www.dol.gov/agencies/eta/foreign-labor/performance",
        source_document="LCA_Disclosure_Data_FY2026_Q2.xlsx",
        source_checksum="a" * 64,
        imported_at="2026-08-06T12:00:00+00:00",
    )

    response = client.get("/api/h1b-employers", params={"q": "Legal Employer"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["lca_worker_positions"] == 42
    assert payload["summary"] == {
        "employers": 1,
        "latest_fiscal_year": 2026,
        "worker_positions": 42,
    }
    assert "not sponsorship guarantees" in payload["disclaimer"]
    page = client.get("/h1b-employers")
    assert page.status_code == 200
    assert "with successful current job feeds" in page.text


def test_company_directory_searches_the_complete_company_universe(tmp_path):
    client, app = make_client(tmp_path)
    app.state.repository.upsert_company("A Company Beyond Old Offset")

    response = client.get("/api/companies", params={"q": "beyond old"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["name"] == "A Company Beyond Old Offset"
    assert client.get("/api/companies", params={"offset": 8000}).status_code == 200
    assert client.get("/companies").status_code == 200


def test_forwarded_for_header_cannot_rotate_rate_limit_identity(tmp_path):
    client, _ = make_client(tmp_path, rate_limit=10)
    for index in range(10):
        response = client.get("/api/stats", headers={"X-Forwarded-For": f"203.0.113.{index}"})
        assert response.status_code == 200
    assert client.get("/api/stats", headers={"X-Forwarded-For": "198.51.100.44"}).status_code == 429


def test_production_filters_synthetic_records_and_fails_readiness(tmp_path):
    database = tmp_path / "production.db"
    setup = create_app(
        settings=Settings(
            database_path=database,
            environment="test",
            allowed_hosts=("jobs.example.test",),
            public_base_url="https://jobs.example.test",
        )
    )
    seed_demo(setup.state.repository)
    company_id = setup.state.repository.upsert_company("Real Company")
    real_id = setup.state.repository.upsert_job(
        company_id,
        JobRecord(
            company_name="Real Company",
            title="Platform Engineer",
            url="https://jobs.example.test/real-1",
            source="greenhouse:real",
            external_job_id="real-1",
            location="Austin, TX",
            description="Build reliable systems.",
        ),
        assess_sponsorship("Build reliable systems."),
    )
    production = create_app(
        settings=Settings(
            database_path=database,
            environment="production",
            allowed_hosts=("jobs.example.test",),
            public_base_url="https://jobs.example.test",
            contact_email="support@example.test",
            show_synthetic=False,
        )
    )
    with TestClient(production, base_url="https://jobs.example.test") as client:
        listing = client.get("/api/jobs").json()
        companies = client.get("/api/companies").json()
        assert listing["total"] == 1
        assert listing["items"][0]["id"] == real_id
        assert companies["total"] == 1
        assert set(companies["items"][0]) == {
            "id",
            "name",
            "slug",
            "ats_type",
            "sec_cik",
            "ticker",
            "active_jobs",
            "last_verified_at",
            "coverage_disposition",
            "approved_sources",
            "source_last_success_at",
        }
        assert client.get("/readyz").status_code == 503


def test_public_coverage_denominator_does_not_equate_listing_with_success(tmp_path):
    client, app = make_client(tmp_path)
    app.state.repository.upsert_company("Listed But Unchecked")

    coverage = client.get("/api/coverage").json()
    company = client.get("/api/companies", params={"q": "Listed But Unchecked"}).json()

    assert coverage["companies"] == 5
    assert coverage["companies_with_successful_sources"] == 0
    assert "does not mean its jobs were checked" in coverage["definition"]
    assert company["items"][0]["coverage_disposition"] == "unreviewed"
    assert company["items"][0]["source_last_success_at"] is None


def test_public_coverage_separates_verified_seed_from_successful_source(tmp_path):
    client, app = make_client(tmp_path)
    repository = app.state.repository
    company_id = repository.upsert_company(
        "Verified Seed Only", website_url="https://verified-seed.example/"
    )
    coverage = repository.get_company_coverage(company_id)
    repository.set_company_disposition(
        company_id,
        coverage["disposition"],
        reason="Canonical website seed verified from reviewed SEC filing evidence",
        actor="verification-test",
    )

    public_coverage = client.get("/api/coverage").json()

    assert public_coverage["companies_with_verified_discovery_seeds"] == 1
    assert public_coverage["companies_with_successful_sources"] == 0
    assert "not a successful job source" in public_coverage["verified_seed_definition"]


def test_public_coverage_reports_exact_h1b_seed_and_source_progress(tmp_path):
    client, app = make_client(tmp_path)
    repository = app.state.repository
    company_id = repository.upsert_company(
        "Exact H1B Company", website_url="https://exact-h1b.example/"
    )
    repository.set_company_disposition(
        company_id,
        "unreviewed",
        reason="Canonical website seed verified from https://evidence.example/company",
        actor="verification-test",
    )
    repository.record_sponsorship_fact(
        company_id,
        fiscal_year=2026,
        source="dol_lca",
        lca_worker_positions=12,
        entity_match_confidence=1.0,
        match_method="reviewed_exact_legal_name",
    )

    coverage = client.get("/api/coverage").json()

    assert coverage["exact_h1b_companies"] == 1
    assert coverage["exact_h1b_with_verified_discovery_seeds"] == 1
    assert coverage["exact_h1b_with_successful_sources"] == 0
    assert "not a guarantee" in coverage["exact_h1b_definition"]


def test_public_coverage_exposes_passive_platform_inventory_without_approval(tmp_path):
    client, app = make_client(tmp_path)
    company_id = app.state.repository.upsert_company("Fingerprint Company")
    app.state.repository.upsert_source_fingerprint(
        company_id,
        observed_url="https://careers.example.icims.com/jobs/search",
        family="icims",
        evidence={"origin_page": "https://example.test/careers"},
        actor="inventory-test",
    )

    coverage = client.get("/api/coverage").json()

    assert coverage["passive_platform_inventory"] == [
        {
            "family": "icims",
            "companies": 1,
            "urls": 1,
            "observations": 1,
            "last_seen_at": coverage["passive_platform_inventory"][0]["last_seen_at"],
        }
    ]
    assert "not approved or schedulable" in coverage["passive_inventory_definition"]
