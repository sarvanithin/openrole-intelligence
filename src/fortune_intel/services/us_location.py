"""Conservative classification of free-text job locations for U.S. eligibility."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from fortune_intel.services.us_location_codes import (
    COUNTRY_AMBIGUOUS_STATE_CODES,
    US_STATE_CODES,
)

LOCATION_RULE_VERSION = "us-location-v4"


class USLocationClassification(StrEnum):
    """Whether location text establishes that a job has a U.S. work location."""

    US = "us"
    US_TERRITORY = "us_territory"
    NON_US = "non_us"
    UNKNOWN = "unknown"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class USLocationAssessment:
    """A classification together with the exact indicators that produced it."""

    classification: USLocationClassification
    evidence: tuple[str, ...] = ()

    @property
    def is_us_eligible(self) -> bool:
        """Return true only when at least one definite U.S. location is present."""

        return self.classification is USLocationClassification.US

    @property
    def eligibility(self) -> str:
        """Return the stable persistence/publication decision."""

        if self.classification is USLocationClassification.US:
            return "eligible"
        if self.classification in {
            USLocationClassification.NON_US,
            USLocationClassification.US_TERRITORY,
        }:
            return "ineligible"
        return "unknown"


_US_STATE_NAMES = frozenset(
    {
        "alabama",
        "alaska",
        "arizona",
        "arkansas",
        "california",
        "colorado",
        "connecticut",
        "delaware",
        "florida",
        # Plain "Georgia" can mean the country, so it needs separate evidence.
        "hawaii",
        "idaho",
        "illinois",
        "indiana",
        "iowa",
        "kansas",
        "kentucky",
        "louisiana",
        "maine",
        "maryland",
        "massachusetts",
        "michigan",
        "minnesota",
        "mississippi",
        "missouri",
        "montana",
        "nebraska",
        "nevada",
        "new hampshire",
        "new jersey",
        "new mexico",
        "new york",
        "north carolina",
        "north dakota",
        "ohio",
        "oklahoma",
        "oregon",
        "pennsylvania",
        "rhode island",
        "south carolina",
        "south dakota",
        "tennessee",
        "texas",
        "utah",
        "vermont",
        "virginia",
        "west virginia",
        "wisconsin",
        "wyoming",
    }
)

_US_TERRITORY_NAMES = frozenset(
    {
        "american samoa",
        "guam",
        "northern mariana islands",
        "puerto rico",
        "u.s. virgin islands",
        "us virgin islands",
        "united states virgin islands",
    }
)

_US_TERRITORY_CODES = frozenset({"AS", "GU", "MP", "PR", "VI"})

# These are intentionally country/region names, not city names. Matching cities
# (for example, Paris or London) would incorrectly exclude their U.S. namesakes.
_NON_US_NAMES = frozenset(
    {
        "argentina",
        "australia",
        "austria",
        "bangladesh",
        "belgium",
        "brazil",
        "canada",
        "chile",
        "china",
        "colombia",
        "costa rica",
        "czech republic",
        "czechia",
        "denmark",
        "egypt",
        "england",
        "finland",
        "france",
        "germany",
        "greece",
        "hong kong",
        "hungary",
        "india",
        "indonesia",
        "ireland",
        "israel",
        "italy",
        "japan",
        "kenya",
        "luxembourg",
        "malaysia",
        "mexico",
        "netherlands",
        "new zealand",
        "nigeria",
        "northern ireland",
        "norway",
        "pakistan",
        "peru",
        "philippines",
        "poland",
        "portugal",
        "romania",
        "scotland",
        "singapore",
        "south africa",
        "south korea",
        "spain",
        "sweden",
        "switzerland",
        "taiwan",
        "thailand",
        "turkey",
        "ukraine",
        "united arab emirates",
        "united kingdom",
        "vietnam",
        "wales",
    }
)

_CANADIAN_PROVINCE_NAMES = frozenset(
    {
        "alberta",
        "british columbia",
        "manitoba",
        "new brunswick",
        "newfoundland and labrador",
        "northwest territories",
        "nova scotia",
        "nunavut",
        "ontario",
        "prince edward island",
        "quebec",
        "saskatchewan",
        "yukon",
    }
)

_CANADIAN_PROVINCE_CODES = frozenset(
    {"AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"}
)

_REGION_MARKERS = frozenset({"apac", "emea"})


def _phrase_pattern(phrases: frozenset[str]) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(item) for item in sorted(phrases, key=len, reverse=True))
    return re.compile(rf"(?<![\w-])(?:{alternatives})(?![\w-])", re.IGNORECASE)


def _delimited_phrase_pattern(phrases: frozenset[str]) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(item) for item in sorted(phrases, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)


_STATE_NAME_PATTERN = _phrase_pattern(_US_STATE_NAMES)
_TERRITORY_NAME_PATTERN = _delimited_phrase_pattern(_US_TERRITORY_NAMES)
_NON_US_NAME_PATTERN = _delimited_phrase_pattern(_NON_US_NAMES)
_PROVINCE_NAME_PATTERN = _phrase_pattern(_CANADIAN_PROVINCE_NAMES)
_REGION_PATTERN = _phrase_pattern(_REGION_MARKERS)

# U.S. abbreviations are accepted only as complete, structured location parts.
# This prevents false matches such as OR in "Portland" or IN in "India".
_STRUCTURED_CODE_PATTERN = re.compile(
    r"(?:^|[,:;/|()\[\]·\-]\s*)"
    r"(?P<code>[A-Z]{2})"
    r"(?=\s*(?:\d{5}(?:-\d{4})?)?\s*(?:$|[,:;/|()\[\]·\-]))"
)
_US_POSTAL_CODE_PATTERN = re.compile(r"(?<!\d)\d{5}(?:-\d{4})?(?!\d)")
_EXPLICIT_US_NAME_PATTERN = re.compile(
    r"(?<!\w)United States(?: of America)?(?!\w)",
    re.IGNORECASE,
)
_EXPLICIT_US_ABBREVIATION_PATTERN = re.compile(r"(?<!\w)(?:USA|U\.S\.A\.|U\.S\.|US)(?!\w)")
_REMOTE_US_PATTERN = re.compile(
    r"(?<![\w-])(?:remote\s*[-:/()]?\s*us|us\s*[-:/()]?\s*remote)(?![\w-])",
    re.IGNORECASE,
)
_DC_PATTERN = re.compile(
    r"(?<![\w-])(?:District of Columbia|Washington,?\s+D\.?C\.?)((?![\w-]))",
    re.IGNORECASE,
)
_WASHINGTON_STATE_PATTERN = re.compile(
    r"(?:(?<!\w)Washington State|,\s*Washington)(?!\w)", re.IGNORECASE
)
_EXPLICIT_NON_US_CODE_PATTERN = re.compile(
    r"(?:^|[,:;/|()\[\]·\-]\s*)(?:UK|UAE)(?=\s*(?:$|[,:;/|()\[\]·\-]))"
)


def _matches(pattern: re.Pattern[str], text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(match.group(0).strip() for match in pattern.finditer(text)))


def _metadata_text(metadata: Mapping[str, object] | None) -> tuple[str, tuple[str, ...]]:
    if not metadata:
        return "", ()
    location_values: list[str] = []
    for key in ("all_locations", "secondary_locations", "additional_locations"):
        value = metadata.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, str) and item.strip():
                location_values.append(item.strip())
            elif isinstance(item, dict):
                for field in ("location", "name", "city", "region", "country"):
                    part = item.get(field)
                    if isinstance(part, str) and part.strip():
                        location_values.append(part.strip())
    countries = tuple(
        str(metadata[key]).strip()
        for key in ("location_country", "primary_location_country", "country_code")
        if metadata.get(key) is not None and str(metadata[key]).strip()
    )
    return " | ".join(location_values), countries


def _structured_country(value: str) -> USLocationClassification:
    normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
    if normalized in {"us", "usa", "unitedstates", "unitedstatesofamerica"}:
        return USLocationClassification.US
    if normalized in {
        "as",
        "gu",
        "mp",
        "pr",
        "vi",
        "americansamoa",
        "guam",
        "northernmarianaislands",
        "puertorico",
        "usvirginislands",
    }:
        return USLocationClassification.US_TERRITORY
    if len(normalized) in {2, 3} or _NON_US_NAME_PATTERN.search(value):
        return USLocationClassification.NON_US
    return USLocationClassification.UNKNOWN


def _classify_text(location: str | None) -> USLocationAssessment:
    """Classify whether free text contains at least one definite U.S. job location.

    For multi-location jobs, one definite U.S. location makes the job U.S.-eligible,
    even when foreign locations are present too. Unknown is intentionally not treated
    as U.S.; callers may retain it in a review queue instead of publishing it.
    """

    if not location or not location.strip():
        return USLocationAssessment(USLocationClassification.UNKNOWN)

    text = " ".join(location.replace("–", "-").replace("—", "-").split())
    parent_us_evidence = list(_matches(_EXPLICIT_US_NAME_PATTERN, text))
    parent_us_evidence.extend(_matches(_EXPLICIT_US_ABBREVIATION_PATTERN, text))
    parent_us_evidence.extend(_matches(_REMOTE_US_PATTERN, text))
    specific_us_evidence = list(_matches(_DC_PATTERN, text))
    specific_us_evidence.extend(_matches(_WASHINGTON_STATE_PATTERN, text))
    specific_us_evidence.extend(_matches(_STATE_NAME_PATTERN, text))
    us_evidence = parent_us_evidence + specific_us_evidence
    territory_evidence = list(_matches(_TERRITORY_NAME_PATTERN, text))

    non_us_evidence = list(_matches(_NON_US_NAME_PATTERN, text))
    non_us_evidence.extend(_matches(_PROVINCE_NAME_PATTERN, text))
    non_us_evidence.extend(_matches(_REGION_PATTERN, text))
    non_us_evidence.extend(_matches(_EXPLICIT_NON_US_CODE_PATTERN, text))

    for match in _STRUCTURED_CODE_PATTERN.finditer(text):
        code = match.group("code")
        if code in _CANADIAN_PROVINCE_CODES:
            non_us_evidence.append(code)
        elif code in _US_TERRITORY_CODES:
            if text.strip() != code:
                territory_evidence.append(code)
        elif code in US_STATE_CODES:
            # Country-overlapping prefixes fail closed. Conventional comma-suffix
            # state form is allowed except DE/IN, which are common country suffixes.
            prefix = text[: match.start("code")].rstrip()
            comma_suffix = prefix.endswith(",") and code not in {"DE", "IN"}
            if text.strip() != code and (
                code not in COUNTRY_AMBIGUOUS_STATE_CODES
                or comma_suffix
                or us_evidence
                or _US_POSTAL_CODE_PATTERN.search(text)
            ):
                us_evidence.append(code)
                specific_us_evidence.append(code)

    if territory_evidence:
        if specific_us_evidence:
            return USLocationAssessment(
                USLocationClassification.US, tuple(dict.fromkeys(us_evidence))
            )
        return USLocationAssessment(
            USLocationClassification.US_TERRITORY,
            tuple(dict.fromkeys(territory_evidence)),
        )
    if us_evidence and non_us_evidence and not parent_us_evidence:
        segments = [part.strip() for part in re.split(r"\s*[|;/]\s*", text) if part.strip()]
        if len(segments) > 1:
            for segment in segments:
                segment_assessment = _classify_text(segment)
                if segment_assessment.classification is USLocationClassification.US:
                    return segment_assessment
        return USLocationAssessment(
            USLocationClassification.CONFLICT,
            tuple(dict.fromkeys(us_evidence + non_us_evidence)),
        )
    if us_evidence:
        return USLocationAssessment(USLocationClassification.US, tuple(dict.fromkeys(us_evidence)))
    if non_us_evidence:
        return USLocationAssessment(
            USLocationClassification.NON_US, tuple(dict.fromkeys(non_us_evidence))
        )
    return USLocationAssessment(USLocationClassification.UNKNOWN)


def classify_us_job_location(
    location: str | None,
    *,
    metadata: Mapping[str, object] | None = None,
) -> USLocationAssessment:
    """Classify a job using display text plus allowlisted ATS location metadata."""

    extra_text, country_values = _metadata_text(metadata)
    combined = " | ".join(part for part in (location or "", extra_text) if part.strip())
    text_assessment = _classify_text(combined)
    structured = {
        classification
        for classification in (_structured_country(value) for value in country_values)
        if classification is not USLocationClassification.UNKNOWN
    }
    if not structured:
        return text_assessment
    if len(structured) > 1:
        if USLocationClassification.US in structured and text_assessment.is_us_eligible:
            return text_assessment
        return USLocationAssessment(
            USLocationClassification.CONFLICT,
            tuple(f"structured_country:{value}" for value in country_values),
        )
    structured_classification = next(iter(structured))
    if text_assessment.classification is USLocationClassification.UNKNOWN:
        return USLocationAssessment(
            structured_classification,
            tuple(f"structured_country:{value}" for value in country_values),
        )
    if text_assessment.classification is not structured_classification:
        return USLocationAssessment(
            USLocationClassification.CONFLICT,
            text_assessment.evidence
            + tuple(f"structured_country:{value}" for value in country_values),
        )
    return USLocationAssessment(
        text_assessment.classification,
        text_assessment.evidence + tuple(f"structured_country:{value}" for value in country_values),
    )
