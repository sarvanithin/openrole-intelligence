"""Deterministic career-source discovery helpers."""

from fortune_intel.discovery.ats import (
    AtsSourceCandidate,
    classify_ats_url,
    classify_official_structured_url,
    discover_ats_sources,
)
from fortune_intel.discovery.network import (
    CareerSourceDiscovery,
    DiscoveryReport,
    FetchFailure,
    FetchResponse,
    RequestsHtmlFetcher,
)
from fortune_intel.discovery.passive import (
    PassiveSourceFingerprint,
    classify_passive_ats_url,
    classify_passive_or_unknown_url,
    classify_unknown_external_career_url,
    has_career_url_marker,
)

__all__ = [
    "AtsSourceCandidate",
    "CareerSourceDiscovery",
    "DiscoveryReport",
    "FetchFailure",
    "FetchResponse",
    "PassiveSourceFingerprint",
    "RequestsHtmlFetcher",
    "classify_ats_url",
    "classify_official_structured_url",
    "classify_passive_ats_url",
    "classify_passive_or_unknown_url",
    "classify_unknown_external_career_url",
    "discover_ats_sources",
    "has_career_url_marker",
]
