"""Command line interface."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
from dataclasses import asdict
from pathlib import Path

from fortune_intel import (
    cli_acquisition,
    cli_fingerprints,
    cli_h1b_linking,
    cli_search_harvest,
    cli_wikidata,
)
from fortune_intel.cli_output import write_coverage_audit
from fortune_intel.cli_sec_filings import (
    add_sec_filing_website_parser,
    run_sec_filing_website_command,
)
from fortune_intel.config import Settings
from fortune_intel.importers.career_registry import import_career_url_registry
from fortune_intel.importers.companies import import_companies
from fortune_intel.importers.discovery_leads import import_discovery_leads
from fortune_intel.importers.dol_h1b import import_dol_lca
from fortune_intel.importers.jobseek_board_registry import import_jobseek_board_registry
from fortune_intel.importers.sec_companies import import_sec_companies
from fortune_intel.importers.sec_websites import (
    SecSubmissionsWebsiteClient,
    import_sec_company_websites,
)
from fortune_intel.importers.source_candidates import import_reviewed_source_candidates
from fortune_intel.importers.sources import import_source_registry
from fortune_intel.importers.website_leads import import_website_leads
from fortune_intel.importers.websites import import_company_websites
from fortune_intel.observability import configure_logging
from fortune_intel.scheduler import scheduler_lock, scheduler_loop
from fortune_intel.seed import seed_demo
from fortune_intel.services.bulk_source_approval import approve_discovered_sources
from fortune_intel.services.discovery_pipeline import discover_company_sources
from fortune_intel.services.discovery_priority import build_discovery_priority_report
from fortune_intel.services.ingestion import CompanySource, sync_companies
from fortune_intel.services.licensed_lead_verification import promote_verified_discovery_leads
from fortune_intel.services.licensed_website_verification import promote_verified_website_leads
from fortune_intel.services.reassessment import reassess_all_jobs
from fortune_intel.services.registry_career_portal_runner import run_registry_career_portal_verifier
from fortune_intel.services.source_approval import approve_source_candidate
from fortune_intel.services.source_sync import sync_due_sources
from fortune_intel.storage import JobRepository


def _repository(path: str) -> JobRepository:
    repository = JobRepository(path)
    repository.initialize()
    return repository


def _company_sources(path: str) -> list[CompanySource]:
    sources = []
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            lowered = {key.casefold(): (value or "").strip() for key, value in row.items()}
            name = lowered.get("company name") or lowered.get("name") or ""
            url = lowered.get("career search url") or lowered.get("career_url") or ""
            if name and url:
                sources.append(
                    CompanySource(
                        name=name,
                        career_url=url,
                        ats_type=lowered.get("platform type") or lowered.get("ats_type") or "",
                    )
                )
    return sources


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="job-intel")
    root.add_argument("--database", default=os.getenv("JOB_INTEL_DB", "data/job_intel.db"))
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="Create or migrate the local database")
    commands.add_parser("seed-demo", help="Load a synthetic showcase dataset")

    companies = commands.add_parser("import-companies", help="Import a user-supplied company CSV")
    companies.add_argument("csv")
    companies.add_argument("--collection", default="Custom")
    companies.add_argument("--year", type=int)

    dol = commands.add_parser("import-dol", help="Import an official DOL LCA CSV/XLSX")
    dol.add_argument("file")
    dol.add_argument("--fiscal-year", type=int, required=True)

    scrape = commands.add_parser(
        "scrape", help="Sync companies through the existing ATS collectors"
    )
    scrape.add_argument("companies_csv")
    scrape.add_argument("--concurrency", type=int, default=3)
    scrape.add_argument("--limit", type=int)

    add_source = commands.add_parser("add-source", help="Register an approved public ATS source")
    add_source.add_argument("--company", required=True, help="Exact imported company name")
    add_source.add_argument(
        "--kind",
        required=True,
        choices=[
            "adp_workforce_now",
            "greenhouse",
            "lever",
            "ashby",
            "oracle_recruiting",
            "smartrecruiters",
            "ukg_recruiting_public",
            "workday",
        ],
    )
    add_source.add_argument("--board-token", required=True)
    add_source.add_argument("--url", required=True)
    add_source.add_argument("--terms-url", required=True)
    add_source.add_argument("--policy-approved-at", required=True, help="ISO-8601 review timestamp")
    add_source.add_argument("--owner-contact", required=True)
    add_source.add_argument(
        "--interval", type=int, default=60, help="Refresh cadence in minutes (default: 60)"
    )

    source_registry = commands.add_parser(
        "import-sources", help="Bulk-register a reviewed deterministic ATS source CSV"
    )
    source_registry.add_argument("csv")

    reschedule = commands.add_parser(
        "reschedule-sources",
        help="Change reviewed source cadence and make affected sources due now",
    )
    reschedule.add_argument("--interval", type=int, required=True)
    target = reschedule.add_mutually_exclusive_group(required=True)
    target.add_argument("--all", dest="all_sources", action="store_true")
    target.add_argument("--company", help="Exact imported company name")

    sec = commands.add_parser(
        "import-sec", help="Import the public SEC ticker/CIK company universe"
    )
    sec.add_argument("json")
    sec.add_argument("--year", type=int)

    websites = commands.add_parser(
        "import-websites", help="Import reviewed canonical company website seeds"
    )
    websites.add_argument("csv")

    source_candidates = commands.add_parser(
        "import-source-candidates", help="Import primary-source-reviewed exact ATS candidates"
    )
    source_candidates.add_argument("csv")

    jobseek_registry = commands.add_parser(
        "import-jobseek-board-registry",
        help="Import attributed Jobseek board configuration as unverified passive leads",
    )
    jobseek_registry.add_argument("boards_csv")
    jobseek_registry.add_argument("companies_csv")
    jobseek_registry.add_argument(
        "--source-revision",
        required=True,
        help="Immutable 40-character Jobseek git SHA used for both CSV files",
    )
    jobseek_registry.add_argument("--retrieved-at", required=True)
    jobseek_registry.add_argument("--actor", required=True)
    jobseek_registry.add_argument(
        "--permission-basis",
        required=True,
        help="Recorded operator authorization context; retain upstream attribution and license notice",
    )

    discovery_leads = commands.add_parser(
        "import-discovery-leads",
        help="Import licensed third-party career URLs as unverified passive leads",
    )
    discovery_leads.add_argument("csv")

    website_leads = commands.add_parser(
        "import-website-leads",
        help="Import licensed third-party company websites as unverified passive leads",
    )
    website_leads.add_argument("csv")

    career_registry = commands.add_parser(
        "import-career-url-registry",
        help="Import user-supplied career URLs as unverified passive inventory",
    )
    career_registry.add_argument("csv")
    career_registry.add_argument("--actor", required=True)
    career_registry.add_argument("--observed-at", required=True)

    verify_leads = commands.add_parser(
        "verify-discovery-leads",
        help="Verify licensed ATS leads at their public board before candidate creation",
    )
    verify_leads.add_argument("--policy", action="append", required=True, metavar="KIND=URL")
    verify_leads.add_argument("--policy-approved-at", required=True)
    verify_leads.add_argument("--actor", required=True)
    verify_leads.add_argument("--limit", type=int, default=100)

    verify_website_leads = commands.add_parser(
        "verify-website-leads",
        help="Verify licensed website leads using first-party organization declarations",
    )
    verify_website_leads.add_argument("--actor", required=True)
    verify_website_leads.add_argument("--limit", type=int, default=100)

    verify_registry_portals = commands.add_parser(
        "verify-registry-career-portals",
        help="Durably verify custom registry career pages as discovery seeds only",
    )
    verify_registry_portals.add_argument("--actor", required=True)
    verify_registry_portals.add_argument("--batch-size", type=int, default=200)
    verify_registry_portals.add_argument("--concurrency", type=int, default=8)
    verify_registry_portals.add_argument("--shard-count", type=int, default=1)
    verify_registry_portals.add_argument("--shard-index", type=int, default=0)
    verify_registry_portals.add_argument("--max-batches", type=int, default=100)
    verify_registry_portals.add_argument("--pace-seconds", type=float, default=0.5)
    verify_registry_portals.add_argument(
        "--policy",
        action="append",
        default=cli_acquisition._environment_policies(),
        metavar="KIND=URL",
    )
    verify_registry_portals.add_argument(
        "--policy-approved-at", default=os.getenv("ATS_POLICY_APPROVED_AT", "")
    )
    verify_registry_portals.add_argument("--approval-concurrency", type=int, default=4)
    verify_registry_portals.add_argument("--interval", type=int, default=60)

    cli_wikidata.add_wikidata_website_parser(commands)

    sec_websites = commands.add_parser("import-sec-websites")
    sec_websites.add_argument("--actor", required=True)
    sec_websites.add_argument("--user-agent", default=os.getenv("SEC_USER_AGENT", ""))
    sec_websites.add_argument("--rate-per-second", type=float, default=5.0)
    sec_websites.add_argument("--concurrency", type=int, default=1)
    sec_websites.add_argument("--limit", type=int)
    sec_websites.add_argument("--dry-run", action="store_true")
    add_sec_filing_website_parser(commands)
    merge = commands.add_parser(
        "merge-companies", help="Merge a reviewed duplicate into a canonical company identity"
    )
    merge.add_argument("--source", required=True, help="Exact duplicate company name")
    merge.add_argument("--target", required=True, help="Exact canonical company name")
    merge.add_argument("--actor", required=True)
    merge.add_argument("--reason", required=True)

    discover = commands.add_parser(
        "discover-sources", help="Discover reviewable ATS candidates from verified websites"
    )
    discovery_target = discover.add_mutually_exclusive_group(required=True)
    discovery_target.add_argument("--all", dest="all_companies", action="store_true")
    discovery_target.add_argument("--company", help="Exact imported company name")
    discover.add_argument("--limit", type=int, default=100)
    discover.add_argument("--concurrency", type=int, default=4)
    discover.add_argument("--actor", required=True)
    discover.add_argument(
        "--coverage-status",
        action="append",
        choices=("unreviewed", "candidate", "unsupported", "blocked", "stale"),
        help="Repeat to restrict --all to specific current coverage dispositions",
    )
    discover.add_argument(
        "--after-company-id",
        type=int,
        default=0,
        help="Resume deterministic --all processing after this company ID",
    )

    discovery_priority = commands.add_parser(
        "discovery-priority",
        help="Emit an auditable batch for verified website and career-source acquisition",
    )
    discovery_priority.add_argument("--batch-size", type=int, default=100)
    discovery_priority.add_argument("--batch-number", type=int, default=1)

    coverage_audit = commands.add_parser(
        "coverage-audit", help="Emit the strict per-company coverage checklist"
    )
    coverage_audit.add_argument("--format", choices=("json", "csv"), default="json")
    coverage_audit.add_argument("--company", default="", help="Company-name substring")
    coverage_audit.add_argument("--status", choices=("all", "covered", "incomplete"), default="all")
    coverage_audit.add_argument("--limit", type=int, default=10000)
    coverage_audit.add_argument("--offset", type=int, default=0)

    approve = commands.add_parser(
        "approve-source-candidate",
        help="Probe, ingest, and approve one reviewed deterministic ATS candidate",
    )
    approve.add_argument("candidate_id", type=int)
    approve.add_argument("--terms-url", required=True)
    approve.add_argument("--policy-approved-at", required=True)
    approve.add_argument("--actor", required=True)
    approve.add_argument("--interval", type=int, default=60)

    bulk_approve = commands.add_parser(
        "approve-discovered-sources",
        help="Probe and activate complete manifests using reviewed vendor policy URLs",
    )
    bulk_approve.add_argument("--policy", action="append", required=True, metavar="KIND=URL")
    bulk_approve.add_argument("--policy-approved-at", required=True)
    bulk_approve.add_argument("--actor", required=True)
    bulk_approve.add_argument("--interval", type=int, default=60)
    bulk_approve.add_argument("--limit", type=int, default=500)
    bulk_approve.add_argument("--concurrency", type=int, default=4)
    bulk_approve.add_argument(
        "--candidate-id",
        action="append",
        type=int,
        help="Repeat to restrict approval to an exact reviewed candidate batch",
    )
    cli_h1b_linking.add_h1b_link_parsers(commands)
    cli_acquisition.add_acquisition_parsers(commands)
    cli_fingerprints.add_fingerprint_parser(commands)
    cli_search_harvest.add_search_harvest_parser(commands)
    sync = commands.add_parser("sync-sources", help="Sync currently due approved ATS sources once")
    sync.add_argument("--limit", type=int, default=25)
    sync.add_argument("--concurrency", type=int, default=4)

    scheduler = commands.add_parser(
        "run-scheduler", help="Continuously sync due approved ATS sources"
    )
    scheduler.add_argument("--poll-seconds", type=int, default=60)
    scheduler.add_argument("--concurrency", type=int, default=4)
    scheduler.add_argument("--batch-size", type=int, default=100)

    commands.add_parser("source-status", help="Show registered source health")
    commands.add_parser(
        "reassess-sponsorship", help="Atomically reapply current sponsorship rules to all jobs"
    )

    serve = commands.add_parser("serve", help="Run the API and dashboard")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    return root


def main() -> None:
    args = parser().parse_args()
    configure_logging(os.getenv("LOG_LEVEL", "INFO"))
    repository = _repository(args.database)
    if args.command == "init-db":
        print(f"Initialized {args.database}")
    elif args.command == "seed-demo":
        if Settings.from_env(database_path=args.database).environment == "production":
            raise SystemExit("seed-demo is disabled in production")
        print(f"Seeded {seed_demo(repository)} synthetic jobs")
    elif args.command == "import-companies":
        count = import_companies(
            repository,
            args.csv,
            collection_name=args.collection,
            collection_year=args.year,
        )
        print(f"Imported {count} companies")
    elif args.command == "import-dol":
        print(
            json.dumps(
                import_dol_lca(repository, args.file, fiscal_year=args.fiscal_year),
                indent=2,
            )
        )
    elif args.command == "scrape":
        companies = _company_sources(args.companies_csv)
        if args.limit:
            companies = companies[: args.limit]
        results = asyncio.run(sync_companies(repository, companies, concurrency=args.concurrency))
        print(json.dumps(results, indent=2))
    elif args.command == "add-source":
        company = repository.find_company_by_normalized_name(args.company)
        if company is None:
            raise SystemExit("company not found or normalized name is ambiguous")
        source_id = repository.upsert_career_source(
            int(company["id"]),
            kind=args.kind,
            board_token=args.board_token,
            base_url=args.url,
            sync_interval_minutes=args.interval,
            terms_url=args.terms_url,
            policy_approved_at=args.policy_approved_at,
            owner_contact=args.owner_contact,
        )
        print(f"Registered source {source_id} for {company['name']}")
    elif args.command == "import-sources":
        print(f"Registered {import_source_registry(repository, args.csv)} reviewed sources")
    elif args.command == "reschedule-sources":
        company_id = None
        if args.company:
            company = repository.find_company_by_normalized_name(args.company)
            if company is None:
                raise SystemExit("company not found or normalized name is ambiguous")
            company_id = int(company["id"])
        count = repository.reschedule_career_sources(
            sync_interval_minutes=args.interval,
            company_id=company_id,
        )
        print(
            f"Rescheduled {count} reviewed sources to every {args.interval} minutes; "
            "affected enabled sources are due now"
        )
    elif args.command == "import-sec":
        print(
            json.dumps(
                import_sec_companies(repository, args.json, collection_year=args.year), indent=2
            )
        )
    elif args.command == "import-websites":
        print(f"Imported {import_company_websites(repository, args.csv)} reviewed websites")
    elif args.command == "import-source-candidates":
        print(
            f"Imported {import_reviewed_source_candidates(repository, args.csv)} "
            "reviewed ATS candidates"
        )
    elif args.command == "import-jobseek-board-registry":
        print(
            json.dumps(
                asdict(
                    import_jobseek_board_registry(
                        repository,
                        boards_csv=args.boards_csv,
                        companies_csv=args.companies_csv,
                        source_revision=args.source_revision,
                        retrieved_at=args.retrieved_at,
                        actor=args.actor,
                        permission_basis=args.permission_basis,
                    )
                ),
                indent=2,
            )
        )
    elif args.command == "import-discovery-leads":
        print(f"Imported {import_discovery_leads(repository, args.csv)} unverified discovery leads")
    elif args.command == "import-website-leads":
        print(f"Imported {import_website_leads(repository, args.csv)} unverified website leads")
    elif args.command == "import-career-url-registry":
        print(
            json.dumps(
                asdict(
                    import_career_url_registry(
                        repository,
                        args.csv,
                        actor=args.actor,
                        observed_at=args.observed_at,
                    )
                ),
                indent=2,
            )
        )
    elif args.command == "verify-discovery-leads":
        policies: dict[str, str] = {}
        for value in args.policy:
            kind, separator, url = value.partition("=")
            if not separator or not kind.strip() or not url.strip() or kind.strip() in policies:
                raise SystemExit("each --policy must use a unique KIND=URL")
            policies[kind.strip()] = url.strip()
        print(
            json.dumps(
                promote_verified_discovery_leads(
                    repository,
                    actor=args.actor,
                    policy_urls=policies,
                    policy_approved_at=args.policy_approved_at,
                    limit=args.limit,
                ),
                indent=2,
            )
        )
    elif args.command == "verify-website-leads":
        print(
            json.dumps(
                promote_verified_website_leads(
                    repository,
                    actor=args.actor,
                    limit=args.limit,
                ),
                indent=2,
            )
        )
    elif args.command == "verify-registry-career-portals":
        try:
            policies: dict[str, str] = {}
            for value in args.policy:
                kind, separator, url = value.partition("=")
                if not separator or not kind.strip() or not url.strip() or kind.strip() in policies:
                    raise ValueError("each --policy must use a unique KIND=URL")
                policies[kind.strip()] = url.strip()
            result = run_registry_career_portal_verifier(
                repository,
                actor=args.actor,
                batch_size=args.batch_size,
                concurrency=args.concurrency,
                shard_count=args.shard_count,
                shard_index=args.shard_index,
                max_batches=args.max_batches,
                pace_seconds=args.pace_seconds,
                policy_urls=policies,
                policy_approved_at=args.policy_approved_at,
                approval_concurrency=args.approval_concurrency,
                sync_interval_minutes=args.interval,
            )
        except ValueError as error:
            raise SystemExit(str(error)) from error
        print(json.dumps(result, indent=2))
    elif args.command == "import-wikidata-websites":
        print(json.dumps(cli_wikidata.run_wikidata_website_command(args, repository), indent=2))
    elif args.command == "import-sec-websites":
        if not args.user_agent.strip():
            raise SystemExit("--user-agent or SEC_USER_AGENT is required by SEC fair-access policy")
        try:
            client = SecSubmissionsWebsiteClient(
                user_agent=args.user_agent,
                requests_per_second=args.rate_per_second,
                concurrency=args.concurrency,
            )
            result = import_sec_company_websites(
                repository,
                client,
                actor=args.actor,
                limit=args.limit,
                dry_run=args.dry_run,
            )
        except ValueError as error:
            raise SystemExit(str(error)) from error
        print(json.dumps(result, indent=2))
    elif args.command == "import-sec-filing-websites":
        if not args.user_agent:
            args.user_agent = os.getenv("SEC_USER_AGENT", "")
        try:
            result = run_sec_filing_website_command(args, repository)
        except ValueError as error:
            raise SystemExit(str(error)) from error
        print(json.dumps(result, indent=2))
    elif args.command == "merge-companies":
        source = repository.find_company_by_normalized_name(args.source)
        target = repository.find_company_by_normalized_name(args.target)
        if source is None or target is None:
            raise SystemExit("source or target company not found or ambiguous")
        repository.merge_companies(
            int(source["id"]),
            int(target["id"]),
            actor=args.actor,
            reason=args.reason,
        )
        print(f"Merged {source['name']} into canonical identity {target['name']}")
    elif args.command == "discover-sources":
        if not 1 <= args.limit <= 10000:
            raise SystemExit("limit must be between 1 and 10000")
        if args.after_company_id < 0:
            raise SystemExit("after-company-id must be non-negative")
        if args.company:
            company = repository.find_company_by_normalized_name(args.company)
            if company is None:
                raise SystemExit("company not found or normalized name is ambiguous")
            targets = [company]
        else:
            statuses = set(args.coverage_status or ())
            eligible_targets = [
                company
                for company in repository.list_companies(include_synthetic=False)
                if company.get("career_url") or company.get("website_url")
                if int(company["id"]) > args.after_company_id
                and (not statuses or company["coverage_disposition"] in statuses)
            ]
            targets = sorted(eligible_targets, key=lambda company: int(company["id"]))[: args.limit]
        results = discover_company_sources(
            repository,
            targets,
            actor=args.actor,
            concurrency=args.concurrency,
        )
        print(
            json.dumps(
                {
                    "targets_with_seeds": len(results),
                    "last_company_id": int(targets[-1]["id"]) if targets else None,
                    "results": results,
                },
                indent=2,
            )
        )
    elif args.command == "discovery-priority":
        try:
            report = build_discovery_priority_report(
                repository,
                batch_size=args.batch_size,
                batch_number=args.batch_number,
            )
        except ValueError as error:
            raise SystemExit(str(error)) from error
        print(json.dumps(report, indent=2))
    elif args.command == "coverage-audit":
        try:
            write_coverage_audit(
                repository,
                output_format=args.format,
                query=args.company,
                status=args.status,
                limit=args.limit,
                offset=args.offset,
            )
        except ValueError as error:
            raise SystemExit(str(error)) from error
    elif args.command == "approve-source-candidate":
        try:
            source_id = approve_source_candidate(
                repository,
                args.candidate_id,
                terms_url=args.terms_url,
                policy_approved_at=args.policy_approved_at,
                actor=args.actor,
                sync_interval_minutes=args.interval,
            )
        except ValueError as error:
            raise SystemExit(str(error)) from error
        print(f"Approved source {source_id}; initial manifest ingested and next sync scheduled")
    elif args.command == "approve-discovered-sources":
        policies: dict[str, str] = {}
        for value in args.policy:
            kind, separator, url = value.partition("=")
            if not separator or not kind.strip() or not url.strip():
                raise SystemExit("each --policy must use KIND=URL")
            policies[kind.strip()] = url.strip()
        print(
            json.dumps(
                approve_discovered_sources(
                    repository,
                    policy_urls=policies,
                    policy_approved_at=args.policy_approved_at,
                    actor=args.actor,
                    limit=args.limit,
                    concurrency=args.concurrency,
                    sync_interval_minutes=args.interval,
                    candidate_ids=set(args.candidate_id) if args.candidate_id else None,
                ),
                indent=2,
            )
        )
    elif args.command in cli_h1b_linking.H1B_LINK_COMMANDS:
        print(json.dumps(cli_h1b_linking.run_h1b_link_command(args, repository), indent=2))
    elif args.command in cli_acquisition.ACQUISITION_COMMANDS:
        try:
            result = cli_acquisition.run_acquisition_command(args, repository)
        except ValueError as error:
            raise SystemExit(str(error)) from error
        print(json.dumps(result, indent=2))
    elif args.command in cli_search_harvest.SEARCH_HARVEST_COMMANDS:
        try:
            result = cli_search_harvest.run_search_harvest_command(args, repository)
        except ValueError as error:
            raise SystemExit(str(error)) from error
        print(json.dumps(result, indent=2))
    elif args.command in {
        "reclassify-source-fingerprints",
        "promote-verified-seed-fingerprints",
        "promote-audited-redirects",
    }:
        try:
            result = cli_fingerprints.run_fingerprint_command(args, repository)
        except ValueError as error:
            raise SystemExit(str(error)) from error
        print(json.dumps(result, indent=2))
    elif args.command == "sync-sources":
        print(
            json.dumps(
                asyncio.run(
                    sync_due_sources(repository, limit=args.limit, concurrency=args.concurrency)
                ),
                indent=2,
            )
        )
    elif args.command == "run-scheduler":
        with scheduler_lock(args.database):
            asyncio.run(
                scheduler_loop(
                    repository,
                    poll_seconds=args.poll_seconds,
                    concurrency=args.concurrency,
                    batch_size=args.batch_size,
                )
            )
    elif args.command == "source-status":
        print(json.dumps(repository.source_status(), indent=2))
    elif args.command == "reassess-sponsorship":
        print(json.dumps(reassess_all_jobs(repository), indent=2))
    elif args.command == "serve":
        import uvicorn

        from fortune_intel.api import create_app

        uvicorn.run(create_app(args.database), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
