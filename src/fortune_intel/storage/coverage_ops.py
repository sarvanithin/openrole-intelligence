"""Auditable company coverage and career-source candidate operations."""

from __future__ import annotations

import ipaddress
import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from fortune_intel.domain import canonicalize_url
from fortune_intel.storage.coverage_schema import (
    CANDIDATE_STATUSES,
    COVERAGE_DISPOSITIONS,
    FINGERPRINT_FAMILIES,
    ROBOTS_STATUSES,
    TERMS_STATUSES,
)


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _timestamp(value: str | None, *, field: str, default: str | None = None) -> str | None:
    if value is None:
        return default
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.isoformat()


def normalize_public_url(value: str, *, field: str, optional: bool = False) -> str:
    if optional and not value.strip():
        return ""
    parsed = urlsplit(value.strip())
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not hostname or parsed.username:
        raise ValueError(f"{field} must be an absolute public HTTP(S) URL")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError(f"{field} must be an absolute public HTTP(S) URL")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError(f"{field} must be an absolute public HTTP(S) URL")
    return canonicalize_url(value)


def _choice(value: str, allowed: tuple[str, ...], *, field: str) -> str:
    normalized = value.strip().casefold()
    if normalized not in allowed:
        raise ValueError(f"invalid {field}: {value}")
    return normalized


