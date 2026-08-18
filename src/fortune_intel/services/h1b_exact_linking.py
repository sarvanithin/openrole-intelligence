"""Conservative bulk linking of exact DOL legal-employer identities."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from fortune_intel.services.reassessment import reassess_company_jobs
from fortune_intel.storage import JobRepository
from fortune_intel.storage.identity import normalize_company_name

MATCH_METHOD = "reviewed_exact_legal_name"
MATCH_CONFIDENCE = 1.0
_PROMOTABLE_METHODS = {"provisional_normalized_name", MATCH_METHOD}


def _group_by_normalized_name(
    records: list[dict[str, Any]], display_field: str
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        normalized = normalize_company_name(str(record[display_field]))
        if normalized and normalized == str(record["normalized_name"]):
            grouped[normalized].append(record)
    return dict(grouped)


def _already_current(existing: dict[str, Any] | None, employer: dict[str, Any]) -> bool:
    if existing is None:
        return False
    return (
        existing["match_method"] == MATCH_METHOD
        and float(existing["entity_match_confidence"]) == MATCH_CONFIDENCE
        and int(existing["lca_worker_positions"]) == int(employer["lca_worker_positions"])
        and str(existing["source_url"] or "") == str(employer["source_url"])
        and str(existing["source_document"] or "") == str(employer["source_document"])
        and str(existing["source_checksum"] or "") == str(employer["source_checksum"])
    )


def bulk_link_exact_h1b_employers(
    repository: JobRepository,
    *,
    dry_run: bool = False,
    report_limit: int = 250,
) -> dict[str, object]:
    """Link only one-to-one exact normalized legal names from the latest DOL year."""

    if not 0 <= report_limit <= 1000:
        raise ValueError("report_limit must be between 0 and 1000")
    inventory = repository.exact_h1b_link_inventory()
    fiscal_year = inventory["latest_fiscal_year"]
    companies = list(inventory["companies"])
    employers = list(inventory["employers"])
    if fiscal_year is None:
        return {
            "dry_run": dry_run,
            "latest_fiscal_year": None,
            "companies_considered": len(companies),
            "employer_records_considered": 0,
            "exact_one_to_one_matches": 0,
            "would_link": 0,
            "facts_written": 0,
            "already_linked": 0,
            "existing_reviewed_conflicts": 0,
            "jobs_reassessed": 0,
            "ambiguous_normalized_names": 0,
            "ambiguous_examples": [],
            "ambiguous_examples_truncated": False,
            "existing_reviewed_conflict_examples": [],
            "existing_reviewed_conflict_examples_truncated": False,
            "links": [],
            "links_truncated": False,
        }

    companies_by_name = _group_by_normalized_name(companies, "name")
    employers_by_name = _group_by_normalized_name(employers, "employer_name")
    shared_names = sorted(set(companies_by_name) & set(employers_by_name))
    ambiguous_names = [
        name
        for name in shared_names
        if len(companies_by_name[name]) != 1 or len(employers_by_name[name]) != 1
    ]
    ambiguous_name_set = set(ambiguous_names)
    exact_names = [name for name in shared_names if name not in ambiguous_name_set]

    stats = {
        "would_link": 0,
        "facts_written": 0,
        "already_linked": 0,
        "existing_reviewed_conflicts": 0,
        "jobs_reassessed": 0,
    }
    links: list[dict[str, object]] = []
    conflict_examples: list[dict[str, object]] = []
    for normalized_name in exact_names:
        company = companies_by_name[normalized_name][0]
        employer = employers_by_name[normalized_name][0]
        company_id = int(company["id"])
        source = str(employer["source"])
        existing = repository.get_sponsorship_fact(
            company_id, source=source, fiscal_year=int(fiscal_year)
        )
        if _already_current(existing, employer):
            status = "already_linked"
            stats[status] += 1
        elif existing is not None and existing["match_method"] not in _PROMOTABLE_METHODS:
            status = "existing_reviewed_conflict"
            stats["existing_reviewed_conflicts"] += 1
            if len(conflict_examples) < report_limit:
                conflict_examples.append(
                    {
                        "company_id": company_id,
                        "company_name": company["name"],
                        "employer_name": employer["employer_name"],
                        "source": source,
                        "fiscal_year": int(fiscal_year),
                        "existing_match_method": existing["match_method"],
                    }
                )
        elif dry_run:
            status = "would_link"
            stats[status] += 1
        else:
            repository.link_reviewed_h1b_employer(
                company_id,
                employer_name=str(employer["employer_name"]),
                fiscal_year=int(fiscal_year),
                match_method=MATCH_METHOD,
            )
            stats["facts_written"] += 1
            stats["jobs_reassessed"] += reassess_company_jobs(repository, company_id)
            status = "linked"
        if len(links) < report_limit:
            links.append(
                {
                    "company_id": company_id,
                    "company_name": company["name"],
                    "employer_name": employer["employer_name"],
                    "normalized_legal_name": normalized_name,
                    "fiscal_year": int(fiscal_year),
                    "lca_worker_positions": int(employer["lca_worker_positions"]),
                    "source": source,
                    "source_url": employer["source_url"],
                    "source_document": employer["source_document"],
                    "source_checksum": employer["source_checksum"],
                    "match_method": MATCH_METHOD,
                    "entity_match_confidence": MATCH_CONFIDENCE,
                    "status": status,
                }
            )

    ambiguous_examples = [
        {
            "normalized_legal_name": name,
            "company_count": len(companies_by_name[name]),
            "employer_record_count": len(employers_by_name[name]),
        }
        for name in ambiguous_names[:report_limit]
    ]
    return {
        "dry_run": dry_run,
        "latest_fiscal_year": int(fiscal_year),
        "companies_considered": len(companies),
        "employer_records_considered": len(employers),
        "company_names_considered": len(companies_by_name),
        "employer_names_considered": len(employers_by_name),
        "company_names_without_exact_employer": len(
            set(companies_by_name) - set(employers_by_name)
        ),
        "employer_names_without_exact_company": len(
            set(employers_by_name) - set(companies_by_name)
        ),
        "exact_one_to_one_matches": len(exact_names),
        **stats,
        "ambiguous_normalized_names": len(ambiguous_names),
        "ambiguous_examples": ambiguous_examples,
        "ambiguous_examples_truncated": len(ambiguous_names) > len(ambiguous_examples),
        "existing_reviewed_conflict_examples": conflict_examples,
        "existing_reviewed_conflict_examples_truncated": (
            stats["existing_reviewed_conflicts"] > len(conflict_examples)
        ),
        "links": links,
        "links_truncated": len(exact_names) > len(links),
    }
