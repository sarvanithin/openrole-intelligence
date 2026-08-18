"""Create auditable recovery plans for verified transient acquisition failures."""

from __future__ import annotations

from collections.abc import Mapping

from fortune_intel.importers.wikidata_websites import normalize_sec_cik
from fortune_intel.services.acquisition_planning import ACQUISITION_STAGES
from fortune_intel.storage import AcquisitionTaskSeed, JobRepository
from fortune_intel.storage.coverage_ops import normalize_public_url

_TRANSIENT_FAILURE_CODES = frozenset({"http_503", "lease_expired", "network_timeout"})
_TRANSIENT_EXCEPTION_PREFIXES = frozenset(
    {"ConnectionError", "OperationalError", "RuntimeError", "TimeoutError"}
)


def _valid_cik(value: object) -> str:
    try:
        return normalize_sec_cik(str(value or ""))
    except ValueError:
        return ""


def _has_verified_snapshot(task: Mapping[str, object]) -> bool:
    """Revalidate that a failed task contains no inferred acquisition target."""

    stage = str(task.get("stage") or "")
    stage_snapshot = dict(task.get("stage_snapshot") or {})
    company_snapshot = dict(task.get("company_snapshot") or {})
    if stage == "website":
        stage_cik = _valid_cik(stage_snapshot.get("sec_cik"))
        company_cik = _valid_cik(company_snapshot.get("sec_cik"))
        return bool(
            stage_snapshot.get("identity_method") == "exact_sec_cik"
            and stage_cik
            and stage_cik == company_cik
        )
    if stage != "discovery":
        return False
    evidence = stage_snapshot.get("verification_events")
    if not isinstance(evidence, list) or not evidence:
        return False
    urls = [
        str(stage_snapshot.get("website_url") or ""),
        str(stage_snapshot.get("career_url") or ""),
    ]
    if not any(urls):
        return False
    try:
        for url in urls:
            if url:
                normalize_public_url(url, field="verified recovery URL")
    except ValueError:
        return False
    return True


def _is_verified_transient_failure(task: Mapping[str, object]) -> bool:
    if task.get("status") != "failed" or not _has_verified_snapshot(task):
        return False
    code = str(task.get("outcome_code") or "").casefold()
    error_summary = str(task.get("error_summary") or "")
    if code in _TRANSIENT_FAILURE_CODES:
        return True
    if code in {"current_discovery_blocked", "discovery_blocked"}:
        return "fetch_failed" in error_summary.casefold()
    if code == "worker_exception":
        prefix = error_summary.partition(":")[0].strip()
        return prefix in _TRANSIENT_EXCEPTION_PREFIXES
    return False


def create_acquisition_recovery_plan(
    repository: JobRepository,
    source_plan_id: str,
    *,
    name: str,
    actor: str,
    stage: str | None = None,
) -> str:
    """Copy only verified, exhausted transient failures into a new plan.

    The failed source tasks remain immutable audit history. Recovery resets the
    attempt budget only in newly created tasks and never changes frozen URLs.
    """

    normalized_stage = stage.strip().casefold() if stage is not None else None
    if normalized_stage is not None and normalized_stage not in ACQUISITION_STAGES:
        raise ValueError("stage must be website or discovery")
    repository.acquisition_plan_status(source_plan_id)
    failed = repository.list_acquisition_tasks(source_plan_id, status="failed")
    seeds = [
        AcquisitionTaskSeed(
            company_id=int(task["company_id"]),
            company_name=str(task["company_name"]),
            stage=str(task["stage"]),
            company_snapshot=dict(task["company_snapshot"]),
            stage_snapshot=dict(task["stage_snapshot"]),
            max_attempts=int(task["max_attempts"]),
        )
        for task in failed
        if (normalized_stage is None or task["stage"] == normalized_stage)
        and _is_verified_transient_failure(task)
    ]
    if not seeds:
        raise ValueError("no verified transient failed acquisition tasks are eligible")
    return repository.create_acquisition_plan(name, seeds, actor=actor)
