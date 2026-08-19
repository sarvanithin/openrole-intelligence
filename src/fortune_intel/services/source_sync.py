"""Persist complete deterministic ATS manifests with lifecycle safeguards."""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

from fortune_intel.connectors import build_connector
from fortune_intel.domain import JobRecord
from fortune_intel.observability import log_event
from fortune_intel.services.sponsorship import assess_sponsorship
from fortune_intel.storage import JobRepository
from fortune_intel.storage.job_geography import assess_job_geography

MAX_SOURCE_SYNC_CONCURRENCY = 16


async def sync_registered_source(
    repository: JobRepository,
    source: dict[str, Any],
) -> dict[str, object]:
    source_id = int(source["id"])
    company_id = int(source["company_id"])
    source_identity = f"{source['kind']}:{source['board_token']}"
    repository.mark_source_started(source_id)
    run_id = repository.start_sync_run(source_identity, company_id)
    log_event(
        "source_sync_started",
        source_id=source_id,
        company=source["company_name"],
        kind=source["kind"],
    )
    try:
        connector = build_connector(source["kind"], source["board_token"])
        result = await asyncio.to_thread(connector.fetch)
        if not result.jobs:
            if result.complete:
                empty_observations = repository.record_source_manifest_observation(
                    source_id, complete=True, jobs_seen=0
                )
                if empty_observations >= 2:
                    closed = repository.finalize_complete_manifest(
                        company_id, source_identity, (), verified_empty=True
                    )
                    repository.finish_sync_run(run_id, status="success", complete=True, jobs_seen=0)
                    repository.mark_source_finished(source_id, success=True)
                    repository.reconcile_source_coverage(company_id)
                    log_event(
                        "source_sync_verified_empty",
                        source_id=source_id,
                        observations=empty_observations,
                        closed=closed,
                    )
                    return {
                        "source_id": source_id,
                        "status": "success",
                        "jobs": 0,
                        "closed": closed,
                        "pages": result.pages_fetched,
                        "complete_empty_observations": empty_observations,
                    }
                status = "anomalous_empty"
                message = "complete source returned zero jobs; awaiting independent confirmation"
            else:
                status = "failed"
                message = _errors(result.errors)
            repository.finish_sync_run(
                run_id,
                status=status,
                complete=False,
                jobs_seen=0,
                error_message=message,
            )
            repository.mark_source_finished(source_id, success=False, error=message)
            repository.reconcile_source_coverage(company_id)
            log_event("source_sync_empty", source_id=source_id, status=status)
            return {"source_id": source_id, "status": status, "jobs": 0}

        history = repository.get_employer_history(company_id)
        seen = []
        geography_counts: Counter[str] = Counter()
        for item in result.jobs:
            metadata = dict(item.metadata)
            geography_counts[assess_job_geography(item.location, metadata).us_eligibility] += 1
            record = JobRecord(
                company_name=source["company_name"],
                title=item.title,
                url=item.url,
                source=source_identity,
                external_job_id=item.external_job_id,
                location=item.location,
                description=item.description,
                source_opened_at=item.source_opened_at,
                source_updated_at=item.source_updated_at,
                metadata=metadata,
            )
            repository.upsert_job(
                company_id,
                record,
                assess_sponsorship(record.description, history),
            )
            seen.append(item.external_job_id)

        closed = 0
        if result.complete:
            repository.record_source_manifest_observation(
                source_id, complete=True, jobs_seen=len(seen)
            )
            closed = repository.finalize_complete_manifest(company_id, source_identity, seen)
        status = "success" if result.complete else "partial_success"
        error_message = "" if result.complete else _errors(result.errors)
        repository.finish_sync_run(
            run_id,
            status=status,
            complete=result.complete,
            jobs_seen=len(seen),
            error_message=error_message or None,
        )
        repository.mark_source_finished(source_id, success=result.complete, error=error_message)
        repository.reconcile_source_coverage(company_id)
        log_event(
            "source_sync_finished",
            source_id=source_id,
            status=status,
            jobs=len(seen),
            closed=closed,
            pages=result.pages_fetched,
            us_eligible=geography_counts["eligible"],
            non_us=geography_counts["ineligible"],
            location_unknown=geography_counts["unknown"],
        )
        return {
            "source_id": source_id,
            "status": status,
            "jobs": len(seen),
            "closed": closed,
            "pages": result.pages_fetched,
        }
    except Exception as error:  # noqa: BLE001 - source isolation must record any connector failure
        message = f"{type(error).__name__}: {error}"[:1000]
        repository.finish_sync_run(
            run_id, status="failed", complete=False, jobs_seen=0, error_message=message
        )
        repository.mark_source_finished(source_id, success=False, error=message)
        repository.reconcile_source_coverage(company_id)
        log_event("source_sync_failed", source_id=source_id, error_type=type(error).__name__)
        return {"source_id": source_id, "status": "failed", "error": message}


async def sync_due_sources(
    repository: JobRepository,
    *,
    limit: int = 25,
    concurrency: int = 4,
) -> list[dict[str, object]]:
    sources = repository.due_career_sources(limit=limit)
    # Fetches are mostly network-bound and source-specific. Keep a finite global
    # bound so a large due queue cannot overwhelm the SQLite writer or an ATS,
    # while allowing the hourly scheduler to complete a several-hundred-source
    # cycle within its configured freshness window.
    semaphore = asyncio.Semaphore(max(1, min(concurrency, MAX_SOURCE_SYNC_CONCURRENCY)))

    async def bounded(source: dict[str, Any]) -> dict[str, object]:
        async with semaphore:
            return await sync_registered_source(repository, source)

    return await asyncio.gather(*(bounded(source) for source in sources))


def _errors(errors: tuple[object, ...]) -> str:
    return "; ".join(str(getattr(error, "message", error)) for error in errors)[:1000]
