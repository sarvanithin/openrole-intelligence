#!/usr/bin/env python3
"""Build an auditable career-URL registry from verified first-party evidence.

This exporter deliberately does not invent a URL from a company name.  A blank
row can be filled only from an enabled, approved career source or a candidate
whose exact ATS URL was either verified directly on the board or discovered
from an SEC-verified company website. Policy review remains required before a
candidate can become a scheduled source. The source CSV remains untouched.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class UrlEvidence:
    url: str
    source: str
    confidence: str
    priority: int


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--input", required=True, help="Existing registry CSV")
    parser.add_argument("--output", required=True, help="New combined registry CSV")
    parser.add_argument("--report", required=True, help="JSON export report")
    return parser.parse_args()


def _verified_website_company_ids(connection: sqlite3.Connection) -> set[int]:
    rows = connection.execute(
        """SELECT DISTINCT company_id FROM company_coverage_events
        WHERE reason LIKE 'Canonical website seed verified from SEC filing %'
           OR reason LIKE 'Canonical company URL imported by exact SEC CIK %'"""
    ).fetchall()
    return {int(row[0]) for row in rows}


def _add(candidates: dict[int, list[UrlEvidence]], company_id: int, evidence: UrlEvidence) -> None:
    candidates.setdefault(company_id, []).append(evidence)


def _first_party_candidates(connection: sqlite3.Connection) -> dict[int, list[UrlEvidence]]:
    """Return only source/candidate URLs with first-party verification evidence."""

    candidates: dict[int, list[UrlEvidence]] = {}
    for row in connection.execute(
        """SELECT company_id, base_url FROM career_sources
        WHERE enabled = 1 AND policy_approved_at IS NOT NULL
          AND base_url IS NOT NULL AND base_url != ''"""
    ):
        _add(
            candidates,
            int(row["company_id"]),
            UrlEvidence(str(row["base_url"]), "approved_source", "100", 3),
        )

    verified_websites = _verified_website_company_ids(connection)
    rows = connection.execute(
        """SELECT company_id, candidate_url, evidence_json, status, terms_status
        FROM career_source_candidates
        WHERE status IN ('discovered', 'approved')"""
    ).fetchall()
    for row in rows:
        try:
            evidence = json.loads(str(row["evidence_json"]))
        except json.JSONDecodeError:
            continue
        company_id = int(row["company_id"])
        method = str(evidence.get("review_method") or "")
        direct_verified = method in {
            "primary_source_exact_ats_url",
            "recorded_search_result_direct_primary_ats_identity",
        } and evidence.get("verification_status") == "verified"
        seeded_from_verified_site = (
            company_id in verified_websites
            and isinstance(evidence.get("seed_urls_checked"), list)
            and bool(evidence["seed_urls_checked"])
        )
        if direct_verified:
            _add(
                candidates,
                company_id,
                UrlEvidence(str(row["candidate_url"]), "direct_primary_ats_verified", "98", 2),
            )
        elif seeded_from_verified_site:
            _add(
                candidates,
                company_id,
                UrlEvidence(str(row["candidate_url"]), "sec_verified_site_discovery", "95", 1),
            )
    return candidates


def _choose(candidates: list[UrlEvidence]) -> UrlEvidence | None:
    """Select the strongest unique URL; conflicting same-tier URLs stay blank."""

    if not candidates:
        return None
    by_priority: dict[int, set[str]] = {}
    for candidate in candidates:
        by_priority.setdefault(candidate.priority, set()).add(candidate.url)
    priority = max(by_priority)
    if len(by_priority[priority]) != 1:
        return None
    selected_url = next(iter(by_priority[priority]))
    return next(candidate for candidate in candidates if candidate.url == selected_url)


def main() -> None:
    args = _arguments()
    input_path = Path(args.input)
    output_path = Path(args.output)
    report_path = Path(args.report)
    with sqlite3.connect(args.database) as connection:
        connection.row_factory = sqlite3.Row
        database_names = {
            int(row["id"]): str(row["name"])
            for row in connection.execute("SELECT id, name FROM companies")
        }
        candidates = _first_party_candidates(connection)

    with input_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("input CSV must have a header")
        fieldnames = list(reader.fieldnames)
        rows = list(reader)
    required = {"company_id", "company_name", "career_url", "source", "confidence"}
    if not required.issubset(fieldnames):
        raise ValueError("input CSV is missing required registry columns")

    report = {"rows": len(rows), "already_present": 0, "filled": 0, "unresolved": 0, "conflicts": 0}
    for row in rows:
        company_id = int((row.get("company_id") or "").strip())
        if database_names.get(company_id) != (row.get("company_name") or "").strip():
            raise ValueError(f"exact company identity mismatch for company_id {company_id}")
        if (row.get("career_url") or "").strip():
            report["already_present"] += 1
            continue
        selected = _choose(candidates.get(company_id, []))
        if selected is None:
            if candidates.get(company_id):
                report["conflicts"] += 1
            else:
                report["unresolved"] += 1
            continue
        row["career_url"] = selected.url
        row["source"] = selected.source
        row["confidence"] = selected.confidence
        report["filled"] += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
