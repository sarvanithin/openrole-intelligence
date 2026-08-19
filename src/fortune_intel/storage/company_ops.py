"""Reviewed company identity consolidation operations."""

from __future__ import annotations

import hashlib


class CompanyOperationsMixin:
    def merge_companies(
        self,
        source_company_id: int,
        target_company_id: int,
        *,
        actor: str,
        reason: str,
    ) -> None:
        """Merge a duplicate into a canonical company without breaking stable job IDs."""
        if source_company_id == target_company_id:
            raise ValueError("source and target companies must differ")
        if not actor.strip() or not reason.strip():
            raise ValueError("actor and reason are required")
        with self.connect() as connection:
            source = connection.execute(
                "SELECT * FROM companies WHERE id = ?", (source_company_id,)
            ).fetchone()
            target = connection.execute(
                "SELECT * FROM companies WHERE id = ?", (target_company_id,)
            ).fetchone()
            if source is None or target is None:
                raise ValueError("source or target company not found")
            if source["sec_cik"] and target["sec_cik"] and source["sec_cik"] != target["sec_cik"]:
                raise ValueError("companies have conflicting SEC CIK identities")
            conflicts = (
                (
                    "jobs",
                    (
                        "source.company_id = ? AND target.company_id = ? "
                        "AND source.source = target.source "
                        "AND source.external_job_id = target.external_job_id"
                    ),
                ),
                (
                    "career_sources",
                    (
                        "source.company_id = ? AND target.company_id = ? "
                        "AND source.base_url = target.base_url"
                    ),
                ),
                (
                    "sponsorship_facts",
                    (
                        "source.company_id = ? AND target.company_id = ? "
                        "AND source.source = target.source "
                        "AND source.fiscal_year = target.fiscal_year"
                    ),
                ),
                (
                    "career_source_candidates",
                    (
                        "source.company_id = ? AND target.company_id = ? "
                        "AND source.candidate_url = target.candidate_url"
                    ),
                ),
                (
                    "career_source_fingerprints",
                    (
                        "source.company_id = ? AND target.company_id = ? "
                        "AND source.family = target.family "
                        "AND source.observed_url = target.observed_url"
                    ),
                ),
            )
            for table, predicate in conflicts:
                collision = connection.execute(
                    f"SELECT 1 FROM {table} source JOIN {table} target ON {predicate} LIMIT 1",
                    (source_company_id, target_company_id),
                ).fetchone()
                if collision is not None:
                    raise ValueError(f"cannot merge conflicting {table} records")

            jobs = connection.execute(
                "SELECT id, source, external_job_id FROM jobs WHERE company_id = ?",
                (source_company_id,),
            ).fetchall()
            for job in jobs:
                stable_key = (
                    f"{target_company_id}:{job['source']}:{str(job['external_job_id']).casefold()}"
                )
                new_id = hashlib.sha256(stable_key.encode()).hexdigest()[:24]
                connection.execute(
                    """INSERT INTO jobs SELECT ?, ?, source, external_job_id,
                        canonical_url, title, location, description, posted_at,
                        source_updated_at, first_seen_at, last_seen_at, closed_at,
                        status, missed_complete_runs, content_hash, cluster_fingerprint,
                        sponsorship_tier, sponsorship_evidence_score, sponsorship_reasons,
                        sponsorship_excerpt, sponsorship_rule_version, metadata,
                        us_eligibility, location_evidence, location_rule_version
                    FROM jobs WHERE id = ?""",
                    (new_id, target_company_id, job["id"]),
                )
                connection.execute(
                    "UPDATE job_versions SET job_id = ? WHERE job_id = ?",
                    (new_id, job["id"]),
                )
                connection.execute("DELETE FROM jobs WHERE id = ?", (job["id"],))

            for table in (
                "career_sources",
                "sponsorship_facts",
                "sync_runs",
                "career_source_candidates",
                "career_source_fingerprints",
                "company_coverage_events",
            ):
                connection.execute(
                    f"UPDATE {table} SET company_id = ? WHERE company_id = ?",
                    (target_company_id, source_company_id),
                )

            source_coverage = connection.execute(
                "SELECT * FROM company_coverage WHERE company_id = ?", (source_company_id,)
            ).fetchone()
            target_coverage = connection.execute(
                "SELECT * FROM company_coverage WHERE company_id = ?", (target_company_id,)
            ).fetchone()
            ranks = {
                "unreviewed": 0,
                "no_source": 1,
                "unsupported": 2,
                "blocked": 2,
                "candidate": 3,
                "approved": 4,
                "stale": 5,
                "supported": 6,
            }
            best = max(
                (row for row in (source_coverage, target_coverage) if row is not None),
                key=lambda row: ranks[str(row["disposition"])],
            )
            connection.execute(
                """UPDATE company_coverage SET disposition = ?, reason = ?,
                    last_discovered_at = ?, last_reviewed_at = ?, stale_after = ?,
                    reviewed_by = ?, updated_at = ? WHERE company_id = ?""",
                (
                    best["disposition"],
                    best["reason"],
                    best["last_discovered_at"],
                    best["last_reviewed_at"],
                    best["stale_after"],
                    best["reviewed_by"],
                    best["updated_at"],
                    target_company_id,
                ),
            )
            connection.execute(
                "DELETE FROM company_coverage WHERE company_id = ?", (source_company_id,)
            )
            connection.execute(
                """UPDATE companies SET
                    career_url = CASE WHEN career_url = '' OR career_url IS NULL
                        THEN ? ELSE career_url END,
                    ats_type = CASE WHEN ats_type = '' OR ats_type IS NULL
                        THEN ? ELSE ats_type END,
                    website_url = CASE WHEN website_url = '' OR website_url IS NULL
                        THEN ? ELSE website_url END,
                    sec_cik = COALESCE(sec_cik, ?), ticker = COALESCE(ticker, ?),
                    updated_at = ? WHERE id = ?""",
                (
                    source["career_url"],
                    source["ats_type"],
                    source["website_url"],
                    source["sec_cik"],
                    source["ticker"],
                    best["updated_at"],
                    target_company_id,
                ),
            )
            connection.execute("DELETE FROM companies WHERE id = ?", (source_company_id,))

        coverage = self.get_company_coverage(target_company_id)
        self.set_company_disposition(
            target_company_id,
            str(coverage["disposition"]),
            reason=f"Reviewed identity merge: {reason}",
            actor=actor,
        )
