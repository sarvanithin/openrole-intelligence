"""Hardened read-only FastAPI application for the public beta."""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from fastapi import FastAPI, HTTPException, Query
from fastapi import Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from fortune_intel.config import Settings
from fortune_intel.domain import SPONSORSHIP_RULE_VERSION
from fortune_intel.security import PublicSecurityMiddleware
from fortune_intel.services.us_location import LOCATION_RULE_VERSION
from fortune_intel.storage import JobRepository
from fortune_intel.storage.coverage_audit_ops import (
    COVERAGE_AUDIT_DEFINITION,
    coverage_audit_summary,
)

COUNTRY_SCOPE = "United States (50 states and Washington, DC)"


def create_app(
    database_path: str | Path | None = None,
    *,
    settings: Settings | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env(database_path=database_path)
    repository = JobRepository(settings.database_path)
    repository.initialize()
    app = FastAPI(
        title="OpenRole Intelligence API",
        version="0.2.0",
        description="Read-only job freshness and explainable sponsorship evidence.",
        openapi_url="/api/openapi.json",
        docs_url=None,
        redoc_url=None,
    )
    app.state.repository = repository
    app.state.settings = settings
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["GET"],
            allow_headers=["Accept", "Content-Type", "X-Request-ID"],
        )
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(PublicSecurityMiddleware, settings=settings)

    @app.get("/healthz", include_in_schema=False)
    def liveness() -> dict[str, object]:
        return {"status": "ok", "version": app.version}

    @app.get("/api/health")
    @app.get("/readyz", include_in_schema=False)
    def readiness() -> dict[str, object]:
        try:
            result = repository.readiness(
                production=settings.environment == "production",
                deep=False,
            )
        except Exception as error:
            raise HTTPException(status_code=503, detail="database unavailable") from error
        if not result["ready"]:
            raise HTTPException(status_code=503, detail=result)
        return {"status": "ready", "version": app.version, **result}

    @app.get("/api/stats")
    def stats() -> dict[str, object]:
        result = repository.overview(include_synthetic=settings.show_synthetic, us_only=True)
        result["h1b_employers"] = repository.h1b_overview()["employers"]
        result["country_scope"] = COUNTRY_SCOPE
        return result

    @app.get("/api/coverage")
    def coverage() -> dict[str, object]:
        result = repository.coverage_overview(include_synthetic=settings.show_synthetic)
        return {
            **result,
            "passive_platform_inventory": repository.source_fingerprint_inventory(),
            "passive_inventory_definition": (
                "Observed career-platform fingerprints are evidence for connector planning "
                "only; they are not approved or schedulable sources."
            ),
            "verified_seed_definition": (
                "A verified discovery seed is an official website or career URL backed by "
                "reviewed SEC or exact-CIK Wikidata provenance. It is not a successful job "
                "source until a policy-approved connector completes a manifest."
            ),
            "exact_h1b_definition": (
                "Exact H-1B companies have positive official DOL activity linked to the "
                "company only through a reviewed exact legal-entity match. This is employer "
                "history, not a guarantee for any current role."
            ),
            "definition": (
                "A company is successfully covered only after an approved career source "
                "has completed at least one successful manifest. Directory inclusion alone "
                "does not mean its jobs were checked. Public job counts include definite "
                "U.S. locations only."
            ),
            "country_scope": COUNTRY_SCOPE,
        }

    @app.get("/api/coverage/companies")
    def company_coverage_audit(
        q: str = Query(default="", max_length=120),
        status: str = Query(default="all", pattern="^(all|covered|incomplete)$"),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, object]:
        records = repository.company_coverage_audit(
            include_synthetic=settings.show_synthetic,
            query=q,
            audit_status=status,
        )
        public_fields = (
            "company_id",
            "company_name",
            "company_slug",
            "coverage_disposition",
            "identity_verified",
            "portal_seed_verified",
            "ats_candidate_discovered",
            "complete_manifest_approved",
            "successful_platform_ingestion",
            "opening_date_provenance_complete",
            "fresh",
            "covered",
            "completed_gates",
            "total_gates",
            "next_action",
            "approved_sources",
            "active_jobs",
            "first_seen_fallback_jobs",
            "last_complete_ingestion_at",
            "audit_as_of",
        )
        page = records[offset : offset + limit]
        return {
            "items": [{key: record[key] for key in public_fields} for record in page],
            "count": len(page),
            "total": len(records),
            "limit": limit,
            "offset": offset,
            "summary": coverage_audit_summary(records),
            "definition": COVERAGE_AUDIT_DEFINITION,
            "country_scope": COUNTRY_SCOPE,
        }

    @app.get("/api/h1b-employers")
    def h1b_employers(
        q: str = Query(default="", max_length=120),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, object]:
        records = repository.list_h1b_employers(query=q, limit=limit, offset=offset)
        return {
            "items": records,
            "count": len(records),
            "total": repository.count_h1b_employers(query=q),
            "limit": limit,
            "offset": offset,
            "summary": repository.h1b_overview(),
            "disclaimer": "Certified LCA worker positions are historical employer filings, not sponsorship guarantees or unique hires.",
        }

    @app.get("/api/jobs")
    def jobs(
        q: str = Query(default="", max_length=80),
        company: str = Query(default="", max_length=120),
        location: str = Query(default="", max_length=120),
        tier: str = Query(default="", pattern="^[A-Ea-e]?$"),
        opened_within_days: int = Query(default=0, ge=0, le=365),
        verified_within_hours: int = Query(default=0, ge=0, le=168),
        sort: str = Query(default="newest", pattern="^(newest|verified)$"),
        status: str = Query(default="active", pattern="^(active|closed)$"),
        limit: int = Query(default=50, ge=1, le=50),
        offset: int = Query(default=0, ge=0, le=5000),
    ) -> dict[str, object]:
        filters = {
            "query": q,
            "company": company,
            "location": location,
            "tier": tier,
            "opened_within_days": opened_within_days,
            "verified_within_hours": verified_within_hours,
            "sort": sort,
            "status": status,
            "include_synthetic": settings.show_synthetic,
            "us_eligibility": "eligible",
        }
        records = repository.list_jobs(**filters, limit=limit, offset=offset)
        count_filters = {key: value for key, value in filters.items() if key != "sort"}
        return {
            "items": records,
            "count": len(records),
            "total": repository.count_jobs(**count_filters),
            "limit": limit,
            "offset": offset,
            "country_scope": COUNTRY_SCOPE,
        }

    @app.get("/api/jobs/{job_id}")
    def job_detail(
        job_id: str = PathParam(pattern="^[a-f0-9]{24}$"),
    ) -> dict[str, object]:
        record = repository.get_job(job_id)
        if (
            record is None
            or record["us_eligibility"] != "eligible"
            or (record["is_synthetic"] and not settings.show_synthetic)
        ):
            raise HTTPException(status_code=404, detail="job not found")
        description = record["description"]
        return {
            "id": record["id"],
            "title": record["title"],
            "url": record["canonical_url"],
            "location": record["location"],
            "country_scope": COUNTRY_SCOPE,
            "source_opened_at": record["posted_at"],
            "first_seen_at": record["first_seen_at"],
            "display_date": record["posted_at"] or record["first_seen_at"],
            "date_provenance": ("source_opened_at" if record["posted_at"] else "first_seen_at"),
            "last_seen_at": record["last_seen_at"],
            "status": record["status"],
            "company_name": record["company_name"],
            "company_slug": record["company_slug"],
            "sponsorship_tier": record["sponsorship_tier"],
            "sponsorship_evidence_score": record["sponsorship_evidence_score"],
            "sponsorship_reasons": record["sponsorship_reasons"],
            "sponsorship_excerpt": record["sponsorship_excerpt"],
            "sponsorship_rule_version": record["sponsorship_rule_version"],
            "description_excerpt": description[:1000],
            "description_truncated": len(description) > 1000,
            "employer_evidence": [
                {
                    key: fact[key]
                    for key in (
                        "source",
                        "fiscal_year",
                        "initial_approvals",
                        "initial_denials",
                        "lca_worker_positions",
                        "entity_match_confidence",
                        "source_url",
                        "imported_at",
                    )
                }
                for fact in record["employer_evidence"]
            ],
            "versions": [{"observed_at": item["observed_at"]} for item in record["versions"]],
        }

    @app.get("/api/companies")
    def companies(
        q: str = Query(default="", max_length=120),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=100000),
    ) -> dict[str, object]:
        records = repository.list_companies(include_synthetic=settings.show_synthetic, us_only=True)
        query = q.strip().casefold()
        if query:
            records = [record for record in records if query in record["name"].casefold()]
        public = [
            {
                key: record[key]
                for key in (
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
                )
            }
            for record in records[offset : offset + limit]
        ]
        return {
            "items": public,
            "count": len(public),
            "total": len(records),
            "limit": limit,
            "offset": offset,
            "country_scope": COUNTRY_SCOPE,
        }

    @app.get("/api/sources/status")
    def source_status() -> dict[str, object]:
        records = repository.source_status()
        public_records = [
            {key: value for key, value in record.items() if key not in {"last_error", "base_url"}}
            | {"healthy": not record["consecutive_failures"]}
            for record in records
        ]
        return {"items": public_records, "count": len(public_records)}

    @app.get("/api/methodology")
    def methodology() -> dict[str, object]:
        return {
            "assessment_version": SPONSORSHIP_RULE_VERSION,
            "location_rule_version": LOCATION_RULE_VERSION,
            "country_scope": COUNTRY_SCOPE,
            "location_policy": (
                "Only roles with definite evidence for a location in a U.S. state or "
                "Washington, DC are public. Ambiguous, worldwide, non-U.S., and U.S. "
                "territory-only locations fail closed."
            ),
            "tiers": {
                "A": "explicit, job-specific immigration sponsorship offer detected",
                "B": "strong recent reviewed employer history",
                "C": "some reviewed employer history",
                "D": "insufficient, conditional, or ambiguous evidence",
                "E": "explicit current-posting sponsorship denial detected",
            },
            "disclaimer": "Evidence is not a sponsorship guarantee or legal advice.",
        }

    web_root = Path(__file__).with_name("web")
    app.mount("/assets", StaticFiles(directory=web_root), name="assets")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(web_root / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/trust", include_in_schema=False)
    def trust_page() -> FileResponse:
        return FileResponse(
            web_root / "trust.html", headers={"Cache-Control": "public, max-age=300"}
        )

    @app.get("/docs", include_in_schema=False)
    def api_guide() -> FileResponse:
        return FileResponse(web_root / "api.html", headers={"Cache-Control": "public, max-age=300"})

    @app.get("/h1b-employers", include_in_schema=False)
    def h1b_employer_page() -> FileResponse:
        return FileResponse(web_root / "h1b.html", headers={"Cache-Control": "public, max-age=300"})

    @app.get("/companies", include_in_schema=False)
    def company_page() -> FileResponse:
        return FileResponse(
            web_root / "companies.html", headers={"Cache-Control": "public, max-age=300"}
        )

    @app.get("/robots.txt", include_in_schema=False)
    def robots() -> PlainTextResponse:
        return PlainTextResponse("User-agent: *\nAllow: /\n")

    @app.get("/sitemap.xml", include_in_schema=False)
    def sitemap() -> Response:
        url = xml_escape(settings.public_base_url)
        pages = "".join(
            f"<url><loc>{url}{path}</loc></url>"
            for path in ("/", "/companies", "/h1b-employers", "/trust", "/docs")
        )
        body = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{pages}</urlset>'
        return Response(body, media_type="application/xml")

    return app
