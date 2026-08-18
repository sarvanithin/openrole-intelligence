from __future__ import annotations

import pytest

from fortune_intel.services.us_location import (
    USLocationClassification,
    classify_us_job_location,
)


@pytest.mark.parametrize(
    "location",
    [
        "San Francisco, CA",
        "Austin, Texas",
        "New York, NY 10001",
        "Washington, D.C.",
        "Remote - US",
        "United States (Remote)",
        "Anywhere in the U.S.",
        "Charlotte, North Carolina / Boston, Massachusetts",
        "North Chicago, IL, us",
        "United States - California - Foster City",
        "Washington State",
        "Gilbert-Arizona-United States of America",
        "US-VA Arlington",
        "Lynnwood, Washington",
    ],
)
def test_classifies_definite_us_locations(location: str) -> None:
    result = classify_us_job_location(location)

    assert result.classification is USLocationClassification.US
    assert result.is_us_eligible
    assert result.evidence


@pytest.mark.parametrize(
    "location",
    [
        "Toronto, Ontario, Canada",
        "London, UK",
        "Berlin, Germany",
        "Vancouver, BC",
        "Remote - India",
        "Multiple locations - EMEA",
        "Sydney, Australia / Auckland, New Zealand",
        "Pune-Maharashtra-India",
    ],
)
def test_classifies_definite_non_us_locations(location: str) -> None:
    result = classify_us_job_location(location)

    assert result.classification is USLocationClassification.NON_US
    assert not result.is_us_eligible
    assert result.evidence


@pytest.mark.parametrize(
    "location",
    [
        None,
        "",
        "Remote",
        "Worldwide",
        "Americas",
        "North America",
        "Washington",
        "Georgia",
        "CA",
        "IN",
        "OR",
        "Bangalore, IN",
        "Berlin, DE",
        "CO - Bogota",
        "ID-Jakarta",
        "IL - Petah Tikva",
        "CA - Mississauga",
        "AR - Buenos Aires",
        "GA-Port Gentil",
    ],
)
def test_leaves_ambiguous_locations_unknown(location: str | None) -> None:
    result = classify_us_job_location(location)

    assert result.classification is USLocationClassification.UNKNOWN
    assert not result.is_us_eligible
    assert result.evidence == ()


@pytest.mark.parametrize(
    "location",
    [
        "Portland",
        "Main office",
        "California-style remote policy",
        "ORANGE COUNTY",
        "Candidate must join us remotely",
        "Texasinstruments campus",
    ],
)
def test_does_not_match_state_codes_or_names_as_substrings(location: str) -> None:
    assert classify_us_job_location(location).classification is USLocationClassification.UNKNOWN


def test_us_location_wins_for_a_multi_country_opening() -> None:
    result = classify_us_job_location("New York, NY | London, UK | Toronto, Canada")

    assert result.classification is USLocationClassification.US
    assert "NY" in result.evidence


@pytest.mark.parametrize(
    "location",
    [
        "San Juan, PR",
        "Puerto Rico",
        "Guam",
        "Puerto Rico, United States of America",
        "Hagatna-Guam-United States of America",
        "U.S. Virgin Islands",
    ],
)
def test_us_territories_are_classified_but_not_publicly_eligible(location: str) -> None:
    result = classify_us_job_location(location)

    assert result.classification is USLocationClassification.US_TERRITORY
    assert result.eligibility == "ineligible"
    assert not result.is_us_eligible


def test_structured_country_can_establish_an_otherwise_unknown_location() -> None:
    result = classify_us_job_location("Remote", metadata={"location_country": "US"})

    assert result.classification is USLocationClassification.US
    assert result.eligibility == "eligible"


def test_conflicting_structured_country_and_display_location_are_unknown() -> None:
    result = classify_us_job_location("Austin, TX", metadata={"primary_location_country": "CA"})

    assert result.classification is USLocationClassification.CONFLICT
    assert result.eligibility in {"unknown", "ineligible"}
    assert not result.is_us_eligible


@pytest.mark.parametrize(
    "location",
    [
        "Tijuana, Baja California, Mexico",
        "1401-G-India: ELCOT IT Bldg, Madurai, TN",
    ],
)
def test_non_us_country_conflicting_with_a_us_name_or_code_fails_closed(location: str) -> None:
    result = classify_us_job_location(location)

    assert result.classification in {
        USLocationClassification.CONFLICT,
        USLocationClassification.NON_US,
    }
    assert result.eligibility in {"unknown", "ineligible"}
    assert not result.is_us_eligible


def test_ambiguous_state_code_is_accepted_with_us_postal_code() -> None:
    result = classify_us_job_location("Indianapolis, IN 46204")

    assert result.classification is USLocationClassification.US
    assert result.evidence == ("IN",)
