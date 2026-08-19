"""Verify passive company career pages before using them as discovery seeds.

The career-URL registry is useful inventory, not source authority.  This
module gives non-ATS entries one deliberately narrow automated route forward:
the supplied HTTPS page must resolve only to public IPs, return directly (no
redirect is followed), and visibly identify the exact company.  A successful
check writes a *career seed* only.  Discovery and complete-manifest approval
remain the only route to an enabled job source.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

from fortune_intel.services.licensed_lead_verification import (
    LeadPage,
    _identity_surface,
    fetch_lead_page,
)
from fortune_intel.storage import JobRepository


class RegistryCareerVerificationReport(dict[str, int]):
    """Counter report plus the exact newly verified company IDs for handoff."""

    def __init__(self, counts: dict[str, int], verified_company_ids: list[int]) -> None:
        super().__init__(counts)
        self.verified_company_ids = tuple(dict.fromkeys(verified_company_ids))


@dataclass(frozen=True, slots=True)
class _FetchResult:
    safe_url: str | None
    page: LeadPage | None
    error: str = ""


def _default_resolver(host: str) -> Iterable[str]:
    return {entry[4][0] for entry in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}


def _public_https_url(url: str, *, resolver: Callable[[str], Iterable[str]]) -> str | None:
    """Return one canonical public HTTPS URL, or reject it before fetching."""

    parsed = urlsplit(url.strip())
    try:
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    host = parsed.hostname.casefold().rstrip(".")
    # A hostname gives the resolver a chance to reject every non-public address.
    # Literal IP addresses are never necessary for an employer career site.
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return None
    try:
        addresses = tuple(resolver(host))
    except (OSError, ValueError):
        return None
    if not addresses:
        return None
    try:
        if any(not ipaddress.ip_address(address).is_global for address in addresses):
            return None
    except ValueError:
        return None
    return urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))


def _record_outcome(
    repository: JobRepository,
    *,
    fingerprint_id: int,
    evidence: dict[str, object],
    status: str,
    details: dict[str, object],
) -> None:
    updated = dict(evidence)
    updated["verification_status"] = status
    # Keep this common key so registry refreshes preserve terminal decisions.
    updated["verification_attempt"] = details
    with repository.connect() as connection:
        connection.execute(
            "UPDATE career_source_fingerprints SET evidence_json = ? WHERE id = ?",
            (json.dumps(updated, sort_keys=True, separators=(",", ":")), fingerprint_id),
        )


def _persist_verified_career_seed(
    repository: JobRepository,
    *,
    company_id: int,
    career_url: str,
    actor: str,
    verified_at: str,
) -> bool:
    """Fill only an empty career field and create provenance usable by discovery."""

    with repository.connect() as connection:
        written = connection.execute(
            """UPDATE companies SET career_url = ?, updated_at = ?
            WHERE id = ? AND (career_url IS NULL OR career_url = '')""",
            (career_url, verified_at, company_id),
        ).rowcount
        current = connection.execute(
            "SELECT career_url FROM companies WHERE id = ?", (company_id,)
        ).fetchone()
    if written != 1:
        return bool(current is not None and str(current["career_url"] or "") == career_url)
    coverage = repository.get_company_coverage(company_id)
    if coverage is not None:
        repository.set_company_disposition(
            company_id,
            str(coverage["disposition"]),
            reason=(
                "Canonical career seed verified by direct public career-page exact company "
                f"identity at {career_url}"
            ),
            actor=actor,
            reviewed_at=verified_at,
        )
    return True


def _safe_fetch(
    observed_url: str,
    *,
    resolver: Callable[[str], Iterable[str]],
    page_fetcher: Callable[[str], LeadPage],
) -> _FetchResult:
    """Validate an endpoint and fetch it once; suitable for bounded workers."""

    safe_url = _public_https_url(observed_url, resolver=resolver)
    if safe_url is None:
        return _FetchResult(None, None, "not_a_public_https_endpoint")
    try:
        return _FetchResult(safe_url, page_fetcher(safe_url))
    except (ConnectionError, OSError, RuntimeError, TimeoutError, ValueError) as error:
        return _FetchResult(safe_url, None, f"fetch_failed:{type(error).__name__}")


def promote_verified_registry_career_portals(
    repository: JobRepository,
    *,
    actor: str,
    limit: int = 100,
    concurrency: int = 8,
    shard_count: int = 1,
    shard_index: int = 0,
    resolver: Callable[[str], Iterable[str]] = _default_resolver,
    page_fetcher: Callable[[str], LeadPage] = fetch_lead_page,
) -> RegistryCareerVerificationReport:
    """Turn direct, exact-identity registry career pages into discovery seeds.

    This considers custom/unrecognized registry URLs, registry rows marked
    ``unknown_external``, and older rows that predate the ``proposed_kind``
    marker. Supported ATS entries with a known marker use the more restrictive
    direct-board verifier instead. No source candidate is created here and no
    source can be activated from this result.
    """

    if not actor.strip():
        raise ValueError("actor is required")
    if not 1 <= limit <= 10_000:
        raise ValueError("limit must be between 1 and 10000")
    if not 1 <= concurrency <= 8:
        raise ValueError("concurrency must be between 1 and 8")
    if not 1 <= shard_count <= 32:
        raise ValueError("shard_count must be between 1 and 32")
    if not 0 <= shard_index < shard_count:
        raise ValueError("shard_index must be between 0 and shard_count - 1")
    with repository.connect() as connection:
        rows = connection.execute(
            """SELECT f.id fingerprint_id, f.observed_url, f.evidence_json,
                      c.id company_id, c.name company_name
            FROM career_source_fingerprints f JOIN companies c ON c.id = f.company_id
            WHERE f.evidence_json LIKE '%"review_method":"user_supplied_career_url_registry"%'
              AND f.evidence_json LIKE '%"verification_status":"unverified"%'
              AND (
                  json_extract(f.evidence_json, '$.proposed_kind') IN (
                      'custom_or_unrecognized', 'unknown_external'
                  )
                  OR json_type(f.evidence_json, '$.proposed_kind') IS NULL
              )
              AND (f.id % ?) = ?
            ORDER BY f.id LIMIT ?""",
            (shard_count, shard_index, limit),
        ).fetchall()
    report = {"scanned": 0, "verified": 0, "rejected": 0, "skipped": 0}
    verified_company_ids: list[int] = []
    pending: list[tuple[object, dict[str, object]]] = []
    for row in rows:
        report["scanned"] += 1
        try:
            evidence = json.loads(str(row["evidence_json"]))
        except json.JSONDecodeError:
            report["skipped"] += 1
            continue
        if (
            evidence.get("review_method") != "user_supplied_career_url_registry"
            or evidence.get("verification_status") != "unverified"
            or evidence.get("activation_allowed") is not False
        ):
            report["skipped"] += 1
            continue
        pending.append((row, evidence))

    # Network work is capped, while the decision and every SQLite write below
    # stays in database order.  This removes serial timeout stalls without
    # making candidate/seed provenance nondeterministic.
    def fetch(row: object) -> _FetchResult:
        return _safe_fetch(
            str(row["observed_url"]),  # type: ignore[index]
            resolver=resolver,
            page_fetcher=page_fetcher,
        )

    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="registry-career") as pool:
        results = list(pool.map(lambda item: fetch(item[0]), pending))
    for (row, evidence), fetched in zip(pending, results, strict=True):
        if fetched.safe_url is None:
            _record_outcome(
                repository,
                fingerprint_id=int(row["fingerprint_id"]),
                evidence=evidence,
                status="rejected",
                details={"reason": fetched.error},
            )
            report["rejected"] += 1
            continue
        safe_url = fetched.safe_url
        page = fetched.page
        if page is None:
            _record_outcome(
                repository,
                fingerprint_id=int(row["fingerprint_id"]),
                evidence=evidence,
                status="rejected",
                details={"reason": fetched.error, "career_url": safe_url},
            )
            report["rejected"] += 1
            continue
        # Fetchers are required to not follow redirects.  If a custom fetcher
        # reports any canonical change, treat it as unsafe rather than guessing.
        if page.final_url != safe_url:
            _record_outcome(
                repository,
                fingerprint_id=int(row["fingerprint_id"]),
                evidence=evidence,
                status="rejected",
                details={
                    "reason": "redirect_or_canonical_url_changed",
                    "final_url": page.final_url,
                },
            )
            report["rejected"] += 1
            continue
        identity = _identity_surface(str(row["company_name"]), page)
        if identity is None:
            _record_outcome(
                repository,
                fingerprint_id=int(row["fingerprint_id"]),
                evidence=evidence,
                status="rejected",
                details={"reason": "exact_company_identity_not_present", "status": page.status},
            )
            report["rejected"] += 1
            continue
        surface, title = identity
        verified_at = datetime.now(UTC).replace(microsecond=0).isoformat()
        persisted = _persist_verified_career_seed(
            repository,
            company_id=int(row["company_id"]),
            career_url=safe_url,
            actor=actor,
            verified_at=verified_at,
        )
        _record_outcome(
            repository,
            fingerprint_id=int(row["fingerprint_id"]),
            evidence=evidence,
            status="verified" if persisted else "rejected",
            details={
                "career_url": safe_url,
                "seed_persisted": persisted,
                "identity_check": {
                    "method": "direct_career_html_exact_normalized_company_name",
                    "surface": surface,
                    "title": title,
                    "status": page.status,
                    "content_type": page.content_type,
                },
            },
        )
        report["verified" if persisted else "rejected"] += 1
        if persisted:
            verified_company_ids.append(int(row["company_id"]))
    return RegistryCareerVerificationReport(report, verified_company_ids)
