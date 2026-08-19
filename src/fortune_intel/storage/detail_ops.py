"""Public detail and source-status queries."""

from __future__ import annotations

import json
from typing import Any

from fortune_intel.storage.job_geography import validate_job_geography
from fortune_intel.storage.schema import SCHEMA_VERSION, validate_schema


class DetailOperationsMixin:
    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT j.*, c.name AS company_name, c.slug AS company_slug,
                    c.collection_name, c.collection_year, c.collection_rank,
                    c.is_synthetic
                FROM jobs j JOIN companies c ON c.id = j.company_id WHERE j.id = ?
                """,
                (job_id,),
            ).fetchone()
            if row is None:
                return None
            facts = connection.execute(
                """
                SELECT source, fiscal_year, initial_approvals, initial_denials,
                    lca_worker_positions, entity_match_confidence, source_url,
                    source_document, source_checksum, match_method, imported_at
                FROM sponsorship_facts WHERE company_id = ?
                ORDER BY fiscal_year DESC, source
                """,
                (row["company_id"],),
            ).fetchall()
            versions = connection.execute(
                "SELECT observed_at, content_hash FROM job_versions WHERE job_id = ? ORDER BY observed_at DESC",
                (job_id,),
            ).fetchall()
        result = dict(row)
        result["sponsorship_reasons"] = json.loads(result["sponsorship_reasons"])
        result["metadata"] = json.loads(result["metadata"])
        result["employer_evidence"] = [dict(fact) for fact in facts]
        result["versions"] = [dict(version) for version in versions]
        return result

    def source_status(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT s.id, s.kind, s.base_url, s.enabled, s.sync_interval_minutes,
                    s.last_started_at, s.last_success_at, s.next_sync_at,
                    s.consecutive_failures,
                    s.consecutive_complete_empty_observations,
                    s.last_error, c.name AS company_name,
                    c.slug AS company_slug
                FROM career_sources s JOIN companies c ON c.id = s.company_id
                ORDER BY s.consecutive_failures DESC, c.name LIMIT ?
                """,
                (min(max(limit, 1), 500),),
            ).fetchall()
        return [dict(row) for row in rows]

    def readiness(self, *, production: bool = False) -> dict[str, object]:
        errors: list[str] = []
        with self.connect() as connection:
            errors.extend(validate_schema(connection))
            errors.extend(validate_job_geography(connection))
            integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
            if integrity != "ok":
                errors.append(f"database integrity: {integrity}")
            synthetic = int(
                connection.execute(
                    "SELECT COUNT(*) FROM companies WHERE is_synthetic = 1"
                ).fetchone()[0]
            )
            if production and synthetic:
                errors.append("production database contains synthetic records")
        return {
            "ready": not errors,
            "schema_version": SCHEMA_VERSION,
            "errors": errors,
        }
