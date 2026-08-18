"""Transactional metadata correction for passive source fingerprints."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fortune_intel.storage.coverage_schema import FINGERPRINT_FAMILIES

FingerprintClassifier = Callable[[str], str | None]
_MISSING = object()


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _moment(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("stored fingerprint timestamp must include a timezone")
    return parsed


def _audit_evidence(
    evidence: object,
    *,
    actor: str,
    occurred_at: str,
    target_family: str,
    merged_evidence: object = _MISSING,
) -> dict[str, object]:
    audit: dict[str, object] = {
        "actor": actor,
        "from_family": "unknown_external",
        "occurred_at": occurred_at,
        "to_family": target_family,
        "method": "strict_connector_parser",
    }
    result: dict[str, object] = {
        "original_evidence": evidence,
        "reclassification": audit,
    }
    if merged_evidence is not _MISSING:
        result["existing_target_evidence"] = merged_evidence
        audit["merged_duplicate"] = True
    return result


class FingerprintOperationsMixin:
    def reclassify_source_fingerprints(
        self,
        classifier: FingerprintClassifier,
        *,
        actor: str,
        dry_run: bool = False,
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        """Promote exact parser matches out of unknown inventory without scheduling them."""

        audit_actor = actor.strip()
        if not audit_actor:
            raise ValueError("actor is required for auditable fingerprint reclassification")
        timestamp = occurred_at or _now()
        _moment(timestamp)
        report: dict[str, Any] = {
            "scanned": 0,
            "reclassified": 0,
            "merged": 0,
            "unchanged": 0,
            "by_family": {},
            "dry_run": dry_run,
        }
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT * FROM career_source_fingerprints
                WHERE family = 'unknown_external' ORDER BY id"""
            ).fetchall()
            report["scanned"] = len(rows)
            for row in rows:
                target_family = classifier(str(row["observed_url"]))
                if target_family is None or target_family == "unknown_external":
                    report["unchanged"] += 1
                    continue
                if target_family not in FINGERPRINT_FAMILIES:
                    raise ValueError(
                        f"classifier returned invalid fingerprint family: {target_family}"
                    )
                target = connection.execute(
                    """SELECT * FROM career_source_fingerprints
                    WHERE company_id = ? AND family = ? AND observed_url = ?""",
                    (row["company_id"], target_family, row["observed_url"]),
                ).fetchone()
                report["reclassified"] += 1
                report["by_family"][target_family] = (
                    int(report["by_family"].get(target_family, 0)) + 1
                )
                if target is not None:
                    report["merged"] += 1
                if dry_run:
                    continue
                source_evidence = json.loads(str(row["evidence_json"]))
                if target is None:
                    evidence = _audit_evidence(
                        source_evidence,
                        actor=audit_actor,
                        occurred_at=timestamp,
                        target_family=target_family,
                    )
                    connection.execute(
                        """UPDATE career_source_fingerprints
                        SET family = ?, evidence_json = ? WHERE id = ?""",
                        (
                            target_family,
                            json.dumps(evidence, sort_keys=True, separators=(",", ":")),
                            row["id"],
                        ),
                    )
                    continue
                source_last = _moment(str(row["last_seen_at"]))
                target_last = _moment(str(target["last_seen_at"]))
                last_row = row if source_last > target_last else target
                evidence = _audit_evidence(
                    source_evidence,
                    actor=audit_actor,
                    occurred_at=timestamp,
                    target_family=target_family,
                    merged_evidence=json.loads(str(target["evidence_json"])),
                )
                first_seen = min(
                    (str(row["first_seen_at"]), str(target["first_seen_at"])),
                    key=_moment,
                )
                last_seen = max(
                    (str(row["last_seen_at"]), str(target["last_seen_at"])),
                    key=_moment,
                )
                connection.execute(
                    """UPDATE career_source_fingerprints SET
                        host = ?, evidence_json = ?, observation_count = ?,
                        first_seen_at = ?, last_seen_at = ?, last_observed_by = ?
                    WHERE id = ?""",
                    (
                        last_row["host"],
                        json.dumps(evidence, sort_keys=True, separators=(",", ":")),
                        int(row["observation_count"]) + int(target["observation_count"]),
                        first_seen,
                        last_seen,
                        last_row["last_observed_by"],
                        target["id"],
                    ),
                )
                connection.execute(
                    "DELETE FROM career_source_fingerprints WHERE id = ?", (row["id"],)
                )
        return report
