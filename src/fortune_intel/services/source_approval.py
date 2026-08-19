"""Probe and approve an operator-reviewed ATS discovery candidate."""

from __future__ import annotations

from datetime import datetime

from fortune_intel.connectors import build_connector
from fortune_intel.discovery import classify_ats_url, classify_official_structured_url
from fortune_intel.domain import JobRecord
from fortune_intel.services.source_provenance import (
    candidate_has_primary_provenance,
    verified_company_seed_evidence,
)
from fortune_intel.services.sponsorship import assess_sponsorship
from fortune_intel.storage import JobRepository
from fortune_intel.storage.coverage_ops import normalize_public_url


class CompleteEmptyObservationPending(ValueError):
    """A complete zero-opening board still needs an independent confirmation."""

    def __init__(self, observations: int, required: int = 2):
        self.observations = observations
        self.required = required
        super().__init__(
            f"complete-empty observation {observations} of {required} recorded; "
            "run approval again after an independent probe"
        )


def approve_source_candidate(
    repository: JobRepository,
    candidate_id: int,
    *,
    terms_url: str,
    policy_approved_at: str,
    actor: str,
    sync_interval_minutes: int = 60,
) -> int:
    if not actor.strip():
        raise ValueError("actor is required")
    policy_url = normalize_public_url(terms_url, field="terms_url")
    try:
        approved_at = datetime.fromisoformat(policy_approved_at)
    except ValueError as error:
        raise ValueError("policy_approved_at must be an ISO-8601 timestamp") from error
    if approved_at.tzinfo is None:
        raise ValueError("policy_approved_at must include a timezone")
    candidate = repository.get_source_candidate(candidate_id)
    if candidate is None:
        raise ValueError("candidate not found")
    if candidate["status"] in {"rejected", "superseded"}:
        raise ValueError("rejected or superseded candidate cannot be approved")
    classified = (
        classify_official_structured_url(
            candidate["candidate_url"], origin="stored official structured candidate"
        )
        if candidate["kind"] == "official_structured"
        else classify_ats_url(candidate["candidate_url"])
    )
    if classified is None or classified.connector_kind != candidate["kind"]:
        raise ValueError("candidate URL no longer matches its supported connector")
    if classified.connector_kind in {
        "icims_public",
        "official_structured",
        "ukg_recruiting_public",
    }:
        company = next(
            (
                item
                for item in repository.list_companies(include_synthetic=False)
                if int(item["id"]) == int(candidate["company_id"])
            ),
            None,
        )
        if company is None:
            raise ValueError("candidate company not found")
        website, career, _ = verified_company_seed_evidence(repository, company)
        if not candidate_has_primary_provenance(
            candidate, {url for url in (website, career) if url}
        ):
            raise ValueError(
                f"{classified.connector_kind} activation requires official company provenance from a verified "
                "company seed or primary-source review"
            )
    if classified.connector_kind in {"icims_public", "official_structured"} and (
        candidate["robots_status"] != "allowed"
    ):
        raise ValueError(
            f"{classified.connector_kind} activation requires an explicit allowed robots review"
        )
    result = build_connector(classified.connector_kind, classified.board_token).fetch()
    if not result.complete:
        details = "; ".join(error.message for error in result.errors)[:500]
        raise ValueError(
            "candidate probe did not return a complete manifest "
            f"(complete={result.complete}, jobs={len(result.jobs)}, errors={details or 'none'})"
        )
    empty_observations = repository.record_candidate_manifest_observation(
        candidate_id,
        complete=True,
        jobs_seen=len(result.jobs),
    )
    if not result.jobs and empty_observations < 2:
        raise CompleteEmptyObservationPending(empty_observations)
    source_identity = f"{classified.connector_kind}:{classified.board_token}"
    history = repository.get_employer_history(int(candidate["company_id"]))
    jobs = tuple(
        (
            JobRecord(
                company_name=str(candidate.get("company_name") or ""),
                title=item.title,
                url=item.url,
                source=source_identity,
                external_job_id=item.external_job_id,
                location=item.location,
                description=item.description,
                source_opened_at=item.source_opened_at,
                source_updated_at=item.source_updated_at,
                metadata=dict(item.metadata),
            ),
            assess_sponsorship(item.description, history),
        )
        for item in result.jobs
    )
    notes = (
        "Two independent complete-empty probes verified a legitimate zero-opening board; "
        "complete manifest ingested"
        if not result.jobs
        else f"Connector probe returned and ingested {len(result.jobs)} jobs in a complete manifest"
    )
    return repository.persist_approved_source_manifest(
        candidate_id,
        kind=classified.connector_kind,
        board_token=classified.board_token,
        base_url=classified.normalized_base_url,
        sync_interval_minutes=sync_interval_minutes,
        terms_url=policy_url,
        policy_approved_at=approved_at.isoformat(),
        actor=actor,
        review_notes=notes,
        jobs=jobs,
    )
