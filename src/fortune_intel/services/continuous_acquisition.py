"""Bounded continuous orchestration for verified company source acquisition."""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from fortune_intel.observability import log_event
from fortune_intel.services.acquisition_scheduler import (
    DEFAULT_CADENCE_SECONDS,
    _plan_name,
    _validate_limits,
)
from fortune_intel.services.acquisition_planning import (
    ACQUISITION_STAGES,
    create_acquisition_plan,
)
from fortune_intel.services.acquisition_worker import run_acquisition_worker
from fortune_intel.services.licensed_lead_verification import promote_verified_discovery_leads
from fortune_intel.services.licensed_website_verification import promote_verified_website_leads
from fortune_intel.services.fingerprint_candidate_promotion import (
    promote_verified_seed_fingerprints,
)
from fortune_intel.services.registry_career_portal_runner import (
    run_registry_career_portal_verifier,
)
from fortune_intel.storage import JobRepository


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _stage_plan_name(stage: str, now: datetime, cadence_seconds: int) -> str:
    return f"{_plan_name(now, cadence_seconds)}-{stage}"


def _run_stage(
    repository: JobRepository,
    *,
    stage: str,
    cycle_now: datetime,
    cadence_seconds: int,
    batch_size: int,
    lease_seconds: int,
    max_batches: int,
    actor: str,
    lease_owner: str,
    wikimedia_user_agent: str,
    policy_urls: Mapping[str, str],
    policy_approved_at: str,
    sync_interval_minutes: int,
    now: Callable[[], datetime],
    worker: Callable[..., dict[str, object]],
) -> dict[str, object]:
    name = _stage_plan_name(stage, cycle_now, cadence_seconds)
    active = repository.active_acquisition_plan_by_name(name)
    resumed = active is not None
    if active is None:
        try:
            plan_id = create_acquisition_plan(
                repository,
                name=name,
                scope=stage,
                actor=actor,
                policy_urls=policy_urls,
                policy_approved_at=policy_approved_at,
                sync_interval_minutes=sync_interval_minutes,
                created_at=cycle_now,
            )
        except ValueError as error:
            if str(error) != "at least one acquisition task is required":
                raise
            return {
                "stage": stage,
                "status": "no_tasks",
                "plan_id": None,
                "resumed": False,
                "batches": 0,
                "claimed": 0,
                "completed": 0,
                "retry_scheduled": 0,
                "failed": 0,
            }
    else:
        plan_id = str(active["id"])

    totals = {"claimed": 0, "completed": 0, "retry_scheduled": 0, "failed": 0}
    batches = 0
    for _ in range(max_batches):
        kwargs: dict[str, object] = {
            "stage": stage,
            "lease_owner": f"{lease_owner}:{stage}",
            "limit": batch_size,
            "lease_seconds": lease_seconds,
            "now": now(),
        }
        if stage == "website":
            kwargs["wikimedia_user_agent"] = wikimedia_user_agent
        result = worker(repository, plan_id, **kwargs)
        batches += 1
        for key in totals:
            totals[key] += int(result.get(key, 0))
        if not int(result.get("claimed", 0)):
            break
    status = repository.acquisition_plan_status(plan_id, now=now())
    return {
        "stage": stage,
        "status": str(status["status"]),
        "plan_id": plan_id,
        "resumed": resumed,
        "batches": batches,
        **totals,
        "remaining": status["counts"],
        "ready_tasks": status["ready_tasks"],
        "next_attempt_at": status["next_attempt_at"],
    }


def acquisition_operational_metrics(
    repository: JobRepository,
    *,
    policy_kinds: set[str] | None = None,
) -> dict[str, object]:
    """Return queue, dead-letter, coverage, and unsupported-family metrics."""

    configured = {kind.strip().casefold() for kind in (policy_kinds or set())}
    with repository.connect() as connection:
        coverage_rows = connection.execute(
            """SELECT COALESCE(cc.disposition, 'unreviewed') AS disposition, COUNT(*) count
            FROM companies c LEFT JOIN company_coverage cc ON cc.company_id = c.id
            WHERE c.is_synthetic = 0 GROUP BY disposition ORDER BY disposition"""
        ).fetchall()
        checkpoint_rows = connection.execute(
            """SELECT stage, status, COUNT(*) count FROM acquisition_tasks
            GROUP BY stage, status ORDER BY stage, status"""
        ).fetchall()
        dead_rows = connection.execute(
            """SELECT stage, outcome_code, COUNT(*) count FROM acquisition_tasks
            WHERE status = 'failed' GROUP BY stage, outcome_code
            ORDER BY stage, outcome_code"""
        ).fetchall()
        candidate_rows = connection.execute(
            """SELECT kind, COUNT(*) count FROM career_source_candidates
            WHERE status = 'discovered' GROUP BY kind ORDER BY kind"""
        ).fetchall()
        family_rows = connection.execute(
            """SELECT family, COUNT(DISTINCT company_id) companies, COUNT(*) observations
            FROM career_source_fingerprints GROUP BY family ORDER BY family"""
        ).fetchall()
        totals = connection.execute(
            """SELECT COUNT(*) companies,
                SUM(CASE WHEN c.website_url IS NULL OR c.website_url = '' THEN 1 ELSE 0 END)
                    missing_websites,
                SUM(CASE WHEN s.company_id IS NOT NULL THEN 1 ELSE 0 END) supported_companies
            FROM companies c LEFT JOIN (
                SELECT DISTINCT company_id FROM career_sources
                WHERE enabled = 1 AND policy_approved_at IS NOT NULL
                  AND last_success_at IS NOT NULL
            ) s ON s.company_id = c.id WHERE c.is_synthetic = 0"""
        ).fetchone()
    candidate_queue = {
        str(row["kind"]): {
            "candidates": int(row["count"]),
            "operator_policy_configured": str(row["kind"]) in configured,
        }
        for row in candidate_rows
    }
    return {
        "generated_at": _utcnow().isoformat(),
        "companies": {key: int(value or 0) for key, value in dict(totals).items()},
        "coverage_by_disposition": {
            str(row["disposition"]): int(row["count"]) for row in coverage_rows
        },
        "checkpoints": {
            f"{row['stage']}:{row['status']}": int(row["count"]) for row in checkpoint_rows
        },
        "dead_letters": {
            f"{row['stage']}:{row['outcome_code']}": int(row["count"]) for row in dead_rows
        },
        "supported_candidate_queue_by_family": candidate_queue,
        "unsupported_family_queue": {
            str(row["family"]): {
                "companies": int(row["companies"]),
                "observations": int(row["observations"]),
            }
            for row in family_rows
        },
    }


