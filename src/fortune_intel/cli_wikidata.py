"""CLI integration for resumable exact-CIK Wikidata URL acquisition."""

from __future__ import annotations

import os
from argparse import _SubParsersAction

from fortune_intel.importers.wikidata_websites import (
    WikidataWebsiteClient,
    import_wikidata_company_websites,
)
from fortune_intel.storage import JobRepository


def add_wikidata_website_parser(commands: _SubParsersAction) -> None:
    command = commands.add_parser(
        "import-wikidata-websites",
        help="Acquire official career/company URLs by exact SEC CIK from Wikidata",
    )
    command.add_argument("--actor", required=True)
    command.add_argument(
        "--user-agent",
        default=os.getenv("WIKIMEDIA_USER_AGENT", ""),
        help="Contactable Wikimedia User-Agent (or set WIKIMEDIA_USER_AGENT)",
    )
    command.add_argument("--batch-size", type=int, default=100)
    command.add_argument("--limit", type=int)
    command.add_argument(
        "--after-company-id",
        type=int,
        default=0,
        help="Resume after this company ID; ordering is deterministic and the cursor is exclusive",
    )
    command.add_argument("--dry-run", action="store_true")


def run_wikidata_website_command(args: object, repository: JobRepository) -> dict[str, object]:
    user_agent = str(args.user_agent)
    if not user_agent.strip():
        raise SystemExit(
            "--user-agent or WIKIMEDIA_USER_AGENT is required by Wikimedia's bot policy"
        )
    try:
        client = WikidataWebsiteClient(
            user_agent=user_agent,
            batch_size=int(args.batch_size),
        )
        return import_wikidata_company_websites(
            repository,
            client,
            actor=str(args.actor),
            limit=args.limit,
            dry_run=bool(args.dry_run),
            after_company_id=int(args.after_company_id),
            missing_websites_only=True,
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error
