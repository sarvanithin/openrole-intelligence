"""
Base scraper class defining the interface for all platform-specific scrapers.
"""
import hashlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urljoin, urlparse

from config import KEYWORDS


@dataclass
class Job:
    """Represents a scraped job listing."""

    job_id: str
    job_title: str
    job_url: str
    company_name: str
    company_career_url: str
    location: str = ""
    posted_date: str = ""  # Date job was posted (if available)
    keywords_matched: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for Sheets API."""
        return {
            "job_id": self.job_id,
            "job_title": self.job_title,
            "job_url": self.job_url,
            "company_name": self.company_name,
            "company_career_url": self.company_career_url,
            "location": self.location,
            "posted_date": self.posted_date,
            "keywords_matched": self.keywords_matched,
        }


class BaseScraper(ABC):
    """Abstract base class for job scrapers."""

    def __init__(self, company_name: str, career_url: str):
        """
        Initialize the scraper.

        Args:
            company_name: Name of the company being scraped.
            career_url: Base career page URL.
        """
        self.company_name = company_name
        self.career_url = career_url
        self.base_domain = self._extract_base_domain(career_url)

    @staticmethod
    def _extract_base_domain(url: str) -> str:
        """Extract the base domain from a URL."""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    @abstractmethod
    async def scrape(self) -> List[Job]:
        """
        Scrape all job listings from the career page.

        This must handle pagination and return all jobs.

        Returns:
            List of Job objects.
        """
        pass

    def generate_job_id(self, job_url: str, job_title: str) -> str:
        """
        Generate a unique job ID from URL and title.

        Uses a hash to create a consistent, unique identifier.
        """
        # Normalize the URL (remove query params that might change)
        parsed = urlparse(job_url)
        normalized = f"{parsed.netloc}{parsed.path}"

        # Create hash from normalized URL + company
        content = f"{self.company_name}|{normalized}|{job_title}"
        return hashlib.md5(content.encode()).hexdigest()[:16]

    def matches_keywords(self, job_title: str) -> List[str]:
        """
        Check if a job title matches any of our keywords.

        Args:
            job_title: The job title to check.

        Returns:
            List of matched keywords (empty if no match).
        """
        title_lower = job_title.lower()
        matched = []

        for keyword in KEYWORDS:
            # Use word boundary matching to avoid partial matches
            pattern = r"\b" + re.escape(keyword.lower()) + r"\b"
            if re.search(pattern, title_lower):
                matched.append(keyword)

        return matched

    def normalize_url(self, url: str, base_url: Optional[str] = None) -> str:
        """
        Normalize a relative or absolute URL.

        Args:
            url: The URL to normalize.
            base_url: Base URL for relative URLs.

        Returns:
            Absolute URL.
        """
        if not url:
            return ""

        # Already absolute
        if url.startswith(("http://", "https://")):
            return url

        # Relative URL - make absolute
        base = base_url or self.base_domain
        return urljoin(base, url)

    def clean_text(self, text: str) -> str:
        """Clean and normalize text content."""
        if not text:
            return ""
        # Remove extra whitespace
        return " ".join(text.split()).strip()
