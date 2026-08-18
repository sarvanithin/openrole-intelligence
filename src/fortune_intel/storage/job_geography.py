"""Persistable U.S.-eligibility decisions for collected job locations."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from fortune_intel.services.us_location import (
    LOCATION_RULE_VERSION,
    classify_us_job_location,
)


@dataclass(frozen=True, slots=True)
class JobGeography:
    us_eligibility: str
    evidence_json: str
    rule_version: str = LOCATION_RULE_VERSION


def assess_job_geography(location: str, metadata: dict[str, object]) -> JobGeography:
    assessment = classify_us_job_location(location, metadata=metadata)
    evidence = {
        "classification": assessment.classification.value,
        "indicators": list(assessment.evidence),
    }
    return JobGeography(
        us_eligibility=assessment.eligibility,
        evidence_json=json.dumps(evidence, sort_keys=True),
    )


def backfill_job_geography(connection: sqlite3.Connection) -> dict[str, int]:
    """Idempotently reassess jobs created under an older location rule."""

    rows = connection.execute(
        """SELECT id, location, metadata FROM jobs
        WHERE location_rule_version IS NULL OR location_rule_version != ?""",
        (LOCATION_RULE_VERSION,),
    ).fetchall()
    counts = {"eligible": 0, "ineligible": 0, "unknown": 0}
    updates: list[tuple[str, str, str, str]] = []
    for row in rows:
        try:
            raw_metadata: Any = json.loads(row[2] or "{}")
        except (TypeError, ValueError):
            raw_metadata = {}
        metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
        geography = assess_job_geography(str(row[1] or ""), metadata)
        counts[geography.us_eligibility] += 1
        updates.append(
            (
                geography.us_eligibility,
                geography.evidence_json,
                geography.rule_version,
                str(row[0]),
            )
        )
    connection.executemany(
        """UPDATE jobs SET us_eligibility = ?, location_evidence = ?,
        location_rule_version = ? WHERE id = ?""",
        updates,
    )
    counts["assessed"] = len(updates)
    return counts


def validate_job_geography(connection: sqlite3.Connection) -> list[str]:
    """Enforce the eligibility domain even on SQLite ALTER-based upgrades."""

    invalid = connection.execute(
        """SELECT 1 FROM jobs
        WHERE us_eligibility NOT IN ('eligible', 'ineligible', 'unknown')
        LIMIT 1"""
    ).fetchone()
    return ["jobs contain invalid us_eligibility values"] if invalid else []
