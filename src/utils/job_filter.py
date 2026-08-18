"""
Keyword filtering utilities.
"""
import re
from typing import List

from config import KEYWORDS


def matches_any_keyword(text: str, keywords: List[str] = None) -> List[str]:
    """
    Check if text matches any of the specified keywords.

    Args:
        text: Text to check (e.g., job title).
        keywords: List of keywords to match. Defaults to config.KEYWORDS.

    Returns:
        List of matched keywords.
    """
    if keywords is None:
        keywords = KEYWORDS

    text_lower = text.lower()
    matched = []

    for keyword in keywords:
        # Word boundary matching for accuracy
        pattern = r"\b" + re.escape(keyword.lower()) + r"\b"
        if re.search(pattern, text_lower):
            matched.append(keyword)

    return matched


def is_relevant_job(job_title: str) -> bool:
    """
    Quick check if a job title is relevant based on keywords.

    Args:
        job_title: The job title to check.

    Returns:
        True if matches any keyword, False otherwise.
    """
    return len(matches_any_keyword(job_title)) > 0
