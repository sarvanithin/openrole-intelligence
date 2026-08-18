"""Typed domain objects shared by ingestion, storage, and the API."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SPONSORSHIP_RULE_VERSION = "rules-1.2"


class SponsorshipTier(StrEnum):
    """Evidence tier; deliberately not a probability of sponsorship."""

    EXPLICIT_YES = "A"
    STRONG_HISTORY = "B"
    EMPLOYER_HISTORY = "C"
    INSUFFICIENT = "D"
    EXPLICIT_NO = "E"


class JobStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


@dataclass(frozen=True)
class SponsorshipAssessment:
    tier: SponsorshipTier
    evidence_score: int
    reasons: tuple[str, ...]
    policy_excerpt: str | None = None
    rule_version: str = SPONSORSHIP_RULE_VERSION


@dataclass(frozen=True)
class JobRecord:
    company_name: str
    title: str
    url: str
    source: str
    external_job_id: str
    location: str = ""
    description: str = ""
    # Opening/publish timestamp supplied by the source ATS. This is never the
    # time our crawler first discovered the job.
    source_opened_at: str | None = None
    source_updated_at: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.company_name.strip() or not self.title.strip():
            raise ValueError("company_name and title are required")
        parsed = urlsplit(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("url must be an absolute HTTP(S) URL")
        if not self.source.strip() or not self.external_job_id.strip():
            raise ValueError("source and external_job_id are required")


_TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "source",
    "src",
    "trk",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}


def canonicalize_url(url: str) -> str:
    """Remove fragments and common tracking parameters without changing identity."""

    parsed = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAMETERS
    ]
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, urlencode(query), ""))