class CoverageOperationsMixin:
    def set_company_disposition(
        self,
        company_id: int,
        disposition: str,
        *,
        reason: str,
        actor: str,
        reviewed_at: str | None = None,
        stale_after: str | None = None,
        candidate_id: int | None = None,
    ) -> None:
        target = _choice(disposition, COVERAGE_DISPOSITIONS, field="disposition")
        if not actor.strip():
            raise ValueError("actor is required for an auditable disposition change")
        occurred_at = _timestamp(reviewed_at, field="reviewed_at", default=_now())
        expires_at = _timestamp(stale_after, field="stale_after")
        with self.connect() as connection:
            company = connection.execute(
                "SELECT id, created_at FROM companies WHERE id = ?", (company_id,)
            ).fetchone()
            if company is None:
                raise ValueError("company not found")
            current = connection.execute(
                "SELECT disposition FROM company_coverage WHERE company_id = ?", (company_id,)
            ).fetchone()
            previous = str(current["disposition"]) if current else None
            if candidate_id is not None:
                candidate = connection.execute(
                    "SELECT company_id FROM career_source_candidates WHERE id = ?",
                    (candidate_id,),
                ).fetchone()
                if candidate is None or int(candidate["company_id"]) != company_id:
                    raise ValueError("candidate does not belong to company")
            connection.execute(
                """INSERT INTO company_coverage (
                    company_id, disposition, reason, last_reviewed_at, stale_after,
                    reviewed_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(company_id) DO UPDATE SET
                    disposition = excluded.disposition,
                    reason = excluded.reason,
                    last_reviewed_at = excluded.last_reviewed_at,
                    stale_after = excluded.stale_after,
                    reviewed_by = excluded.reviewed_by,
                    updated_at = excluded.updated_at""",
                (
                    company_id,
                    target,
                    reason.strip(),
                    occurred_at,
                    expires_at,
                    actor.strip(),
                    str(company["created_at"]),
                    occurred_at,
                ),
            )
            connection.execute(
                """INSERT INTO company_coverage_events (
                    company_id, candidate_id, from_disposition, to_disposition,
                    reason, actor, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    company_id,
                    candidate_id,
                    previous,
                    target,
                    reason.strip(),
                    actor.strip(),
                    occurred_at,
                ),
            )

    def upsert_source_candidate(
        self,
        company_id: int,
        *,
        candidate_url: str,
        kind: str,
        confidence: float,
        evidence: object,
        robots_status: str = "unknown",
        robots_checked_at: str | None = None,
        terms_url: str = "",
        terms_status: str = "unknown",
        terms_reviewed_at: str | None = None,
        discovered_at: str | None = None,
    ) -> int:
        if not kind.strip():
            raise ValueError("candidate kind is required")
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        url = normalize_public_url(candidate_url, field="candidate_url")
        policy_url = normalize_public_url(terms_url, field="terms_url", optional=True)
        robots = _choice(robots_status, ROBOTS_STATUSES, field="robots_status")
        terms = _choice(terms_status, TERMS_STATUSES, field="terms_status")
        checked_at = _timestamp(robots_checked_at, field="robots_checked_at")
        terms_at = _timestamp(terms_reviewed_at, field="terms_reviewed_at")
        observed_at = _timestamp(discovered_at, field="discovered_at", default=_now())
        try:
            encoded_evidence = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as error:
            raise ValueError("evidence must be JSON serializable") from error
        with self.connect() as connection:
            if (
                connection.execute("SELECT 1 FROM companies WHERE id = ?", (company_id,)).fetchone()
                is None
            ):
                raise ValueError("company not found")
            connection.execute(
                """INSERT INTO career_source_candidates (
                    company_id, candidate_url, kind, confidence, evidence_json,
                    robots_status, robots_checked_at, terms_url, terms_status,
                    terms_reviewed_at, discovered_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(company_id, candidate_url) DO UPDATE SET
                    kind = CASE
                        WHEN career_source_candidates.status = 'discovered' THEN excluded.kind
                        ELSE career_source_candidates.kind
                    END,
                    confidence = excluded.confidence,
                    evidence_json = excluded.evidence_json,
                    robots_status = CASE
                        WHEN career_source_candidates.status = 'discovered'
                            THEN excluded.robots_status
                        ELSE career_source_candidates.robots_status
                    END,
                    robots_checked_at = CASE
                        WHEN career_source_candidates.status = 'discovered'
                            THEN excluded.robots_checked_at
                        ELSE career_source_candidates.robots_checked_at
                    END,
                    terms_url = CASE
                        WHEN career_source_candidates.terms_status IN
                            ('permitted', 'restricted', 'prohibited')
                            THEN career_source_candidates.terms_url
                        ELSE excluded.terms_url
                    END,
                    terms_status = CASE
                        WHEN career_source_candidates.terms_status IN
                            ('permitted', 'restricted', 'prohibited')
                            THEN career_source_candidates.terms_status
                        ELSE excluded.terms_status
                    END,
                    terms_reviewed_at = CASE
                        WHEN career_source_candidates.terms_status IN
                            ('permitted', 'restricted', 'prohibited')
                            THEN career_source_candidates.terms_reviewed_at
                        ELSE excluded.terms_reviewed_at
                    END,
                    updated_at = excluded.updated_at""",
                (
                    company_id,
                    url,
                    kind.strip().casefold(),
                    confidence,
                    encoded_evidence,
                    robots,
                    checked_at,
                    policy_url,
                    terms,
                    terms_at,
                    observed_at,
                    observed_at,
                    observed_at,
                ),
            )
            row = connection.execute(
                "SELECT id FROM career_source_candidates WHERE company_id = ? AND candidate_url = ?",
                (company_id, url),
            ).fetchone()
            connection.execute(
                """UPDATE company_coverage
                SET last_discovered_at = ?, updated_at = ? WHERE company_id = ?""",
                (observed_at, observed_at, company_id),
            )
        assert row is not None
        return int(row["id"])

    def review_source_candidate(
        self,
        candidate_id: int,
        *,
        status: str,
        reviewed_by: str,
        review_notes: str = "",
        reviewed_at: str | None = None,
        terms_url: str | None = None,
        terms_status: str | None = None,
    ) -> None:
        candidate_status = _choice(status, CANDIDATE_STATUSES, field="candidate status")
        if not reviewed_by.strip():
            raise ValueError("reviewed_by is required")
        timestamp = _timestamp(reviewed_at, field="reviewed_at", default=_now())
        policy_url = (
            normalize_public_url(terms_url, field="terms_url") if terms_url is not None else None
        )
        policy_status = (
            _choice(terms_status, TERMS_STATUSES, field="terms_status")
            if terms_status is not None
            else None
        )
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE career_source_candidates SET status = ?, reviewed_at = ?,
                    reviewed_by = ?, review_notes = ?,
                    terms_url = COALESCE(?, terms_url),
                    terms_status = COALESCE(?, terms_status),
                    terms_reviewed_at = CASE WHEN ? IS NOT NULL THEN ? ELSE terms_reviewed_at END,
                    updated_at = ? WHERE id = ?""",
                (
                    candidate_status,
                    timestamp,
                    reviewed_by.strip(),
                    review_notes.strip(),
                    policy_url,
                    policy_status,
                    policy_status,
                    timestamp,
                    timestamp,
                    candidate_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("candidate not found")

    def get_source_candidate(self, candidate_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT candidate.*, company.name AS company_name
                FROM career_source_candidates candidate
                JOIN companies company ON company.id = candidate.company_id
                WHERE candidate.id = ?""",
                (candidate_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["evidence"] = json.loads(result.pop("evidence_json"))
        return result

    def get_company_coverage(self, company_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM company_coverage WHERE company_id = ?", (company_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def mark_company_discovered(
        self,
        company_id: int,
        *,
        discovered_at: str | None = None,
    ) -> None:
        """Record completion of a bounded discovery attempt, including empty results."""

        timestamp = _timestamp(discovered_at, field="discovered_at", default=_now())
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE company_coverage SET last_discovered_at = ?, updated_at = ?
                WHERE company_id = ?""",
                (timestamp, timestamp, company_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("company coverage not found")

    def list_source_candidates(self, company_id: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM career_source_candidates WHERE company_id = ?
                ORDER BY confidence DESC, discovered_at DESC""",
                (company_id,),
            ).fetchall()
        result = [dict(row) for row in rows]
        for candidate in result:
            candidate["evidence"] = json.loads(candidate.pop("evidence_json"))
        return result

    def upsert_source_fingerprint(
        self,
        company_id: int,
        *,
        observed_url: str,
        family: str,
        evidence: object,
        actor: str,
        observed_at: str | None = None,
        mark_discovered: bool = True,
    ) -> int:
        """Record passive inventory without creating an activatable source candidate.

        ``mark_discovered=False`` retains external lead provenance without claiming
        that bounded discovery from a verified primary-company seed occurred.
        """

        if not actor.strip():
            raise ValueError("actor is required for an auditable fingerprint")
        fingerprint_family = _choice(
            family,
            FINGERPRINT_FAMILIES,
            field="fingerprint family",
        )
        value = observed_url.strip()
        normalize_public_url(value, field="observed_url")
        parsed = urlsplit(value)
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("observed_url must be an absolute public HTTPS URL") from error
        if parsed.scheme.casefold() != "https" or port not in {None, 443}:
            raise ValueError("observed_url must be an absolute public HTTPS URL")
        host = (parsed.hostname or "").casefold().rstrip(".")
        timestamp = _timestamp(observed_at, field="observed_at", default=_now())
        try:
            encoded_evidence = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as error:
            raise ValueError("evidence must be JSON serializable") from error
        with self.connect() as connection:
            if (
                connection.execute("SELECT 1 FROM companies WHERE id = ?", (company_id,)).fetchone()
                is None
            ):
                raise ValueError("company not found")
            connection.execute(
                """INSERT INTO career_source_fingerprints (
                    company_id, observed_url, family, host, evidence_json,
                    observation_count, first_seen_at, last_seen_at, last_observed_by
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(company_id, family, observed_url) DO UPDATE SET
                    host = excluded.host,
                    evidence_json = excluded.evidence_json,
                    observation_count = career_source_fingerprints.observation_count + 1,
                    last_seen_at = excluded.last_seen_at,
                    last_observed_by = excluded.last_observed_by""",
                (
                    company_id,
                    value,
                    fingerprint_family,
                    host,
                    encoded_evidence,
                    timestamp,
                    timestamp,
                    actor.strip(),
                ),
            )
            row = connection.execute(
                """SELECT id FROM career_source_fingerprints
                WHERE company_id = ? AND family = ? AND observed_url = ?""",
                (company_id, fingerprint_family, value),
            ).fetchone()
            if mark_discovered:
                connection.execute(
                    """UPDATE company_coverage SET last_discovered_at = ?, updated_at = ?
                    WHERE company_id = ?""",
                    (timestamp, timestamp, company_id),
                )
        assert row is not None
        return int(row["id"])

    def list_source_fingerprints(self, company_id: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM career_source_fingerprints WHERE company_id = ?
                ORDER BY family, observed_url""",
                (company_id,),
            ).fetchall()
        result = [dict(row) for row in rows]
        for fingerprint in result:
            fingerprint["evidence"] = json.loads(fingerprint.pop("evidence_json"))
        return result

    def source_fingerprint_inventory(self) -> list[dict[str, Any]]:
        """Rank passive families by independently observed companies."""

        with self.connect() as connection:
            rows = connection.execute(
                """SELECT family, COUNT(DISTINCT company_id) AS companies,
                    COUNT(*) AS urls, SUM(observation_count) AS observations,
                    MAX(last_seen_at) AS last_seen_at
                FROM career_source_fingerprints GROUP BY family
                ORDER BY companies DESC, urls DESC, family"""
            ).fetchall()
        return [dict(row) for row in rows]

    def company_coverage_events(self, company_id: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM company_coverage_events WHERE company_id = ?
                ORDER BY occurred_at DESC, id DESC""",
                (company_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def reconcile_source_coverage(self, company_id: int) -> str:
        """Derive public coverage from approved source health without overstating it."""
        with self.connect() as connection:
            sources = connection.execute(
                """SELECT last_success_at, consecutive_failures FROM career_sources
                WHERE company_id = ? AND enabled = 1
                  AND policy_approved_at IS NOT NULL""",
                (company_id,),
            ).fetchall()
        if not sources:
            return self.get_company_coverage(company_id)["disposition"]
        successful = [source for source in sources if source["last_success_at"]]
        if any(int(source["consecutive_failures"]) < 2 for source in successful):
            target = "supported"
            reason = "At least one approved career source has a recent successful manifest"
        elif successful:
            target = "stale"
            reason = "Approved career sources have repeated failures since their last success"
        else:
            target = "approved"
            reason = "Career source is approved but has not completed a successful manifest"
        current = self.get_company_coverage(company_id)
        if current is None or current["disposition"] != target:
            self.set_company_disposition(
                company_id,
                target,
                reason=reason,
                actor="source-health-reconciler",
            )
        return target
