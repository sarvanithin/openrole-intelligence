"""Plan and execute durable exact-evidence website and ATS discovery work."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fortune_intel.importers.wikidata_websites import (
    WikidataWebsiteClient,
    normalize_sec_cik,
)
from fortune_intel.services.acquisition_planning import ACQUISITION_STAGES
from fortune_intel.services.discovery_pipeline import discover_company_sources
from fortune_intel.services.source_approval import (
    CompleteEmptyObservationPending,
    approve_source_candidate,
)
from fortune_intel.storage import JobRepository
from fortune_intel.storage.coverage_ops import normalize_public_url

_RECENT_DISCOVERY_WINDOW = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class _StageOutcome:
    code: str
    details: Mapping[str, object]
    failed: bool = False
    retryable: bool = False
    error_summary: str = ""


def _valid_cik(value: object) -> str:
    try:
        return normalize_sec_cik(str(value or ""))
    except ValueError:
        return ""


def _current_company(repository: JobRepository, company_id: int) -> dict[str, Any] | None:
    with repository.connect() as connection:
        row = connection.execute(
            """SELECT c.*, COALESCE(cc.disposition, 'unreviewed') AS coverage_disposition,
                COALESCE(cc.reason, '') AS coverage_reason,
                cc.last_discovered_at,
                cc.updated_at AS coverage_updated_at
            FROM companies c LEFT JOIN company_coverage cc ON cc.company_id = c.id
            WHERE c.id = ?""",
            (company_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def _write_exact_cik_urls(
    repository: JobRepository,
    *,
    company_id: int,
    sec_cik: str,
    website_url: str,
    career_url: str,
) -> tuple[bool, bool, str]:
    """Write only into still-empty URL fields under a locked exact-CIK recheck."""

    timestamp = datetime.now(UTC).replace(microsecond=0).isoformat()
    with repository.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT sec_cik, website_url, career_url FROM companies WHERE id = ?", (company_id,)
        ).fetchone()
        if row is None or _valid_cik(row["sec_cik"]) != sec_cik:
            raise ValueError("company SEC CIK changed before URL persistence")
        existing_website = str(row["website_url"] or "")
        if existing_website:
            return False, False, existing_website
        career_written = bool(career_url and not str(row["career_url"] or ""))
        connection.execute(
            """UPDATE companies SET website_url = ?,
                career_url = CASE WHEN (career_url IS NULL OR career_url = '')
                    THEN ? ELSE career_url END,
                updated_at = ? WHERE id = ?""",
            (website_url, career_url, timestamp, company_id),
        )
    return True, career_written, website_url


def _process_website(
    repository: JobRepository,
    task: Mapping[str, object],
    query_result: Any,
    *,
    actor: str,
) -> _StageOutcome:
    stage = dict(task["stage_snapshot"])
    if stage.get("identity_method") != "exact_sec_cik":
        return _StageOutcome(
            "identity_unavailable",
            {"reason": str(stage.get("identity_reason") or "missing exact SEC CIK")},
            failed=True,
            error_summary=(
                "official website acquisition requires a unique exact SEC CIK or "
                "separately reviewed public provenance"
            ),
        )
    cik = normalize_sec_cik(str(stage.get("sec_cik") or ""))
    company_id = int(task["company_id"])
    company = _current_company(repository, company_id)
    if company is None:
        return _StageOutcome(
            "company_missing", {}, failed=True, error_summary="company was deleted"
        )
    if _valid_cik(company.get("sec_cik")) != cik:
        return _StageOutcome(
            "identity_changed", {}, failed=True, error_summary="company SEC CIK changed after plan"
        )
    with repository.connect() as connection:
        cik_count = connection.execute(
            "SELECT COUNT(*) FROM companies WHERE sec_cik = ?", (cik,)
        ).fetchone()[0]
    if int(cik_count) != 1:
        return _StageOutcome(
            "ambiguous_company_cik", {}, failed=True, error_summary="SEC CIK is no longer unique"
        )
    if str(company.get("website_url") or ""):
        return _StageOutcome("already_present", {"website_url": company["website_url"]})

    rows = [candidate for candidate in query_result.candidates if candidate.sec_cik == cik]
    if not rows:
        return _StageOutcome(
            "no_wikidata_match",
            {"sec_cik": cik, "pages_requested": query_result.pages_requested},
        )
    items = {row.item_url for row in rows}
    if len(items) != 1:
        return _StageOutcome("ambiguous_wikidata_identity", {"items": sorted(items)})
    websites = {row.website_url for row in rows if row.website_url}
    careers = {row.career_url for row in rows if row.career_url}
    if len(websites) != 1:
        code = "no_official_website" if not websites else "ambiguous_official_website"
        return _StageOutcome(code, {"candidate_count": len(websites), "entity": next(iter(items))})
    website = next(iter(websites))
    career = next(iter(careers)) if len(careers) == 1 else ""
    website = normalize_public_url(website, field="website_url")
    career = normalize_public_url(career, field="career_url", optional=True)
    written, career_written, final_website = _write_exact_cik_urls(
        repository,
        company_id=company_id,
        sec_cik=cik,
        website_url=website,
        career_url=career,
    )
    if not written:
        return _StageOutcome("already_present", {"website_url": final_website})
    coverage = repository.get_company_coverage(company_id)
    properties = [f"P856 ({website})"]
    if career_written:
        properties.append(f"P10311 ({career})")
    repository.set_company_disposition(
        company_id,
        str(coverage["disposition"]),
        reason=(
            f"Canonical company URL imported by exact SEC CIK {cik}: "
            f"Wikidata {next(iter(items))} P5531 -> {', '.join(properties)}"
        ),
        actor=actor,
    )
    return _StageOutcome(
        "website_verified",
        {
            "sec_cik": cik,
            "wikidata_entity_url": next(iter(items)),
            "website_url": website,
            "career_url": career if career_written else "",
        },
    )


def _process_discovery(
    repository: JobRepository,
    task: Mapping[str, object],
    *,
    actor: str,
    observed_at: str | datetime | None,
    discovery_runner: Callable[..., list[dict[str, object]]],
) -> _StageOutcome:
    company_id = int(task["company_id"])
    current = _current_company(repository, company_id)
    if current is None:
        return _StageOutcome(
            "company_missing", {}, failed=True, error_summary="company was deleted"
        )
    if current["coverage_disposition"] == "supported":
        return _StageOutcome("already_supported", {})
    last_discovered = str(current.get("last_discovered_at") or "")
    stage = dict(task["stage_snapshot"])
    if (
        int(task["attempts"]) == 1
        and _recent_discovery(last_discovered, observed_at)
        and not _verified_seed_changed_since(stage, last_discovered)
    ):
        disposition = str(current["coverage_disposition"])
        details = {
            "persisted_disposition": disposition,
            "persisted_reason": str(current.get("coverage_reason") or ""),
            "last_discovered_at": last_discovered,
        }
        if disposition == "candidate":
            return _StageOutcome("current_candidate", details)
        if disposition == "unsupported":
            return _StageOutcome("current_unsupported", details)
        if disposition == "blocked":
            reason = str(current.get("coverage_reason") or "")
            if "fetch_failed" in reason or any(
                marker in reason
                for marker in ("robots_denied", "unsafe_redirect", "rejected_start_url")
            ):
                return _StageOutcome(
                    "current_discovery_blocked",
                    details,
                    failed=True,
                    retryable="fetch_failed" in reason,
                    error_summary=reason,
                )
    company = {
        "id": company_id,
        "name": str(task["company_name"]),
        "website_url": str(stage.get("website_url") or ""),
        "career_url": str(stage.get("career_url") or ""),
    }
    if not company["website_url"] and not company["career_url"]:
        return _StageOutcome(
            "verified_seed_missing", {}, failed=True, error_summary="frozen seed is empty"
        )
    results = discovery_runner(repository, [company], actor=actor, concurrency=1)
    if len(results) != 1:
        raise RuntimeError("discovery did not return exactly one company outcome")
    result = results[0]
    disposition = str(result["disposition"])
    details = {"discovery": result}
    if disposition == "candidate":
        return _StageOutcome("candidate_discovered", details)
    if disposition == "unsupported":
        return _StageOutcome("no_supported_ats", details)
    coverage = repository.get_company_coverage(company_id) or {}
    reason = str(coverage.get("reason") or "")
    retryable = "fetch_failed" in reason
    return _StageOutcome(
        "discovery_blocked",
        details,
        failed=True,
        retryable=retryable,
        error_summary=reason or "bounded discovery was blocked",
    )


def _process_activation(
    repository: JobRepository,
    task: Mapping[str, object],
    *,
    actor: str,
    activation_runner: Callable[..., int],
) -> _StageOutcome:
    stage = dict(task["stage_snapshot"])
    candidate_id = int(stage.get("candidate_id") or 0)
    candidate = repository.get_source_candidate(candidate_id)
    if candidate is None:
        return _StageOutcome(
            "candidate_missing", {}, failed=True, error_summary="candidate was deleted"
        )
    if int(candidate["company_id"]) != int(task["company_id"]):
        return _StageOutcome(
            "candidate_identity_changed",
            {},
            failed=True,
            error_summary="candidate company changed after plan",
        )
    if (
        candidate["status"] != "discovered"
        or str(candidate["candidate_url"]) != str(stage.get("candidate_url") or "")
        or str(candidate["kind"]) != str(stage.get("kind") or "")
    ):
        return _StageOutcome("candidate_no_longer_pending", {})
    try:
        source_id = activation_runner(
            repository,
            candidate_id,
            terms_url=str(stage["policy_url"]),
            policy_approved_at=str(stage["policy_approved_at"]),
            actor=actor,
            sync_interval_minutes=int(stage["sync_interval_minutes"]),
        )
    except CompleteEmptyObservationPending as error:
        return _StageOutcome(
            "empty_manifest_confirmation_pending",
            {"observations": error.observations, "required": error.required},
            failed=True,
            retryable=True,
            error_summary=str(error),
        )
    except (ConnectionError, RuntimeError, TimeoutError, ValueError) as error:
        return _StageOutcome(
            "candidate_probe_failed",
            {},
            failed=True,
            retryable=True,
            error_summary=f"{type(error).__name__}: {str(error)[:300]}",
        )
    return _StageOutcome("source_activated", {"source_id": source_id})


def _recent_discovery(value: str, now: str | datetime | None) -> bool:
    if not value:
        return False
    try:
        observed = datetime.fromisoformat(value)
        current = (
            datetime.now(UTC)
            if now is None
            else (now if isinstance(now, datetime) else datetime.fromisoformat(now))
        )
    except ValueError:
        return False
    if observed.tzinfo is None or current.tzinfo is None:
        return False
    age = current.astimezone(UTC) - observed.astimezone(UTC)
    return -timedelta(minutes=5) <= age <= _RECENT_DISCOVERY_WINDOW


def _verified_seed_changed_since(stage: Mapping[str, object], last_discovered: str) -> bool:
    """Force a new crawl when reviewed URL evidence is newer than the last crawl."""

    try:
        previous = datetime.fromisoformat(last_discovered).astimezone(UTC)
    except (ValueError, AttributeError):
        return False
    events = stage.get("verification_events")
    if not isinstance(events, list):
        return False
    for event in events:
        if not isinstance(event, Mapping):
            continue
        try:
            occurred = datetime.fromisoformat(str(event.get("occurred_at") or ""))
        except ValueError:
            continue
        if occurred.tzinfo is not None and occurred.astimezone(UTC) > previous:
            return True
    return False


def run_acquisition_worker(
    repository: JobRepository,
    plan_id: str,
    *,
    stage: str,
    lease_owner: str,
    limit: int = 10,
    lease_seconds: int = 300,
    wikimedia_user_agent: str = "",
    now: str | datetime | None = None,
    wikidata_client_factory: Callable[[str], Any] = lambda user_agent: WikidataWebsiteClient(
        user_agent=user_agent, batch_size=100, page_delay_seconds=0
    ),
    discovery_runner: Callable[..., list[dict[str, object]]] = discover_company_sources,
    activation_runner: Callable[..., int] = approve_source_candidate,
) -> dict[str, object]:
    """Claim and immediately persist one independent outcome per acquisition task."""

    normalized_stage = stage.strip().casefold()
    if normalized_stage not in ACQUISITION_STAGES:
        raise ValueError("stage must be website, discovery, or activation")
    client = None
    if normalized_stage == "website" and not wikimedia_user_agent.strip():
        raise ValueError("Wikimedia user-agent is required for website tasks")
    claimed = repository.claim_acquisition_tasks(
        plan_id,
        lease_owner=lease_owner,
        stage=normalized_stage,
        limit=limit,
        lease_seconds=lease_seconds,
        now=now,
    )
    website_query_result = None
    website_query_error: Exception | None = None
    if normalized_stage == "website" and claimed:
        ciks = set()
        for task in claimed:
            try:
                ciks.add(normalize_sec_cik(str(dict(task["stage_snapshot"]).get("sec_cik") or "")))
            except ValueError:
                continue
        if ciks:
            try:
                client = wikidata_client_factory(wikimedia_user_agent.strip())
                website_query_result = client.query(sorted(ciks))
            except Exception as error:  # noqa: BLE001 - third-party client failures are persisted per task.
                website_query_error = error
    summary: dict[str, object] = {
        "plan_id": plan_id,
        "stage": normalized_stage,
        "claimed": len(claimed),
        "completed": 0,
        "retry_scheduled": 0,
        "failed": 0,
        "lease_lost": 0,
        "outcomes": [],
    }
    outcomes: list[dict[str, object]] = summary["outcomes"]  # type: ignore[assignment]
    actor = f"acquisition-worker:{lease_owner.strip()}"
    for task in claimed:
        try:
            if normalized_stage == "website":
                stage_snapshot = dict(task["stage_snapshot"])
                if stage_snapshot.get("identity_method") == "exact_sec_cik":
                    normalize_sec_cik(str(stage_snapshot.get("sec_cik") or ""))
                if website_query_error is not None:
                    raise website_query_error
                outcome = _process_website(repository, task, website_query_result, actor=actor)
            elif normalized_stage == "discovery":
                outcome = _process_discovery(
                    repository,
                    task,
                    actor=actor,
                    observed_at=now,
                    discovery_runner=discovery_runner,
                )
            else:
                outcome = _process_activation(
                    repository,
                    task,
                    actor=actor,
                    activation_runner=activation_runner,
                )
        except Exception as error:  # noqa: BLE001 - each independent task must fail closed, not abort its batch.
            retryable = isinstance(
                error,
                (ConnectionError, RuntimeError, TimeoutError, sqlite3.OperationalError),
            )
            outcome = _StageOutcome(
                "worker_exception",
                {},
                failed=True,
                retryable=retryable,
                error_summary=f"{type(error).__name__}: {str(error)[:300]}",
            )
        try:
            if outcome.failed:
                updated = repository.fail_acquisition_task(
                    str(task["id"]),
                    lease_owner=lease_owner,
                    outcome_code=outcome.code,
                    retryable=outcome.retryable,
                    error_summary=outcome.error_summary,
                    now=now,
                )
                key = "retry_scheduled" if updated["status"] == "pending" else "failed"
            else:
                repository.complete_acquisition_task(
                    str(task["id"]),
                    lease_owner=lease_owner,
                    outcome_code=outcome.code,
                    outcome=outcome.details,
                    now=now,
                )
                key = "completed"
            summary[key] = int(summary[key]) + 1
            outcomes.append({"task_id": task["id"], "outcome_code": outcome.code, "status": key})
        except ValueError as error:
            summary["lease_lost"] = int(summary["lease_lost"]) + 1
            outcomes.append(
                {"task_id": task["id"], "outcome_code": "lease_lost", "error": str(error)}
            )
    return summary
