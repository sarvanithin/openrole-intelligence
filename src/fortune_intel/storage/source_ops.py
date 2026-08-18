"""Career-source scheduling operations mixed into the SQLite repository."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fortune_intel.domain import canonicalize_url


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class SourceOperationsMixin:
    def upsert_career_source(
        self,
        company_id: int,
        *,
        kind: str,
        board_token: str,
        base_url: str,
        sync_interval_minutes: int = 60,
        enabled: bool = True,
        terms_url: str = "",
        policy_approved_at: str | None = None,
        owner_contact: str = "",
    ) -> int:
        if not 15 <= sync_interval_minutes <= 10080:
            raise ValueError("sync interval must be between 15 minutes and 7 days")
        now = _now()
        canonical_url = canonicalize_url(base_url)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO career_sources (
                    company_id, kind, board_token, base_url, enabled,
                    sync_interval_minutes, next_sync_at, terms_url,
                    policy_approved_at, owner_contact, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(company_id, base_url) DO UPDATE SET
                    kind = excluded.kind, board_token = excluded.board_token,
                    enabled = excluded.enabled,
                    sync_interval_minutes = excluded.sync_interval_minutes,
                    next_sync_at = CASE
                        WHEN career_sources.sync_interval_minutes <> excluded.sync_interval_minutes
                            OR (career_sources.enabled = 0 AND excluded.enabled = 1)
                        THEN excluded.next_sync_at
                        ELSE career_sources.next_sync_at
                    END,
                    terms_url = excluded.terms_url,
                    policy_approved_at = excluded.policy_approved_at,
                    owner_contact = excluded.owner_contact,
                    updated_at = excluded.updated_at
                """,
                (
                    company_id,
                    kind.casefold(),
                    board_token,
                    canonical_url,
                    int(enabled),
                    sync_interval_minutes,
                    now,
                    terms_url,
                    policy_approved_at,
                    owner_contact,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT id FROM career_sources WHERE company_id = ? AND base_url = ?",
                (company_id, canonical_url),
            ).fetchone()
        assert row is not None
        return int(row["id"])

    def reschedule_career_sources(
        self,
        *,
        sync_interval_minutes: int,
        company_id: int | None = None,
    ) -> int:
        """Set a reviewed source cadence and make affected sources due immediately."""
        if not 15 <= sync_interval_minutes <= 10080:
            raise ValueError("sync interval must be between 15 minutes and 7 days")
        now = _now()
        parameters: tuple[Any, ...]
        company_filter = ""
        if company_id is None:
            parameters = (sync_interval_minutes, now, now)
        else:
            company_filter = " AND company_id = ?"
            parameters = (sync_interval_minutes, now, now, company_id)
        with self.connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE career_sources
                SET sync_interval_minutes = ?, next_sync_at = ?, updated_at = ?
                WHERE policy_approved_at IS NOT NULL{company_filter}
                """,
                parameters,
            )
            return int(cursor.rowcount)

    def due_career_sources(self, *, limit: int = 25) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT s.*, c.name AS company_name, c.slug AS company_slug
                FROM career_sources s JOIN companies c ON c.id = s.company_id
                WHERE s.enabled = 1 AND s.policy_approved_at IS NOT NULL
                  AND (s.next_sync_at IS NULL OR s.next_sync_at <= ?)
                ORDER BY COALESCE(s.next_sync_at, s.created_at) ASC LIMIT ?
                """,
                (_now(), min(max(limit, 1), 1000)),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_source_started(self, source_id: int) -> None:
        now = _now()
        with self.connect() as connection:
            connection.execute(
                "UPDATE career_sources SET last_started_at = ?, updated_at = ? WHERE id = ?",
                (now, now, source_id),
            )

    def mark_source_finished(self, source_id: int, *, success: bool, error: str = "") -> None:
        now = datetime.now(UTC).replace(microsecond=0)
        with self.connect() as connection:
            source = connection.execute(
                "SELECT sync_interval_minutes, consecutive_failures FROM career_sources WHERE id = ?",
                (source_id,),
            ).fetchone()
            if source is None:
                raise ValueError("career source not found")
            failures = 0 if success else int(source["consecutive_failures"]) + 1
            delay = (
                int(source["sync_interval_minutes"])
                if success
                else min(360, 5 * (2 ** min(failures, 6)))
            )
            connection.execute(
                """
                UPDATE career_sources SET
                    last_success_at = CASE WHEN ? THEN ? ELSE last_success_at END,
                    next_sync_at = ?, consecutive_failures = ?, last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    int(success),
                    now.isoformat(),
                    (now + timedelta(minutes=delay)).isoformat(),
                    failures,
                    "" if success else error[:1000],
                    now.isoformat(),
                    source_id,
                ),
            )
