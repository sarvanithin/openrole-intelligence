"""Bridge the existing ATS collectors into the normalized repository."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

from fortune_intel.domain import JobRecord, canonicalize_url
from fortune_intel.services.sponsorship import assess_sponsorship
from fortune_intel.storage import JobRepository


@dataclass(frozen=True)
class CompanySource:
    name: str
    career_url: str
    ats_type: str = ""
    manifest_complete: bool = False

    def __post_init__(self) -> None:
        parsed = urlsplit(self.career_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("career_url must be an absolute HTTP(S) URL")
        if parsed.hostname.casefold().rstrip(".") in {
            "localhost",
            "metadata.google.internal",
            "metadata.google",
        }:
            raise ValueError("career_url cannot target a private or local network")
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            return
        if not address.is_global:
            raise ValueError("career_url cannot target a private or local network")


def _validate_resolved_host(url: str) -> None:
    """Reject DNS names that currently resolve to non-public addresses."""

    host = urlsplit(url).hostname
    assert host is not None
    for result in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP):
        address = ipaddress.ip_address(result[4][0].split("%", 1)[0])
        if not address.is_global:
            raise ValueError("career_url resolves to a private or local network")


async def sync_company(repository: JobRepository, company: CompanySource) -> dict[str, object]:
    """Run one legacy collector with lifecycle-safe persistence."""

    # Import lazily so database/API users do not require Playwright at startup.
    from scraper.dispatcher import ScraperDispatcher

    try:
        await asyncio.to_thread(_validate_resolved_host, company.career_url)
    except (OSError, ValueError) as error:
        return {
            "company": company.name,
            "status": "rejected_source",
            "error": str(error),
        }
    company_id = repository.upsert_company(
        company.name, career_url=company.career_url, ats_type=company.ats_type
    )
    ats_type = company.ats_type or ScraperDispatcher.detect_platform(company.career_url)
    # Include the approved board URL in identity because ATS IDs can overlap
    # across tenants, including multiple boards owned by one organization.
    source = f"{ats_type.casefold()}:{canonicalize_url(company.career_url)}"
    run_id = repository.start_sync_run(source, company_id)
    try:
        scraper = ScraperDispatcher.get_scraper(
            company.name, company.career_url, platform_hint=company.ats_type or None
        )
        scraped_jobs = await scraper.scrape()
        if not scraped_jobs:
            repository.finish_sync_run(
                run_id,
                status="anomalous_empty",
                complete=False,
                jobs_seen=0,
                error_message="Empty result is not treated as a complete manifest",
            )
            return {"company": company.name, "status": "anomalous_empty", "jobs": 0}

        history = repository.get_employer_history(company_id)
        seen: list[str] = []
        for raw in scraped_jobs:
            # Legacy job_id includes the mutable title. The canonical URL is a
            # safer fallback identity until each adapter exposes its native ID.
            external_id = canonicalize_url(raw.job_url)
            record = JobRecord(
                company_name=company.name,
                title=raw.job_title,
                url=raw.job_url,
                source=source,
                external_job_id=external_id,
                location=raw.location,
                source_opened_at=raw.posted_date or None,
                metadata={
                    "keywords_matched": raw.keywords_matched,
                    "source_opened_at_field": "legacy_scraper.posted_date"
                    if raw.posted_date
                    else None,
                },
            )
            repository.upsert_job(
                company_id,
                record,
                assess_sponsorship(record.description, history),
            )
            seen.append(external_id)

        closed = 0
        if company.manifest_complete:
            closed = repository.finalize_complete_manifest(company_id, source, seen)
        repository.finish_sync_run(
            run_id,
            status="success" if company.manifest_complete else "partial_success",
            complete=company.manifest_complete,
            jobs_seen=len(seen),
        )
        return {
            "company": company.name,
            "status": "success" if company.manifest_complete else "partial_success",
            "jobs": len(seen),
            "closed": closed,
        }
    except Exception as error:  # noqa: BLE001 - source isolation must record any collector failure
        repository.finish_sync_run(
            run_id,
            status="failed",
            complete=False,
            jobs_seen=0,
            error_message=str(error)[:1000],
        )
        return {"company": company.name, "status": "failed", "error": str(error)}


async def sync_companies(
    repository: JobRepository,
    companies: list[CompanySource],
    *,
    concurrency: int = 3,
) -> list[dict[str, object]]:
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def bounded(company: CompanySource) -> dict[str, object]:
        async with semaphore:
            return await sync_company(repository, company)

    return await asyncio.gather(*(bounded(company) for company in companies))
