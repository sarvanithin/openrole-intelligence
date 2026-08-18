"""High-precision declarative website extraction for SEC filing HTML."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup

from fortune_intel.storage.coverage_ops import normalize_public_url

_URL_TOKEN = r"(?P<url>(?:https?://|www\.)[A-Za-z0-9][^\s<>\"'()\[\]{},;]{2,})"
_OWNER = r"(?:our|the\s+company(?:'s|’s)?|the\s+registrant(?:'s|’s)?|the\s+issuer(?:'s|’s)?)"
_SITE = r"(?:(?:corporate|internet)\s+)?(?:web\s*site|website|internet\s+address)"
_RELATION = (
    r"(?:address\s+)?(?:is|at|is\s+located\s+at|can\s+be\s+found\s+at|"
    r"is\s+available\s+at|:|,)"
)
_DECLARATION_RE = re.compile(rf"\b{_OWNER}\s+{_SITE}\s*{_RELATION}\s*{_URL_TOKEN}", re.I)
_MAINTAIN_RE = re.compile(
    rf"\b(?:we|the\s+company)\s+(?:maintain|maintains|has)\s+(?:an?\s+)?"
    rf"{_SITE}\s+at\s+{_URL_TOKEN}",
    re.I,
)
_BLOCKED_HOST_SUFFIXES = (
    "sec.gov",
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "glassdoor.com",
    "indeed.com",
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "myworkdayjobs.com",
    "workday.com",
    "smartrecruiters.com",
    "icims.com",
    "oraclecloud.com",
    "cloudfront.net",
    "akamaized.net",
    "cdn-website.com",
    "gcs-web.com",
    "investorroom.com",
    "q4inc.com",
)
_IR_SEGMENTS = {"investor", "investors", "investor-relations", "investorrelations", "ir"}


@dataclass(frozen=True, slots=True)
class SecFilingWebsiteEvidence:
    website_url: str
    evidence_text: str


def _candidate_url(token: str) -> str:
    value = token.strip().rstrip(".?!:)")
    if any(ord(character) < 32 for character in value) or "\\" in value:
        raise ValueError("SEC filing website contains unsafe characters")
    if value.casefold().startswith("www."):
        value = f"https://{value}"
    parsed = urlsplit(value)
    if parsed.scheme.casefold() == "http":
        value = urlunsplit(parsed._replace(scheme="https"))
        parsed = urlsplit(value)
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("SEC filing website must be a simple public web origin")
    if parsed.query or parsed.fragment:
        raise ValueError("SEC filing website cannot contain a query or fragment")
    normalized = normalize_public_url(value, field="SEC filing website")
    parsed = urlsplit(normalized)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ValueError("SEC filing website must normalize to public HTTPS")
    if parsed.port not in (None, 443) or parsed.query or parsed.fragment:
        raise ValueError("SEC filing website must be an unambiguous HTTPS URL")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("SEC filing website cannot be an IP literal")
    if any(host == suffix or host.endswith(f".{suffix}") for suffix in _BLOCKED_HOST_SUFFIXES):
        raise ValueError("SEC filing website is a third-party host")
    path_segments = {part.casefold() for part in parsed.path.split("/") if part}
    host_labels = set(host.removeprefix("www.").split("."))
    if host_labels & {"ir", "investor", "investors", "ri"}:
        raise ValueError("SEC filing website is investor-relations-only")
    if host_labels & {"assets", "cdn", "file", "files", "static"}:
        raise ValueError("SEC filing website is a storage-only host")
    if parsed.path in {"", "/"}:
        return normalized
    if path_segments & _IR_SEGMENTS:
        raise ValueError("SEC filing website is investor-relations-only")
    return urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))


def _evidence_excerpt(text: str, start: int, end: int) -> str:
    excerpt = text[max(0, start - 80) : min(len(text), end + 120)]
    return re.sub(r"\s+", " ", excerpt).strip()[:500]


def _record_matches(
    text: str,
    pattern: re.Pattern[str],
    found: dict[str, SecFilingWebsiteEvidence],
) -> None:
    for match in pattern.finditer(text):
        preceding = text[max(0, match.start() - 45) : match.start()].casefold()
        if "investor relations" in preceding or "investor website" in preceding:
            continue
        try:
            website = _candidate_url(match.group("url"))
        except ValueError:
            continue
        found.setdefault(
            website,
            SecFilingWebsiteEvidence(website, _evidence_excerpt(text, match.start(), match.end())),
        )


def extract_declared_company_websites(
    html: bytes, *, company_name: str
) -> tuple[SecFilingWebsiteEvidence, ...]:
    """Return only URLs immediately governed by first-party declarations."""
    if not company_name.strip():
        raise ValueError("company_name is required")
    try:
        markup = html.decode("utf-8")
    except UnicodeDecodeError:
        markup = html.decode("latin-1")
    soup = BeautifulSoup(markup, "html.parser")
    hidden = soup.select(
        "script,style,noscript,template,[hidden],[aria-hidden='true'],"
        "[style*='display:none'],[style*='display: none']"
    )
    for element in hidden:
        element.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    found: dict[str, SecFilingWebsiteEvidence] = {}
    _record_matches(text, _DECLARATION_RE, found)
    _record_matches(text, _MAINTAIN_RE, found)
    legal_name = r"\s+".join(re.escape(part) for part in company_name.strip().split())
    named = re.compile(rf"\b{legal_name}(?:'s|’s)?\s+{_SITE}\s*{_RELATION}\s*{_URL_TOKEN}", re.I)
    _record_matches(text, named, found)
    return tuple(sorted(found.values(), key=lambda item: item.website_url))
