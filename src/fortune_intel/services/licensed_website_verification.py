"""Promote licensed website leads only after first-party identity confirmation.

This is intentionally stricter than a title or body-text search.  The target
site must return HTML directly (no followed redirects) and declare the exact
normalized company name in an Organization/Corporation JSON-LD object or in a
site-name metadata field.  The result is only a verified *website seed*; it
never creates or activates a career source.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from fortune_intel.storage import JobRepository
from fortune_intel.storage.coverage_ops import normalize_public_url

_CORPORATE_SUFFIXES = re.compile(
    r"\b(incorporated|inc|corp|corporation|company|co|limited|ltd|llc|plc|lp|holdings|group)\b"
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_SPACE = re.compile(r"\s+")
_MAX_RESPONSE_BYTES = 512 * 1024
_ORGANIZATION_TYPES = frozenset(
    {"organization", "corporation", "company", "governmentorganization"}
)
_SITE_NAME_META = frozenset({"application-name", "og:site_name"})
_THIRD_PARTY_PROFILE_HOSTS = frozenset(
    {"linkedin.com", "outscal.com", "wellfound.com", "wikipedia.org", "crunchbase.com"}
)


@dataclass(frozen=True, slots=True)
class WebsitePage:
    status: int
    final_url: str
    content_type: str
    body: bytes


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class _IdentityParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, list[str]] = {}
        self.json_ld: list[str] = []
        self._json_ld_depth = 0
        self._current_json_ld: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if tag.casefold() == "meta":
            key = (attributes.get("name") or attributes.get("property") or "").casefold()
            value = attributes.get("content", "").strip()
            if key in _SITE_NAME_META and value:
                self.meta.setdefault(key, []).append(value)
        if (
            tag.casefold() == "script"
            and attributes.get("type", "").casefold().split(";", 1)[0] == "application/ld+json"
        ):
            self._json_ld_depth = 1
            self._current_json_ld = []

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._json_ld_depth:
            self.json_ld.append("".join(self._current_json_ld))
            self._json_ld_depth = 0
            self._current_json_ld = []

    def handle_data(self, data: str) -> None:
        if self._json_ld_depth:
            self._current_json_ld.append(data)


def _normal_company_name(value: str) -> str:
    lowered = _NON_ALNUM.sub(" ", value.casefold().replace("&", " and "))
    return _SPACE.sub(" ", _CORPORATE_SUFFIXES.sub(" ", lowered)).strip()


def _is_third_party_profile_host(url: str) -> bool:
    host = (urlsplit(url).hostname or "").casefold().removeprefix("www.")
    return any(
        host == blocked or host.endswith(f".{blocked}") for blocked in _THIRD_PARTY_PROFILE_HOSTS
    )


def fetch_website_page(url: str) -> WebsitePage:
    """Fetch one public HTTPS page without following redirects."""

    request = Request(
        url, headers={"User-Agent": "OpenRoleIntelligence/1.0 (+https://openrole.example)"}
    )
    try:
        with build_opener(_NoRedirect()).open(request, timeout=20) as response:
            return WebsitePage(
                status=int(response.status),
                final_url=str(response.url),
                content_type=str(response.headers.get("Content-Type", "")),
                body=response.read(_MAX_RESPONSE_BYTES + 1),
            )
    except (HTTPError, URLError, TimeoutError, ValueError) as error:
        return WebsitePage(0, url, "", f"{type(error).__name__}: {error}".encode())


def _objects(value: object) -> Iterator[dict[str, object]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child)


def _has_organization_type(value: object) -> bool:
    types = value if isinstance(value, list) else [value]
    return any(isinstance(item, str) and item.casefold() in _ORGANIZATION_TYPES for item in types)


def _identity_declaration(company_name: str, page: WebsitePage) -> tuple[str, str] | None:
    target = _normal_company_name(company_name)
    if len(target) < 5 or page.status != 200 or len(page.body) > _MAX_RESPONSE_BYTES:
        return None
    if "html" not in page.content_type.casefold():
        return None
    parser = _IdentityParser()
    parser.feed(page.body.decode("utf-8", errors="replace"))
    parser.close()
    for key, values in parser.meta.items():
        for value in values:
            if _normal_company_name(value) == target:
                return (f"meta:{key}", value[:240])
    for raw in parser.json_ld:
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for item in _objects(decoded):
            if not _has_organization_type(item.get("@type")):
                continue
            for field in ("legalName", "name", "alternateName"):
                value = item.get(field)
                values = value if isinstance(value, list) else [value]
                for candidate in values:
                    if isinstance(candidate, str) and _normal_company_name(candidate) == target:
                        return (f"json_ld:{field}", candidate[:240])
    return None


def _record_website_lead_outcome(
    repository: JobRepository,
    *,
    company_id: int,
    observed_url: str,
    evidence: dict[str, object],
    status: str,
    details: dict[str, object],
) -> None:
    updated = dict(evidence)
    updated["verification_status"] = status
    updated["website_seed_verification_attempt"] = details
    with repository.connect() as connection:
        connection.execute(
            """UPDATE career_source_fingerprints SET evidence_json = ?
            WHERE company_id = ? AND observed_url = ? AND family = 'unknown_external'""",
            (json.dumps(updated, sort_keys=True, separators=(",", ":")), company_id, observed_url),
        )


def promote_verified_website_leads(
    repository: JobRepository,
    *,
    actor: str,
    limit: int = 100,
    page_fetcher: Callable[[str], WebsitePage] = fetch_website_page,
) -> dict[str, int]:
    """Promote exact, directly self-declared company websites and nothing else."""

    if not actor.strip():
        raise ValueError("actor is required")
    if not 1 <= limit <= 10_000:
        raise ValueError("limit must be between 1 and 10000")
    with repository.connect() as connection:
        rows = connection.execute(
            """SELECT f.observed_url, f.evidence_json, c.id company_id, c.name company_name
            FROM career_source_fingerprints f JOIN companies c ON c.id = f.company_id
            WHERE (c.website_url IS NULL OR c.website_url = '')
              AND f.family = 'unknown_external'
              AND f.evidence_json LIKE '%\"review_method\":\"licensed_company_website_lead\"%'
              AND f.evidence_json LIKE '%\"verification_status\":\"unverified\"%'
            ORDER BY f.id LIMIT ?""",
            (limit,),
        ).fetchall()
    report = {"scanned": 0, "verified": 0, "rejected": 0, "skipped": 0}
    for row in rows:
        report["scanned"] += 1
        try:
            lead = json.loads(str(row["evidence_json"]))
        except json.JSONDecodeError:
            report["skipped"] += 1
            continue
        if (
            lead.get("review_method") != "licensed_company_website_lead"
            or lead.get("verification_status") != "unverified"
            or lead.get("website_seed_promotion_allowed") is not False
            or lead.get("primary_site_identity_confirmation_required") is not True
        ):
            report["skipped"] += 1
            continue
        observed_url = str(row["observed_url"])
        if _is_third_party_profile_host(observed_url):
            _record_website_lead_outcome(
                repository,
                company_id=int(row["company_id"]),
                observed_url=observed_url,
                evidence=lead,
                status="rejected",
                details={"reason": "third_party_profile_host"},
            )
            report["rejected"] += 1
            continue
        page = page_fetcher(observed_url)
        try:
            final_url = normalize_public_url(page.final_url, field="final_url")
        except ValueError:
            final_url = ""
        if final_url != observed_url:
            _record_website_lead_outcome(
                repository,
                company_id=int(row["company_id"]),
                observed_url=observed_url,
                evidence=lead,
                status="rejected",
                details={
                    "reason": "redirect_or_canonical_url_changed",
                    "final_url": page.final_url,
                },
            )
            report["rejected"] += 1
            continue
        identity = _identity_declaration(str(row["company_name"]), page)
        if identity is None:
            _record_website_lead_outcome(
                repository,
                company_id=int(row["company_id"]),
                observed_url=observed_url,
                evidence=lead,
                status="rejected",
                details={
                    "reason": "first_party_exact_organization_identity_not_present",
                    "status": page.status,
                },
            )
            report["rejected"] += 1
            continue
        declaration, value = identity
        verified_at = datetime.now(UTC).replace(microsecond=0).isoformat()
        # Do not race another authoritative seed writer or replace a reviewed
        # website.  This lead may only fill a still-empty value.
        with repository.connect() as connection:
            written = connection.execute(
                """UPDATE companies SET website_url = ?, updated_at = ?
                WHERE id = ? AND (website_url IS NULL OR website_url = '')""",
                (observed_url, verified_at, int(row["company_id"])),
            ).rowcount
        if written != 1:
            report["skipped"] += 1
            continue
        coverage = repository.get_company_coverage(int(row["company_id"]))
        if coverage is not None:
            repository.set_company_disposition(
                int(row["company_id"]),
                str(coverage["disposition"]),
                reason=(
                    "Canonical website seed verified by direct first-party organization declaration "
                    f"at {observed_url}"
                ),
                actor=actor,
                reviewed_at=verified_at,
            )
        _record_website_lead_outcome(
            repository,
            company_id=int(row["company_id"]),
            observed_url=observed_url,
            evidence=lead,
            status="verified",
            details={
                "website_url": observed_url,
                "identity_declaration": declaration,
                "identity_value": value,
                "status": page.status,
                "content_type": page.content_type,
                "body_sha256": hashlib.sha256(page.body).hexdigest(),
            },
        )
        report["verified"] += 1
    return report
