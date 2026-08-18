"""Persistent manifest-observation and job-closure safeguards."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime


class ManifestOperationsMixin:
    def record_candidate_manifest_observation(
        self,
        candidate_id: int,
        *,
        complete: bool,
        jobs_seen: int,
    ) -> int:
        """Record a candidate probe and return its complete-empty streak."""

        return self._record_manifest_observation(
            "career_source_candidates",
            candidate_id,
            complete=complete,
            jobs_seen=jobs_seen,
        )

    def record_source_manifest_observation(
        self,
        source_id: int,
        *,
        complete: bool,
        jobs_seen: int,
    ) -> int:
        """Record a registered-source probe and return its complete-empty streak."""

        return self._record_manifest_observation(
            "career_sources",
            source_id,
            complete=complete,
            jobs_seen=jobs_seen,
        )

    def _record_manifest_observation(
        self,
        table: str,
        record_id: int,
        *,
        complete: bool,
        jobs_seen: int,
    ) -> int:
        if jobs_seen < 0:
            raise ValueError("jobs_seen cannot be negative")
        if table not in {"career_source_candidates", "career_sources"}:
            raise ValueError("unsupported manifest observation table")
        with self.connect() as connection:
            row = connection.execute(
                f"SELECT consecutive_complete_empty_observations FROM {table} WHERE id = ?",
                (record_id,),
            ).fetchone()
            if row is None:
                raise ValueError("manifest observation target not found")
            count = int(row[0])
            if complete:
                count = count + 1 if jobs_seen == 0 else 0
                connection.execute(
                    f"""UPDATE {table}
                    SET consecutive_complete_empty_observations = ?, updated_at = ?
                    WHERE id = ?""",
                    (count, self._manifest_utc_now(), record_id),
                )
            return count

    def finalize_complete_manifest(
        self,
        company_id: int,
        source: str,
        seen_external_ids: Sequence[str],
        *,
        verified_empty: bool = False,
    ) -> int:
        """Apply two-manifest closure grace after a trusted complete manifest."""

        if not seen_external_ids and not verified_empty:
            return 0
        with self.connect() as connection:
            connection.execute(
                "CREATE TEMP TABLE IF NOT EXISTS seen_manifest_ids "
                "(external_job_id TEXT PRIMARY KEY)"
            )
            connection.execute("DELETE FROM seen_manifest_ids")
            connection.executemany(
                "INSERT OR IGNORE INTO seen_manifest_ids (external_job_id) VALUES (?)",
                ((external_id,) for external_id in seen_external_ids),
            )
            connection.execute(
                """
                UPDATE jobs SET missed_complete_runs = missed_complete_runs + 1
                WHERE company_id = ? AND source = ? AND status = 'active'
                  AND NOT EXISTS (
                    SELECT 1 FROM seen_manifest_ids s
                    WHERE s.external_job_id = jobs.external_job_id
                  )
                """,
                (company_id, source),
            )
            cursor = connection.execute(
                """
                UPDATE jobs SET status = 'closed', closed_at = ?
                WHERE company_id = ? AND source = ? AND status = 'active'
                  AND missed_complete_runs >= 2
                """,
                (self._manifest_utc_now(), company_id, source),
            )
            return int(cursor.rowcount)

    @staticmethod
    def _manifest_utc_now() -> str:
        return datetime.now(UTC).replace(microsecond=0).isoformat()
