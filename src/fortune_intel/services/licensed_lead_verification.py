"""Turn licensed ATS leads into reviewable candidates only after an origin check.

Third-party datasets are useful for coverage, but they never authorize a source by
themselves.  This module rechecks the actual public ATS board and requires an
exact normalized company identity in the returned HTML before it can create a
candidate.  Activation remains a separate complete-manifest operation.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from fortune_intel.discovery.ats import classify_ats_url
from fortune_intel.storage import JobRepository

_CORPORATE_SUFFIXES = re.compile(
    r"\b(incorporated|inc|corp|corporation|company|co|limited|ltd|llc|plc|lp|holdings|group)\b"
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_SPACE = re.compile(r"\s+")
_MAX_RESPONSE_BYTES = 512 * 1024
_VERIFIABLE_LEAD_METHODS = frozenset(
    {"third_party_discovery_lead", "user_supplied_career_url_registry"}
)


@dataclass(frozen=True, slots=True)
class LeadPage:
    """Bounded response used to verify a public candidate board."""

    status: int
    final_url: str
    content_type: str
    body: bytes


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() == "title":
            self._in_title = True
        for key, value in attrs:
            if key.casefold() in {"content", "aria-label"} and value:
                self.parts.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        self.parts.append(data)


def _normal_company_name(value: str) -> str:
    lowered = _NON_ALNUM.sub(" ", value.casefold().replace("&", " and "))
    return _SPACE.sub(" ", _CORPORATE_SUFFIXES.sub(" ", lowered)).strip()


def _page_text(body: bytes) -> tuple[str, str]:
    parser = _TextExtractor()
    parser.feed(body.decode("utf-8", errors="replace"))
    parser.close()
    return _SPACE.sub(" ", parser.title).strip(), _SPACE.sub(" ", " ".join(parser.parts)).strip()


def fetch_lead_page(url: str) -> LeadPage:
    """Fetch one HTTPS page without following a potentially unsafe redirect."""

    request = Request(
        url, headers={"User-Agent": "OpenRoleIntelligence/1.0 (+https://openrole.example)"}
    )
    try:
        with build_opener(_NoRedirect()).open(request, timeout=20) as response:
            return LeadPage(
                status=int(response.status),
                final_url=str(response.url),
                content_type=str(response.headers.get("Content-Type", "")),
                body=response.read(_MAX_RESPONSE_BYTES + 1),
            )
    except (HTTPError, URLError, TimeoutError, ValueError) as error:
        return LeadPage(0, url, "", f"{type(error).__name__}: {error}".encode())


def _identity_surface(company_name: str, page: LeadPage) -> tuple[str, str] | None:
    target = _normal_company_name(company_name)
    # Very short legal names are too easy to match incidentally.
    if len(target) < 5 or page.status != 200 or len(page.body) > _MAX_RESPONSE_BYTES:
        return None
    if "html" not in page.content_type.casefold():
        return None
    title, text = _page_text(page.body)
    for surface, value in (("title", title), ("page_text", text)):
        normalized = _normal_company_name(value)
        if target and re.search(rf"(?<![a-z0-9]){re.escape(target)}(?![a-z0-9])", normalized):
            return surface, title[:240]
    return None


def _record_lead_outcome(
    repository: JobRepository,
    *,
    company_id: int,
    observed_url: str,
    evidence: dict[str, object],
    status: str,
    details: dict[str, object],
) -> None:
    """Make each lead decision durable so the scheduler never hammers failures."""

    updated = dict(evidence)
    updated["verification_status"] = status
    updated["verification_attempt"] = details
    with repository.connect() as connection:
        connection.execute(
            """UPDATE career_source_fingerprints SET evidence_json = ?
            WHERE company_id = ? AND observed_url = ?""",
            (json.dumps(updated, sort_keys=True, separators=(",", ":")), company_id, observed_url),
        )


def promote_verified_discovery_leads(
    repository: JobRepository,
    *,
    actor: str,
    policy_urls: dict[str, str],
    policy_approved_at: str,
    limit: int = 100,
    page_fetcher: Callable[[str], LeadPage] = fetch_lead_page,
) -> dict[str, int]:
    """Verify passive licensed leads and create auditable, non-live candidates.

    A resulting candidate is still merely *discovered*.  The normal approval path
    probes every page of its job manifest before it can create a job source.
    """

    if not actor.strip():
        raise ValueError("actor is required")
    if not policy_approved_at.strip():
        raise ValueError("policy_approved_at is required")
    if not 1 <= limit <= 10_000:
        raise ValueError("limit must be between 1 and 10000")
    with repository.connect() as connection:
        rows = connection.execute(
            """SELECT f.observed_url, f.evidence_json, c.id company_id, c.name company_name
            FROM career_source_fingerprints f JOIN companies c ON c.id = f.company_id
            WHERE f.evidence_json LIKE '%"verification_status":"unverified"%'
              AND (
                  f.evidence_json LIKE '%"review_method":"third_party_discovery_lead"%'
                  OR f.evidence_json LIKE '%"review_method":"user_supplied_career_url_registry"%'
              )
              AND (
                  f.observed_url LIKE '%ashbyhq.com%'
                  OR f.observed_url LIKE '%greenhouse.io%'
                  OR f.observed_url LIKE '%lever.co%'
                  OR f.observed_url LIKE '%oraclecloud.com%'
                  OR f.observed_url LIKE '%smartrecruiters.com%'
                  OR f.observed_url LIKE '%myworkdayjobs.com%'
                  OR f.observed_url LIKE '%myworkdaysite.com%'
              )
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
            lead.get("review_method") not in _VERIFIABLE_LEAD_METHODS
            or lead.get("verification_status") != "unverified"
            or lead.get("activation_allowed") is not False
        ):
            report["skipped"] += 1
            continue
        found = classify_ats_url(str(row["observed_url"]), origin="licensed-lead origin check")
        if found is None or found.connector_kind not in policy_urls:
            _record_lead_outcome(
                repository,
                company_id=int(row["company_id"]),
                observed_url=str(row["observed_url"]),
                evidence=lead,
                status="unsupported",
                details={"reason": "connector_not_policy_enabled"},
            )
            report["skipped"] += 1
            continue
        page = page_fetcher(found.normalized_base_url)
        if page.final_url != found.normalized_base_url:
            _record_lead_outcome(
                repository,
                company_id=int(row["company_id"]),
                observed_url=str(row["observed_url"]),
                evidence=lead,
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
            _record_lead_outcome(
                repository,
                company_id=int(row["company_id"]),
                observed_url=str(row["observed_url"]),
                evidence=lead,
                status="rejected",
                details={"reason": "exact_company_identity_not_present", "status": page.status},
            )
            report["rejected"] += 1
            continue
        surface, title = identity
        repository.upsert_source_candidate(
            int(row["company_id"]),
            candidate_url=found.normalized_base_url,
            kind=found.connector_kind,
            confidence=0.98,
            evidence={
                "review_method": "primary_source_exact_ats_url",
                "verification_status": "verified",
                "source_url": found.normalized_base_url,
                "origin_lead": lead,
                "identity_check": {
                    "method": "direct_ats_html_exact_normalized_company_name",
                    "surface": surface,
                    "title": title,
                    "status": page.status,
                    "content_type": page.content_type,
                    "body_sha256": hashlib.sha256(page.body).hexdigest(),
                },
                "board_token": found.board_token,
            },
            terms_url=policy_urls[found.connector_kind],
            terms_status="permitted",
            terms_reviewed_at=policy_approved_at,
            discovered_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        )
        coverage = repository.get_company_coverage(int(row["company_id"]))
        if coverage is not None and str(coverage["disposition"]) != "supported":
            repository.set_company_disposition(
                int(row["company_id"]),
                "candidate",
                reason="Licensed ATS lead verified by direct exact company identity on public board",
                actor=actor,
            )
        _record_lead_outcome(
            repository,
            company_id=int(row["company_id"]),
            observed_url=str(row["observed_url"]),
            evidence=lead,
            status="verified",
            details={"candidate_url": found.normalized_base_url, "identity_surface": surface},
        )
        report["verified"] += 1
    return report
