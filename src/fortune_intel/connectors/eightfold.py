"""Fail-closed parsing for exact, policy-held Eightfold career-board URLs.

Eightfold's documented Position APIs require tenant credentials and explicit
permissions. This module therefore inventories exact public board observations;
it does not derive API URLs, fetch undocumented endpoints, or expose a scheduler
connector.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

_HOST_SUFFIX = ".eightfold.ai"
_TENANT_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_DOMAIN = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$", re.IGNORECASE)
_RESERVED_TENANTS = frozenset({"api", "apidocs", "apiv2", "developer", "status", "www"})
_BOARD_ROOTS = frozenset({"careerhub", "careers"})
_ALLOWED_SECOND_SEGMENTS = frozenset({"embed", "home", "job", "jobs", "position", "positions"})

EIGHTFOLD_POLICY_REASON = (
    "policy-held: Eightfold's official Position APIs require tenant-issued authentication and "
    "Position:READ permission; no documented anonymous complete-manifest API was verified"
)


@dataclass(frozen=True, slots=True)
class EightfoldPolicyHeldCandidate:
    """An exact observed board URL that is not authorized for ingestion."""

    observed_url: str
    host: str
    tenant: str
    board_path: str
    customer_domain: str = ""
    policy_status: str = "review_required"
    activation_allowed: bool = False
    policy_reason: str = EIGHTFOLD_POLICY_REASON


@dataclass(frozen=True, slots=True)
class EightfoldPolicyProbe:
    """A local-only probe result; it deliberately performs zero HTTP requests."""

    candidate: EightfoldPolicyHeldCandidate
    network_requests: int = 0
    endpoint_validated: bool = False
    complete_manifest_validated: bool = False
    pagination_validated: bool = False
    posting_dates_validated: bool = False
    locations_validated: bool = False
    us_filter_compatible: bool = False
    bounded_http_validated: bool = False
    activation_allowed: bool = False
    outcome: str = "policy_held"


def classify_eightfold_board_url(url: str) -> EightfoldPolicyHeldCandidate | None:
    """Recognize only exact HTTPS Eightfold career-board observations."""

    value = url.strip()
    if not value or len(value) > 4096:
        return None
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").casefold().rstrip(".")
    if (
        parsed.scheme.casefold() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
        or not host.endswith(_HOST_SUFFIX)
    ):
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return None
    tenant = host.removesuffix(_HOST_SUFFIX)
    if (
        not tenant
        or "." in tenant
        or not _TENANT_LABEL.fullmatch(tenant)
        or tenant in _RESERVED_TENANTS
    ):
        return None
    segments = [segment.casefold() for segment in parsed.path.split("/") if segment]
    if not segments or segments[0] not in _BOARD_ROOTS:
        return None
    if len(segments) > 1 and segments[1] not in _ALLOWED_SECOND_SEGMENTS:
        return None
    query = parse_qs(parsed.query, keep_blank_values=True)
    domains = query.get("domain", [])
    if len(domains) > 1:
        return None
    customer_domain = domains[0].casefold().rstrip(".") if domains else ""
    if customer_domain and not _DOMAIN.fullmatch(customer_domain):
        return None
    return EightfoldPolicyHeldCandidate(
        observed_url=value,
        host=host,
        tenant=tenant,
        board_path=parsed.path or "/",
        customer_domain=customer_domain,
    )


def probe_eightfold_policy(url: str) -> EightfoldPolicyProbe:
    """Return the policy hold for an exact URL without accessing the network."""

    candidate = classify_eightfold_board_url(url)
    if candidate is None:
        raise ValueError("URL is not an exact supported-shape Eightfold public board observation")
    return EightfoldPolicyProbe(candidate=candidate)
