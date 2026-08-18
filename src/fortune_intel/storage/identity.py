"""Stable organization identity helpers shared by imports and storage."""

from __future__ import annotations

import hashlib
import re
import unicodedata


def normalize_company_name(name: str) -> str:
    """Normalize punctuation without collapsing distinct legal suffixes."""

    normalized = "".join(
        character if character.isalnum() else " "
        for character in unicodedata.normalize("NFKC", name).casefold()
    )
    return " ".join(normalized.split())


def slugify(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", normalize_company_name(name))
    ascii_name = normalized.encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")
    if slug:
        return slug
    digest = hashlib.sha256(name.casefold().encode()).hexdigest()[:10]
    return f"company-{digest}"
