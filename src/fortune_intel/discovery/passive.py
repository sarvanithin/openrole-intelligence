"""Passive fingerprints for ATS families without supported connectors."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from fortune_intel.connectors import (
    classify_adp_board_url,
    classify_avature_board_url,
    classify_dayforce_board_url,
    classify_eightfold_board_url,
    classify_icims_board_url,
    classify_jobvite_board_url,
    classify_paycom_board_url,
    classify_paycor_board_url,
    classify_paylocity_board_url,
    classify_rippling_board_url,
    classify_successfactors_board_url,
    classify_taleo_board_url,
    classify_ukg_board_url,
)


@dataclass(frozen=True, slots=True, order=True)
class PassiveSourceFingerprint:
    """An exact URL observation that is never an ingestible source candidate."""

    family: str
    observed_url: str
    host: str
    origin_page: str
    evidence: tuple[str, ...]


_STRICT_FAMILY_CLASSIFIERS = (
    ("adp", classify_adp_board_url),
    ("avature", classify_avature_board_url),
    ("dayforce", classify_dayforce_board_url),
    ("eightfold", classify_eightfold_board_url),
    ("icims", classify_icims_board_url),
    ("jobvite", classify_jobvite_board_url),
    ("paycom", classify_paycom_board_url),
    ("paycor", classify_paycor_board_url),
    ("paylocity", classify_paylocity_board_url),
    ("rippling", classify_rippling_board_url),
    ("successfactors", classify_successfactors_board_url),
    ("taleo", classify_taleo_board_url),
    ("ukg", classify_ukg_board_url),
)
_LEGACY_PASSIVE_SUFFIXES = (
    ("phenom", ("phenompeople.com",)),
    ("gr8_people", ("gr8people.com", "gr8people.eu")),
    ("directemployers", ("dejobs.org",)),
)
_NOISY_EXTERNAL_SUFFIXES = frozenset(
    {
        "bing.com",
        "facebook.com",
        "glassdoor.com",
        "google.com",
        "indeed.com",
        "instagram.com",
        "linkedin.com",
        "monster.com",
        "tiktok.com",
        "twitter.com",
        "x.com",
        "youtube.com",
        "ziprecruiter.com",
    }
)
_CAREER_URL_MARKER = re.compile(
    r"(?:^|[./_-])(?:career|careers|employment|job|jobs|join-us|openings|opportunities|recruiting|work-with-us)(?:$|[./_-])",
    re.IGNORECASE,
)
_CAREER_TERMS = (
    "career",
    "employment",
    "job",
    "join-us",
    "openings",
    "opportunities",
    "recruiting",
    "work-with-us",
)
_STATIC_RESOURCE_SUFFIXES = (
    ".css",
    ".doc",
    ".docx",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".pdf",
    ".png",
    ".svg",
    ".webp",
    ".xls",
    ".xlsx",
)


def _is_host_or_subdomain(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith(f".{suffix}")


def _safe_https_parts(url: str) -> tuple[str, str] | None:
    parsed = urlsplit(url.strip())
    try:
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").casefold().rstrip(".")
    if (
        parsed.scheme.casefold() != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return None
    return url.strip(), host


def classify_passive_ats_url(
    url: str,
    *,
    origin_page: str,
) -> PassiveSourceFingerprint | None:
    """Classify exact HTTPS URLs for known but unsupported ATS families."""

    safe = _safe_https_parts(url)
    if safe is None:
        return None
    exact_url, host = safe
    path = urlsplit(exact_url).path.casefold()
    if path.endswith(_STATIC_RESOURCE_SUFFIXES):
        return None
    if host == "cdn.phenompeople.com":
        return None
    family = None
    for candidate_family, classifier in _STRICT_FAMILY_CLASSIFIERS:
        if classifier(exact_url) is not None:
            family = candidate_family
            break
    if family is None:
        for candidate_family, suffixes in _LEGACY_PASSIVE_SUFFIXES:
            if any(_is_host_or_subdomain(host, suffix) for suffix in suffixes):
                family = candidate_family
                break
    if family is None:
        return None
    return PassiveSourceFingerprint(
        family=family,
        observed_url=exact_url,
        host=host,
        origin_page=origin_page,
        evidence=(
            f"exact outbound URL matches the allow-listed {family} fingerprint",
            "passive inventory only; no connector support or scheduling authorization",
        ),
    )


def classify_unknown_external_career_url(
    url: str,
    *,
    origin_page: str,
) -> PassiveSourceFingerprint | None:
    """Retain a bounded unknown external link only when its URL is clearly career-like."""

    safe = _safe_https_parts(url)
    if safe is None:
        return None
    exact_url, host = safe
    if any(_is_host_or_subdomain(host, suffix) for suffix in _NOISY_EXTERNAL_SUFFIXES):
        return None
    parsed = urlsplit(exact_url)
    marker_text = f"{host}{parsed.path}"
    if _CAREER_URL_MARKER.search(marker_text) is None:
        return None
    return PassiveSourceFingerprint(
        family="unknown_external",
        observed_url=exact_url,
        host=host,
        origin_page=origin_page,
        evidence=(
            "exact outbound HTTPS URL has a bounded career marker in its host or path",
            "unknown external family; target was recorded but not fetched",
        ),
    )


def classify_passive_or_unknown_url(
    url: str,
    *,
    origin_page: str,
) -> PassiveSourceFingerprint | None:
    """Classify a validated outbound URL without fetching its target."""

    return classify_passive_ats_url(
        url, origin_page=origin_page
    ) or classify_unknown_external_career_url(url, origin_page=origin_page)


def has_career_url_marker(url: str) -> bool:
    """Return whether a URL itself, rather than link text, indicates careers."""

    parsed = urlsplit(url)
    value = f"{parsed.hostname or ''}{parsed.path}?{parsed.query}".casefold()
    return any(term in value for term in _CAREER_TERMS)
