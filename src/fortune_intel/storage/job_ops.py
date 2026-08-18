"""Job persistence operations shared by sync and transactional approval."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime

from fortune_intel.domain import JobRecord, SponsorshipAssessment, canonicalize_url
from fortune_intel.storage.identity import normalize_company_name
from fortune_intel.storage.job_geography import assess_job_geography


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class JobOperationsMixin:
    def upsert_job(
        self,
        company_id: int,
        job: JobRecord,
        assessment: SponsorshipAssessment,
    ) -> str:
        with self.connect() as connection:
            return self._upsert_job_with_connection(connection, company_id, job, assessment)

    def _upsert_job_with_connection(
        self,
        connection: sqlite3.Connection,
        company_id: int,
        job: JobRecord,
        assessment: SponsorshipAssessment,
        *,
        observed_at: str | None = None,
    ) -> str:
        now = observed_at or _now()
        canonical_url = canonicalize_url(job.url)
        source = job.source.casefold().strip()
        stable_key = f"{company_id}:{source}:{job.external_job_id.casefold()}"
        job_id = hashlib.sha256(stable_key.encode()).hexdigest()[:24]
        content = json.dumps(
            {"title": job.title, "location": job.location, "description": job.description},
            sort_keys=True,
        )
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        version_snapshot = json.dumps(
            {
                "title": job.title,
                "location": job.location,
                "description_hash": hashlib.sha256(job.description.encode()).hexdigest(),
            },
            sort_keys=True,
        )
        fingerprint_content = "|".join(
            (
                normalize_company_name(job.company_name),
                job.title.casefold().strip(),
                job.location.casefold().strip(),
            )
        )
        cluster_fingerprint = hashlib.sha256(fingerprint_content.encode()).hexdigest()[:24]
        metadata = dict(job.metadata)
        geography = assess_job_geography(job.location, metadata)
        connection.execute(
            """
            INSERT INTO jobs (
                id, company_id, source, external_job_id, canonical_url, title,
                location, description, posted_at, source_updated_at,
                first_seen_at, last_seen_at, content_hash, cluster_fingerprint,
                sponsorship_tier, sponsorship_evidence_score, sponsorship_reasons,
                sponsorship_excerpt, sponsorship_rule_version, metadata,
                us_eligibility, location_evidence, location_rule_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_id, source, external_job_id) DO UPDATE SET
                company_id = excluded.company_id,
                canonical_url = excluded.canonical_url,
                title = excluded.title,
                location = excluded.location,
                description = excluded.description,
                posted_at = COALESCE(excluded.posted_at, jobs.posted_at),
                source_updated_at = COALESCE(excluded.source_updated_at, jobs.source_updated_at),
                last_seen_at = excluded.last_seen_at,
                closed_at = NULL,
                status = 'active',
                missed_complete_runs = 0,
                content_hash = excluded.content_hash,
                cluster_fingerprint = excluded.cluster_fingerprint,
                sponsorship_tier = excluded.sponsorship_tier,
                sponsorship_evidence_score = excluded.sponsorship_evidence_score,
                sponsorship_reasons = excluded.sponsorship_reasons,
                sponsorship_excerpt = excluded.sponsorship_excerpt,
                sponsorship_rule_version = excluded.sponsorship_rule_version,
                metadata = excluded.metadata,
                us_eligibility = excluded.us_eligibility,
                location_evidence = excluded.location_evidence,
                location_rule_version = excluded.location_rule_version
            """,
            (
                job_id,
                company_id,
                source,
                job.external_job_id,
                canonical_url,
                job.title.strip(),
                job.location.strip(),
                job.description.strip(),
                job.source_opened_at,
                job.source_updated_at,
                now,
                now,
                content_hash,
                cluster_fingerprint,
                assessment.tier.value,
                assessment.evidence_score,
                json.dumps(assessment.reasons),
                assessment.policy_excerpt,
                assessment.rule_version,
                json.dumps(metadata, sort_keys=True),
                geography.us_eligibility,
                geography.evidence_json,
                geography.rule_version,
            ),
        )
        connection.execute(
            """INSERT OR IGNORE INTO job_versions
            (job_id, content_hash, snapshot, observed_at) VALUES (?, ?, ?, ?)""",
            (job_id, content_hash, version_snapshot, now),
        )
        return job_id
