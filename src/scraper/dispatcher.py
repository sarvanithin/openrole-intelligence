"""
Dispatcher that routes URLs to the appropriate scraper based on platform detection.
Supports 8+ major ATS platforms with fallback to generic scraper.
"""
from typing import Optional
from urllib.parse import urlparse

from config import (
    WORKDAY_PATTERNS,
    EIGHTFOLD_PATTERNS,
    GREENHOUSE_PATTERNS,
    LEVER_PATTERNS,
    ICIMS_PATTERNS,
    TALEO_PATTERNS,
    SMARTRECRUITERS_PATTERNS,
    PLAID_PATTERNS,
)
from scraper.base_scraper import BaseScraper
from scraper.generic_scraper import GenericScraper
from scraper.workday_scraper import WorkdayScraper
from scraper.eightfold_scraper import EightfoldScraper
from scraper.greenhouse_scraper import GreenhouseScraper
from scraper.lever_scraper import LeverScraper
from scraper.icims_scraper import ICIMSScraper
from scraper.taleo_scraper import TaleoScraper
from scraper.smartrecruiters_scraper import SmartRecruitersScraper
from scraper.plaid_scraper import PlaidScraper


class ScraperDispatcher:
    """Routes career URLs to the appropriate platform-specific scraper."""

    # Platform detection priority (more specific patterns first)
    PLATFORM_MATCHERS = [
        # Specific company sites first
        ("plaid", PLAID_PATTERNS, PlaidScraper),
        # Major ATS platforms
        ("workday", WORKDAY_PATTERNS, WorkdayScraper),
        ("eightfold", EIGHTFOLD_PATTERNS, EightfoldScraper),
        ("greenhouse", GREENHOUSE_PATTERNS, GreenhouseScraper),
        ("lever", LEVER_PATTERNS, LeverScraper),
        ("icims", ICIMS_PATTERNS, ICIMSScraper),
        ("taleo", TALEO_PATTERNS, TaleoScraper),
        ("smartrecruiters", SMARTRECRUITERS_PATTERNS, SmartRecruitersScraper),
    ]

    @staticmethod
    def detect_platform(url: str) -> str:
        """
        Detect the career platform from URL patterns.

        Returns:
            Platform name or 'generic' if no match found.
        """
        url_lower = url.lower()

        for platform_name, patterns, _ in ScraperDispatcher.PLATFORM_MATCHERS:
            for pattern in patterns:
                if pattern.lower() in url_lower:
                    return platform_name

        return "generic"

    @staticmethod
    def get_scraper(
        company_name: str,
        career_url: str,
        platform_hint: Optional[str] = None,
    ) -> BaseScraper:
        """
        Get the appropriate scraper for a given career URL.

        Args:
            company_name: Company name for context.
            career_url: The career page URL to scrape.
            platform_hint: Optional hint about the platform type (from sheet).

        Returns:
            Appropriate scraper instance.
        """
        # Use hint if provided, otherwise detect
        platform = platform_hint.lower() if platform_hint else None
        
        if not platform:
            platform = ScraperDispatcher.detect_platform(career_url)

        print(f"Using {platform} scraper for {company_name}")

        # Find matching scraper class
        for platform_name, _, scraper_class in ScraperDispatcher.PLATFORM_MATCHERS:
            if platform == platform_name:
                return scraper_class(company_name, career_url)

        # Default to generic scraper
        return GenericScraper(company_name, career_url)

    @staticmethod
    def get_supported_platforms() -> list:
        """Return list of supported platform names."""
        return [name for name, _, _ in ScraperDispatcher.PLATFORM_MATCHERS] + ["generic"]