def run_continuous_acquisition_cycle(
    repository: JobRepository,
    *,
    cadence_seconds: int = DEFAULT_CADENCE_SECONDS,
    batch_size: int = 50,
    lease_seconds: int = 300,
    max_batches: int = 200,
    actor: str = "continuous-acquisition-scheduler",
    lease_owner: str = "continuous-acquisition-1",
    wikimedia_user_agent: str,
    policy_urls: Mapping[str, str] | None = None,
    policy_approved_at: str = "",
    sync_interval_minutes: int = 60,
    now: Callable[[], datetime] = _utcnow,
    worker: Callable[..., dict[str, object]] = run_acquisition_worker,
    registry_runner: Callable[..., dict[str, object]] = run_registry_career_portal_verifier,
) -> dict[str, object]:
    """Drain one bounded website→discovery→activation cadence checkpoint."""

    _validate_limits(
        cadence_seconds=cadence_seconds,
        batch_size=batch_size,
        lease_seconds=lease_seconds,
        max_batches=max_batches,
    )
    if not actor.strip() or not lease_owner.strip():
        raise ValueError("actor and lease_owner are required")
    if not wikimedia_user_agent.strip():
        raise ValueError("Wikimedia user-agent is required for official website acquisition")
    policies = dict(policy_urls or {})
    cycle_now = now()
    website_lead_verification = promote_verified_website_leads(
        repository,
        actor=actor,
        limit=batch_size,
    )
    # The durable runner performs the required verified-page -> discovery
    # handoff (and, when policies are configured, complete-manifest approval).
    # Calling the lower-level verifier here would mark a portal verified but
    # strand it before ATS/feed discovery until a separate process happened to
    # revisit it. One bounded batch keeps this scheduler cadence predictable.
    registry_career_portal_verification = registry_runner(
        repository,
        actor=actor,
        batch_size=batch_size,
        concurrency=min(8, batch_size),
        max_batches=1,
        pace_seconds=0,
        policy_urls=policies,
        policy_approved_at=policy_approved_at,
        sync_interval_minutes=sync_interval_minutes,
    )
    fingerprint_promotion = promote_verified_seed_fingerprints(
        repository,
        actor=actor,
        limit=batch_size,
    )
    lead_verification = promote_verified_discovery_leads(
        repository,
        actor=actor,
        policy_urls=policies,
        policy_approved_at=policy_approved_at,
        limit=batch_size,
    ) if policies else {"scanned": 0, "verified": 0, "rejected": 0, "skipped": 0}
    stages = [
        _run_stage(
            repository,
            stage=stage,
            cycle_now=cycle_now,
            cadence_seconds=cadence_seconds,
            batch_size=batch_size,
            lease_seconds=lease_seconds,
            max_batches=max_batches,
            actor=actor.strip(),
            lease_owner=lease_owner.strip(),
            wikimedia_user_agent=wikimedia_user_agent.strip(),
            policy_urls=policies,
            policy_approved_at=policy_approved_at,
            sync_interval_minutes=sync_interval_minutes,
            now=now,
            worker=worker,
        )
        for stage in ACQUISITION_STAGES
    ]
    statuses = Counter(str(stage["status"]) for stage in stages)
    return {
        "cycle_name": _plan_name(cycle_now, cadence_seconds),
        "status": "active" if statuses["active"] else "finished",
        "licensed_website_verification": website_lead_verification,
        "registry_career_portal_verification": registry_career_portal_verification,
        "verified_seed_fingerprint_promotion": fingerprint_promotion,
        "licensed_lead_verification": lead_verification,
        "stages": stages,
        "metrics": acquisition_operational_metrics(repository, policy_kinds=set(policies)),
    }


def continuous_acquisition_scheduler_loop(
    repository: JobRepository,
    *,
    poll_seconds: int = 60,
    max_cycles: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
    **cycle_options: object,
) -> None:
    """Continuously resume ready checkpoints and periodically freeze new work."""

    if not 10 <= poll_seconds <= 3_600:
        raise ValueError("poll_seconds must be between 10 and 3600")
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        result = run_continuous_acquisition_cycle(repository, **cycle_options)
        cycles += 1
        log_event("continuous_acquisition_cycle_finished", **result)
        if max_cycles is not None and cycles >= max_cycles:
            return
        sleep(float(poll_seconds))
