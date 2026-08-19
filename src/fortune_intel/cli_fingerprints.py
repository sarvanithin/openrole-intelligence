"""CLI wiring for passive fingerprint maintenance."""

from __future__ import annotations

from typing import Any

from fortune_intel.services.audited_redirect_promotion import promote_audited_redirects
from fortune_intel.services.fingerprint_candidate_promotion import (
    promote_verified_seed_fingerprints,
)
from fortune_intel.services.fingerprint_reclassification import (
    reclassify_passive_fingerprints,
)


def add_fingerprint_parser(commands: Any) -> None:
    command = commands.add_parser(
        "reclassify-source-fingerprints",
        help="Normalize stored unknown ATS fingerprints with strict local parsers",
    )
    command.add_argument("--actor", required=True)
    command.add_argument("--dry-run", action="store_true")
    promote = commands.add_parser(
        "promote-verified-seed-fingerprints",
        help="Create reviewable ATS candidates from exact observations on verified company pages",
    )
    promote.add_argument("--actor", required=True)
    promote.add_argument("--limit", type=int, default=500)
    redirects = commands.add_parser(
        "promote-audited-redirects",
        help="Verify public ATS redirect audit entries and create review-only candidates",
    )
    redirects.add_argument("audit_results_jsonl")
    redirects.add_argument("--policy", action="append", required=True, metavar="KIND=URL")
    redirects.add_argument("--policy-approved-at", required=True)
    redirects.add_argument("--actor", required=True)
    redirects.add_argument("--limit", type=int, default=100)


def run_fingerprint_command(args: Any, repository: Any) -> dict[str, Any]:
    if args.command == "promote-audited-redirects":
        policies: dict[str, str] = {}
        for value in args.policy:
            kind, separator, url = value.partition("=")
            if not separator or not kind.strip() or not url.strip() or kind.strip() in policies:
                raise ValueError("each --policy must use a unique KIND=URL")
            policies[kind.strip()] = url.strip()
        return promote_audited_redirects(
            repository,
            audit_results_path=args.audit_results_jsonl,
            actor=args.actor,
            policy_urls=policies,
            policy_approved_at=args.policy_approved_at,
            limit=args.limit,
        )
    if args.command == "promote-verified-seed-fingerprints":
        return promote_verified_seed_fingerprints(repository, actor=args.actor, limit=args.limit)
    return reclassify_passive_fingerprints(
        repository,
        actor=args.actor,
        dry_run=args.dry_run,
    )
