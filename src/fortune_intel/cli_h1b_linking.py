"""CLI integration for exact latest-year H-1B employer linking."""

from __future__ import annotations

from argparse import _SubParsersAction

from fortune_intel.services.h1b_exact_linking import bulk_link_exact_h1b_employers
from fortune_intel.services.reassessment import reassess_company_jobs
from fortune_intel.storage import JobRepository

H1B_LINK_COMMANDS = frozenset({"link-h1b-employer", "bulk-link-exact-h1b"})


def add_h1b_link_parsers(commands: _SubParsersAction) -> None:
    manual = commands.add_parser(
        "link-h1b-employer", help="Link a company to an exact reviewed DOL legal employer"
    )
    manual.add_argument("--company", required=True)
    manual.add_argument("--employer", required=True)
    manual.add_argument("--fiscal-year", type=int, required=True)
    command = commands.add_parser(
        "bulk-link-exact-h1b",
        help="Link one-to-one exact legal names from the latest imported H-1B year",
    )
    command.add_argument("--dry-run", action="store_true")
    command.add_argument("--report-limit", type=int, default=250)


def run_h1b_link_command(args: object, repository: JobRepository) -> dict[str, object]:
    command = str(args.command)
    if command == "link-h1b-employer":
        company = repository.find_company_by_normalized_name(str(args.company))
        if company is None:
            raise SystemExit("company not found or normalized name is ambiguous")
        company_id = int(company["id"])
        repository.link_reviewed_h1b_employer(
            company_id,
            employer_name=str(args.employer),
            fiscal_year=int(args.fiscal_year),
        )
        return {
            "company_id": company_id,
            "linked": True,
            "jobs_reassessed": reassess_company_jobs(repository, company_id),
        }
    try:
        return bulk_link_exact_h1b_employers(
            repository,
            dry_run=bool(args.dry_run),
            report_limit=int(args.report_limit),
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error
