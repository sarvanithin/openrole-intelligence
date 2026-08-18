"""CLI integration for evidence-backed website acquisition from SEC filings."""

from __future__ import annotations

import argparse
from typing import Any

from fortune_intel.importers.sec_filing_websites import (
    SecFilingWebsiteClient,
    import_sec_filing_company_websites,
)
from fortune_intel.services.discovery_pipeline import discover_company_sources
from fortune_intel.storage import JobRepository


def add_sec_filing_website_parser(commands: argparse._SubParsersAction[Any]) -> None:
    """Register the filing importer without growing the main CLI module."""
    filing_websites = commands.add_parser(
        "import-sec-filing-websites",
        help="Acquire explicit company websites from exact-CIK SEC filings",
    )
    filing_websites.add_argument("--actor", required=True)
    filing_websites.add_argument("--user-agent", default="")
    filing_websites.add_argument("--rate-per-second", type=float, default=5.0)
    filing_websites.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="SEC fetch workers (default: 1, maximum: 8; shared rate limit)",
    )
    filing_websites.add_argument("--limit", type=int)
    filing_websites.add_argument(
        "--after-cik",
        help="Resume after this CIK; companies are processed in deterministic CIK order",
    )
    filing_websites.add_argument("--dry-run", action="store_true")
    filing_websites.add_argument(
        "--discover-new",
        action="store_true",
        help="Run bounded ATS discovery only for website seeds written by this import",
    )
    filing_websites.add_argument(
        "--discovery-concurrency",
        type=int,
        default=4,
        help="Workers for opt-in ATS discovery (default: 4, maximum: 8)",
    )


def _seeds_by_company(repository: JobRepository) -> dict[int, tuple[str, str]]:
    return {
        int(company["id"]): (
            str(company.get("website_url") or ""),
            str(company.get("career_url") or ""),
        )
        for company in repository.list_companies(include_synthetic=False)
    }


def run_sec_filing_website_command(
    args: argparse.Namespace,
    repository: JobRepository,
) -> dict[str, object]:
    """Import filing evidence and optionally create review-only ATS candidates."""
    if not args.user_agent.strip():
        raise ValueError("--user-agent or SEC_USER_AGENT is required by SEC fair-access policy")
    if args.discover_new and args.dry_run:
        raise ValueError("--discover-new cannot be combined with --dry-run")
    if not 1 <= args.concurrency <= 8:
        raise ValueError("concurrency must be between 1 and 8")
    if not 1 <= args.discovery_concurrency <= 8:
        raise ValueError("discovery-concurrency must be between 1 and 8")

    previous_seeds = _seeds_by_company(repository) if args.discover_new else {}
    client = SecFilingWebsiteClient(
        user_agent=args.user_agent,
        requests_per_second=args.rate_per_second,
        concurrency=args.concurrency,
    )
    imported = import_sec_filing_company_websites(
        repository,
        client,
        actor=args.actor,
        limit=args.limit,
        after_cik=args.after_cik,
        dry_run=args.dry_run,
    )
    output: dict[str, object] = {"import": imported}
    if not args.discover_new:
        return output

    targets = [
        company
        for company in repository.list_companies(include_synthetic=False)
        if previous_seeds.get(int(company["id"]))
        != (
            str(company.get("website_url") or ""),
            str(company.get("career_url") or ""),
        )
        and (company.get("website_url") or company.get("career_url"))
    ]
    discoveries = discover_company_sources(
        repository,
        targets,
        actor=args.actor,
        concurrency=args.discovery_concurrency,
    )
    output["discovery"] = {
        "new_verified_seeds": len(targets),
        "targets_processed": len(discoveries),
        "results": discoveries,
        "approval": "not_performed",
    }
    return output
