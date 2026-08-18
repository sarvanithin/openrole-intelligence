"""Conservative, explainable sponsorship evidence assessment."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from fortune_intel.domain import SponsorshipAssessment, SponsorshipTier


@dataclass(frozen=True)
class EmployerHistory:
    uscis_initial_approvals: int = 0
    uscis_initial_denials: int = 0
    lca_worker_positions: int = 0
    latest_fiscal_year: int | None = None
    entity_match_confidence: float = 0.0
    recency_weighted: bool = False


_ROLE = r"(?:role|position|job|opening|opportunity)"
_IMMIGRATION_SPONSORSHIP = (
    r"(?:(?:(?:employment\s+)?(?:immigration|visa)|employment|"
    r"h\s*[- ]?\s*1\s*[- ]?\s*b(?:\s+visa)?)\s+)?"
    r"sponsorship"
)


_NEGATIVE_RULES = (
    re.compile(
        r"(?:unable|not able) to (?:provide|offer) " + _IMMIGRATION_SPONSORSHIP,
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:will|do|does|can) not sponsor|cannot sponsor|won't sponsor", re.IGNORECASE
    ),
    re.compile(
        r"\bno\b[^.!?]{0,90}?\b"
        r"(?:employment|immigration|visa|h\s*[- ]?\s*1\s*[- ]?\s*b)\s+"
        r"sponsorship\b(?:\s+(?:is|are))?\s+(?:currently\s+)?"
        r"(?:available|provided|offered)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:not\s+(?:currently\s+)?eligible|ineligible)\s+for\s+"
        + _IMMIGRATION_SPONSORSHIP,
        re.IGNORECASE,
    ),
    re.compile(
        _IMMIGRATION_SPONSORSHIP
        + r"(?:\s+for\s+(?:this|the|your)\s+"
        + _ROLE
        + r")?\s+(?:is|will be)\s+"
        + r"(?:not\s+(?:currently\s+)?(?:available|provided|offered)|unavailable)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:(?:applicants?|candidates?|employees?)\s+)?(?:do|does) not qualify for "
        + _IMMIGRATION_SPONSORSHIP,
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:applicants?|candidates?|employees?) (?:must|need to) (?:be able to )?"
        r"(?:work|remain employed)[^.]{0,80}without (?:current or future )?(?:visa )?sponsorship",
        re.IGNORECASE,
    ),
)

_ROLE_SCOPED_POSITIVE_RULES = (
    re.compile(
        r"(?:visa|immigration|employment|h\s*[- ]?\s*1\s*[- ]?\s*b)\s+"
        r"sponsorship\s+(?:is|will be)\s+(?:available|provided|offered)\s+"
        r"(?:for|with)\s+(?:this|the|your)\s+" + _ROLE,
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:this|the|your)\s+" + _ROLE + r"\s+(?:is|will be)\s+eligible\s+for\s+"
        + _IMMIGRATION_SPONSORSHIP,
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:we|the company)\s+will\s+sponsor\s+(?:an?\s+)?"
        r"(?:employment\s+|immigration\s+|visa\s+|h\s*[- ]?\s*1\s*[- ]?\s*b\s+)"
        r"(?:for\s+)?(?:this|the|your)\s+" + _ROLE,
        re.IGNORECASE,
    ),
)

_REVIEW_RULES = (
    re.compile(r"with (?:or|and) without (?:visa )?sponsorship", re.IGNORECASE),
    re.compile(r"both sponsored and non-?sponsored candidates", re.IGNORECASE),
    re.compile(
        r"(?:visa|immigration|employment|h\s*[- ]?\s*1\s*[- ]?\s*b)\s+"
        r"sponsorship\s+(?:is|will be)\s+(?:available|provided|offered)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:we|the company)\s+(?:can|may|will)\s+sponsor\b", re.IGNORECASE),
    re.compile(r"eligible\s+for\s+" + _IMMIGRATION_SPONSORSHIP, re.IGNORECASE),
)


def _excerpt(text: str, match: re.Match[str], radius: int = 90) -> str:
    start = max(0, match.start() - radius)
    end = min(len(text), match.end() + radius)
    return " ".join(text[start:end].split())


def assess_sponsorship(
    description: str,
    history: EmployerHistory | None = None,
    *,
    current_year: int | None = None,
) -> SponsorshipAssessment:
    """Assign an auditable evidence tier.

    Employer history is contextual evidence only. A current posting's detected
    negative language always overrides a company's historical filings.
    """

    text = description or ""
    negative_matches = [match for rule in _NEGATIVE_RULES if (match := rule.search(text))]
    if negative_matches:
        # A role-specific denial is authoritative for the current posting, even
        # when the same text also contains a positive-looking substring such as
        # "eligible for visa sponsorship" inside "not eligible for ..." or a
        # general statement about other roles.
        match = min(negative_matches, key=lambda item: item.start())
        return SponsorshipAssessment(
            tier=SponsorshipTier.EXPLICIT_NO,
            evidence_score=0,
            reasons=("current_posting_negative_language_detected",),
            policy_excerpt=_excerpt(text, match),
        )

    positive_matches = [
        match for rule in _ROLE_SCOPED_POSITIVE_RULES if (match := rule.search(text))
    ]
    if positive_matches:
        return SponsorshipAssessment(
            tier=SponsorshipTier.EXPLICIT_YES,
            evidence_score=100,
            reasons=("current_posting_positive_language_detected",),
            policy_excerpt=_excerpt(text, positive_matches[0]),
        )

    for rule in _REVIEW_RULES:
        if match := rule.search(text):
            return SponsorshipAssessment(
                tier=SponsorshipTier.INSUFFICIENT,
                evidence_score=0,
                reasons=("ambiguous_sponsorship_language_requires_review",),
                policy_excerpt=_excerpt(text, match),
            )

    history = history or EmployerHistory()
    if history.entity_match_confidence < 0.9:
        return SponsorshipAssessment(
            tier=SponsorshipTier.INSUFFICIENT,
            evidence_score=0,
            reasons=("no_high_confidence_employer_evidence_match",),
        )

    total_signal = history.uscis_initial_approvals + history.lca_worker_positions
    if total_signal <= 0:
        return SponsorshipAssessment(
            tier=SponsorshipTier.INSUFFICIENT,
            evidence_score=0,
            reasons=("no_recent_official_sponsorship_history",),
        )

    now_year = current_year or datetime.now(UTC).year
    age = max(0, now_year - (history.latest_fiscal_year or 0))
    freshness = 1.0 if history.recency_weighted else max(0.1, 1 - (0.18 * age))
    petition_total = history.uscis_initial_approvals + history.uscis_initial_denials
    approval_quality = (
        history.uscis_initial_approvals / petition_total if petition_total >= 20 else 1.0
    )
    approval_component = math.log1p(history.uscis_initial_approvals) * 14 * approval_quality
    lca_component = math.log1p(history.lca_worker_positions) * 8
    score = min(89, round((approval_component + lca_component) * freshness))

    reasons = []
    if history.uscis_initial_approvals:
        reasons.append("recent_uscis_initial_approval_history")
    if history.lca_worker_positions:
        reasons.append("recent_dol_lca_worker_position_history")
    if age > 2:
        reasons.append("official_evidence_is_older_than_two_years")

    strong = age <= 2 and (
        history.uscis_initial_approvals >= 20 or history.lca_worker_positions >= 50
    )
    return SponsorshipAssessment(
        tier=(SponsorshipTier.STRONG_HISTORY if strong else SponsorshipTier.EMPLOYER_HISTORY),
        evidence_score=score,
        reasons=tuple(reasons),
    )
