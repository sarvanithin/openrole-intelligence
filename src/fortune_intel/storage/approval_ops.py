"""Atomic persistence of an approved connector's initial manifest."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from fortune_intel.domain import JobRecord, SponsorshipAssessment, canonicalize_url


class ApprovalOperationsMixin:
    def persist_approved_source_manifest(
        self,
        candidate_id: int,
        *,
        kind: str,
        board_token: str,
        base_url: str,
        sync_interval_minutes: int,
        terms_url: str,
        policy_approved_at: str,
        actor: str,
        review_notes: str,
        jobs: Sequence[tuple[JobRecord, SponsorshipAssessment]],
    ) -> int:
        """Commit registration, ingestion, policy audit, and health as one unit."""

        if not 15 <= sync_interval_minutes <= 10080:
            raise ValueError("sync interval must be between 15 minutes and 7 days")
        if not actor.strip():
            raise ValueError("actor is required")
        observed = datetime.now(UTC).replace(microsecond=0)
        observed_at = observed.isoformat()
        next_sync_at = (observed + timedelta(minutes=sync_interval_minutes)).isoformat()
        source_kind = kind.casefold().strip()
        source_identity = f"{source_kind}:{board_token}"
        canonical_base_url = canonicalize_url(base_url)
        with self.connect() as connection:
            candidate = connection.execute(
                """SELECT candidate.company_id, candidate.status, company.created_at
                FROM career_source_candidates candidate
                JOIN companies company ON company.id = candidate.company_id
                WHERE candidate.id = ?""",
                (candidate_id,),
            ).fetchone()
            if candidate is None:
                raise ValueError("candidate not found")
            if candidate["status"] in {"rejected", "superseded"}:
                raise ValueError("rejected or superseded candidate cannot be approved")
            company_id = int(candidate["company_id"])
            connection.execute(
                """INSERT INTO career_sources (
                    company_id, kind, board_token, base_url, enabled,
                    sync_interval_minutes, last_started_at, last_success_at,
                    next_sync_at, consecutive_failures,
                    consecutive_complete_empty_observations, last_error,
                    terms_url, policy_approved_at, owner_contact, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, 0, 0, '', ?, ?, ?, ?, ?)
                ON CONFLICT(company_id, base_url) DO UPDATE SET
                    kind = excluded.kind,
                    board_token = excluded.board_token,
                    enabled = 1,
                    sync_interval_minutes = excluded.sync_interval_minutes,
                    last_started_at = excluded.last_started_at,
                    last_success_at = excluded.last_success_at,
                    next_sync_at = excluded.next_sync_at,
                    consecutive_failures = 0,
                    consecutive_complete_empty_observations = 0,
                    last_error = '',
                    terms_url = excluded.terms_url,
                    policy_approved_at = excluded.policy_approved_at,
                    owner_contact = excluded.owner_contact,
                    updated_at = excluded.updated_at""",
                (
                    company_id,
                    source_kind,
                    board_token,
                    canonical_base_url,
                    sync_interval_minutes,
                    observed_at,
                    observed_at,
                    next_sync_at,
                    terms_url,
                    policy_approved_at,
                    actor.strip(),
                    observed_at,
                    observed_at,
                ),
            )
            source = connection.execute(
                "SELECT id FROM career_sources WHERE company_id = ? AND base_url = ?",
                (company_id, canonical_base_url),
            ).fetchone()
            assert source is not None
            source_id = int(source["id"])
            for job, assessment in jobs:
                self._upsert_job_with_connection(
                    connection,
                    company_id,
                    job,
                    assessment,
                    observed_at=observed_at,
                )
            connection.execute(
                """INSERT INTO sync_runs (
                    source, company_id, status, complete, jobs_seen,
                    started_at, finished_at
                ) VALUES (?, ?, 'success', 1, ?, ?, ?)""",
                (source_identity, company_id, len(jobs), observed_at, observed_at),
            )
            connection.execute(
                """UPDATE career_source_candidates SET
                    status = 'approved', reviewed_at = ?, reviewed_by = ?,
                    review_notes = ?, terms_url = ?, terms_status = 'permitted',
                    terms_reviewed_at = ?, updated_at = ? WHERE id = ?""",
                (
                    policy_approved_at,
                    actor.strip(),
                    review_notes.strip(),
                    terms_url,
                    policy_approved_at,
                    observed_at,
                    candidate_id,
                ),
            )
            coverage = connection.execute(
                "SELECT disposition FROM company_coverage WHERE company_id = ?",
                (company_id,),
            ).fetchone()
            previous = str(coverage["disposition"]) if coverage else None
            reason = "Approved connector probe manifest ingested successfully"
            connection.execute(
                """INSERT INTO company_coverage (
                    company_id, disposition, reason, last_reviewed_at,
                    reviewed_by, created_at, updated_at
                ) VALUES (?, 'supported', ?, ?, ?, ?, ?)
                ON CONFLICT(company_id) DO UPDATE SET
                    disposition = 'supported', reason = excluded.reason,
                    last_reviewed_at = excluded.last_reviewed_at,
                    reviewed_by = excluded.reviewed_by,
                    updated_at = excluded.updated_at""",
                (
                    company_id,
                    reason,
                    observed_at,
                    "source-approval-ingestion",
                    str(candidate["created_at"]),
                    observed_at,
                ),
            )
            connection.execute(
                """INSERT INTO company_coverage_events (
                    company_id, candidate_id, from_disposition, to_disposition,
                    reason, actor, occurred_at
                ) VALUES (?, ?, ?, 'supported', ?, ?, ?)""",
                (
                    company_id,
                    candidate_id,
                    previous,
                    reason,
                    actor.strip(),
                    observed_at,
                ),
            )
            return source_id
