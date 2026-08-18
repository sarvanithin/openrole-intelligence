"""Recompute job evidence after reviewed employer history changes."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime

from fortune_intel.services.sponsorship import EmployerHistory, assess_sponsorship
from fortune_intel.storage import JobRepository


def reassess_company_jobs(repository: JobRepository, company_id: int) -> int:
    history = repository.get_employer_history(company_id)
    with repository.connect() as connection:
        rows = connection.execute(
            "SELECT id, description FROM jobs WHERE company_id = ?", (company_id,)
        ).fetchall()
        for row in rows:
            assessment = assess_sponsorship(str(row["description"]), history)
            connection.execute(
                """UPDATE jobs SET sponsorship_tier = ?, sponsorship_evidence_score = ?,
                    sponsorship_reasons = ?, sponsorship_excerpt = ?,
                    sponsorship_rule_version = ? WHERE id = ?""",
                (
                    assessment.tier.value,
                    assessment.evidence_score,
                    json.dumps(assessment.reasons),
                    assessment.policy_excerpt,
                    assessment.rule_version,
                    row["id"],
                ),
            )
    return len(rows)


def reassess_all_jobs(repository: JobRepository) -> dict[str, object]:
    """Atomically recompute sponsorship evidence for every stored job."""

    current_year = datetime.now(UTC).year
    facts: dict[int, list[object]] = defaultdict(list)
    transitions: dict[str, int] = defaultdict(int)
    with repository.connect() as connection:
        for row in connection.execute(
            """SELECT company_id, fiscal_year, initial_approvals, initial_denials,
                lca_worker_positions, entity_match_confidence
            FROM sponsorship_facts WHERE fiscal_year >= ?""",
            (current_year - 4,),
        ):
            facts[int(row["company_id"])].append(row)

        histories: dict[int, EmployerHistory] = {}
        for company_id, rows in facts.items():
            def weight(year: int) -> float:
                return max(0.2, 1 - (0.2 * max(0, current_year - int(year))))

            histories[company_id] = EmployerHistory(
                uscis_initial_approvals=round(
                    sum(row["initial_approvals"] * weight(row["fiscal_year"]) for row in rows)
                ),
                uscis_initial_denials=round(
                    sum(row["initial_denials"] * weight(row["fiscal_year"]) for row in rows)
                ),
                lca_worker_positions=round(
                    sum(row["lca_worker_positions"] * weight(row["fiscal_year"]) for row in rows)
                ),
                latest_fiscal_year=max(int(row["fiscal_year"]) for row in rows),
                entity_match_confidence=min(
                    float(row["entity_match_confidence"]) for row in rows
                ),
                recency_weighted=True,
            )

        jobs = connection.execute(
            """SELECT id, company_id, description, sponsorship_tier
            FROM jobs ORDER BY id"""
        ).fetchall()
        updates = []
        for row in jobs:
            assessment = assess_sponsorship(
                str(row["description"]), histories.get(int(row["company_id"]), EmployerHistory())
            )
            old_tier = str(row["sponsorship_tier"])
            if old_tier != assessment.tier.value:
                transitions[f"{old_tier}->{assessment.tier.value}"] += 1
            updates.append(
                (
                    assessment.tier.value,
                    assessment.evidence_score,
                    json.dumps(assessment.reasons),
                    assessment.policy_excerpt,
                    assessment.rule_version,
                    row["id"],
                )
            )
        connection.executemany(
            """UPDATE jobs SET sponsorship_tier = ?, sponsorship_evidence_score = ?,
                sponsorship_reasons = ?, sponsorship_excerpt = ?,
                sponsorship_rule_version = ? WHERE id = ?""",
            updates,
        )
    return {"jobs_reassessed": len(jobs), "tier_transitions": dict(sorted(transitions.items()))}
