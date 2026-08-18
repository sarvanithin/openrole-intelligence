import pytest

from fortune_intel.domain import (
    SPONSORSHIP_RULE_VERSION,
    SponsorshipTier,
    canonicalize_url,
)
from fortune_intel.services.sponsorship import EmployerHistory, assess_sponsorship


def test_explicit_no_overrides_strong_employer_history():
    assessment = assess_sponsorship(
        "Candidates must be able to work without current or future visa sponsorship.",
        EmployerHistory(
            uscis_initial_approvals=500,
            lca_worker_positions=1000,
            latest_fiscal_year=2026,
            entity_match_confidence=1.0,
        ),
        current_year=2026,
    )
    assert assessment.tier == SponsorshipTier.EXPLICIT_NO
    assert assessment.evidence_score == 0
    assert "without current or future visa sponsorship" in assessment.policy_excerpt.lower()


def test_explicit_offer_is_tier_a():
    assessment = assess_sponsorship("Visa sponsorship is available for this position.")
    assert assessment.tier == SponsorshipTier.EXPLICIT_YES
    assert assessment.evidence_score == 100


def test_screening_question_is_not_misclassified_as_policy():
    assessment = assess_sponsorship(
        "Will you now or in the future require sponsorship for employment?"
    )
    assert assessment.tier == SponsorshipTier.INSUFFICIENT


def test_ability_to_work_question_is_not_a_negative_policy():
    assessment = assess_sponsorship(
        "Are you able to work in the United States without visa sponsorship?"
    )
    assert assessment.tier == SponsorshipTier.INSUFFICIENT


def test_with_or_without_language_requires_review():
    assessment = assess_sponsorship("We consider candidates with or without visa sponsorship.")
    assert assessment.tier == SponsorshipTier.INSUFFICIENT
    assert assessment.reasons == ("ambiguous_sponsorship_language_requires_review",)


def test_role_specific_negative_overrides_general_positive_language():
    assessment = assess_sponsorship(
        "Visa sponsorship is available for some roles. This role does not sponsor."
    )
    assert assessment.tier == SponsorshipTier.EXPLICIT_NO
    assert assessment.reasons == ("current_posting_negative_language_detected",)


@pytest.mark.parametrize(
    "description",
    (
        "This position is not currently eligible for visa sponsorship.",
        "No H1-B visa sponsorship is available for this role.",
        "No new H-1B sponsorship is available.",
        "No relocation assistance or visa sponsorship is available.",
        "Visa sponsorship is available at our company. This role does not sponsor.",
    ),
)
def test_real_world_negative_variants_are_never_tier_a(description):
    assessment = assess_sponsorship(description)
    assert assessment.tier == SponsorshipTier.EXPLICIT_NO


@pytest.mark.parametrize(
    "description",
    (
        "We will sponsor a security clearance for this position.",
        "The company may sponsor qualified candidates.",
        "Visa sponsorship is available for some roles.",
        "Are you eligible for visa sponsorship?",
    ),
)
def test_generic_or_non_immigration_sponsorship_language_is_not_tier_a(description):
    assessment = assess_sponsorship(description)
    assert assessment.tier == SponsorshipTier.INSUFFICIENT


def test_job_scoped_offer_is_required_for_tier_a():
    assessment = assess_sponsorship("Visa sponsorship is available with this position.")
    assert assessment.tier == SponsorshipTier.EXPLICIT_YES


@pytest.mark.parametrize(
    "description",
    (
        "This position is not eligible for visa sponsorship.",
        "This role is ineligible for H-1B sponsorship.",
        "Visa sponsorship is not available for this position.",
        "Immigration sponsorship for this role will be unavailable.",
        "We cannot sponsor candidates for this opening.",
        "Candidates do not qualify for employment visa sponsorship.",
    ),
)
def test_explicit_negative_variants_are_tier_e(description):
    assessment = assess_sponsorship(description)
    assert assessment.tier == SponsorshipTier.EXPLICIT_NO
    assert assessment.evidence_score == 0
    assert assessment.reasons == ("current_posting_negative_language_detected",)
    assert assessment.rule_version == SPONSORSHIP_RULE_VERSION


def test_not_eligible_does_not_trigger_eligible_positive_substring():
    assessment = assess_sponsorship(
        "Applicants are not eligible for visa sponsorship for this opportunity."
    )
    assert assessment.tier == SponsorshipTier.EXPLICIT_NO
    assert "not eligible for visa sponsorship" in assessment.policy_excerpt.lower()


def test_recent_official_history_is_explainable_but_not_tier_a():
    assessment = assess_sponsorship(
        "Build data platforms.",
        EmployerHistory(
            uscis_initial_approvals=25,
            lca_worker_positions=80,
            latest_fiscal_year=2025,
            entity_match_confidence=1.0,
        ),
        current_year=2026,
    )
    assert assessment.tier == SponsorshipTier.STRONG_HISTORY
    assert 0 < assessment.evidence_score < 100
    assert "recent_uscis_initial_approval_history" in assessment.reasons


def test_weak_entity_match_never_infers_company_history():
    assessment = assess_sponsorship(
        "Build data platforms.",
        EmployerHistory(
            uscis_initial_approvals=200,
            latest_fiscal_year=2026,
            entity_match_confidence=0.75,
        ),
        current_year=2026,
    )
    assert assessment.tier == SponsorshipTier.INSUFFICIENT


def test_url_canonicalization_removes_tracking_but_keeps_identity_parameters():
    url = canonicalize_url("HTTPS://Jobs.Example.com/roles/42/?utm_source=mail&job=42#apply")
    assert url == "https://jobs.example.com/roles/42?job=42"


def test_greenhouse_job_id_is_not_treated_as_tracking():
    first = canonicalize_url("https://boards.example.com/jobs?gh_jid=101&utm_source=x")
    second = canonicalize_url("https://boards.example.com/jobs?gh_jid=202&utm_source=x")
    assert first != second
