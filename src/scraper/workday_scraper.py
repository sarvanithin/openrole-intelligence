"""
Workday-specific scraper - handles myworkdayjobs.com career sites.
Workday sites use a consistent API structure that we can leverage.
"""
import asyncio
import json
import re
from typing import List, Optional
from urllib.parse import urljoin, urlparse, parse_qs

from playwright.async_api import async_playwright, Page, Browser, TimeoutError as PlaywrightTimeout

from config import (
    SCRAPE_DELAY_SECONDS,
    PAGE_LOAD_TIMEOUT_MS,
    MAX_PAGES_PER_COMPANY,
)
from scraper.base_scraper import BaseScraper, Job


class WorkdayScraper(BaseScraper):
    """
    Specialized scraper for Workday career sites.
    Workday sites have a predictable structure that we can exploit.
    """

    # Workday-specific selectors
    JOB_LIST_SELECTORS = [
        '[data-automation-id="jobTitle"]',
        'a[data-automation-id="jobTitle"]',
        '[data-automation-id="compositeJobResults"] a',
        '.job-listing a[href*="job/"]',
        'section[data-automation-id="jobResults"] a',
    ]

    PAGINATION_SELECTORS = [
        'button[data-automation-id="paginationNextBtn"]',
        'button[aria-label="next"]',
        '[data-automation-id="paginationNextBtn"]',
        'a[data-uxi-element-id="next"]',
    ]

    def __init__(self, company_name: str, career_url: str):
        super().__init__(company_name, career_url)
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.seen_urls: set = set()

    async def scrape(self) -> List[Job]:
        """Scrape all job listings from Workday career page."""
        all_jobs: List[Job] = []

        async with async_playwright() as p:
            self.browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                ]
            )

            context = await self.browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            )

            self.page = await context.new_page()

            try:
                # Navigate to career page
                await self.page.goto(
                    self.career_url,
                    wait_until='networkidle',
                    timeout=PAGE_LOAD_TIMEOUT_MS,
                )
                await asyncio.sleep(2)  # Extra wait for Workday's JS

                # Scrape pages
                page_count = 0
                while page_count < MAX_PAGES_PER_COMPANY:
                    page_count += 1
                    print(f"  [Workday] Scraping page {page_count} for {self.company_name}...")

                    # Extract jobs from current page
                    page_jobs = await self._extract_jobs()
                    all_jobs.extend(page_jobs)

                    if not page_jobs:
                        print(f"  [Workday] No jobs found on page {page_count}")
                        break

                    # Try pagination
                    has_more = await self._handle_pagination()
                    if not has_more:
                        break

                    await asyncio.sleep(SCRAPE_DELAY_SECONDS)

            except Exception as e:
                print(f"  [Workday] Error scraping {self.company_name}: {e}")

            finally:
                await self.browser.close()

        # Filter by keywords
        filtered_jobs = []
        for job in all_jobs:
            matched_keywords = self.matches_keywords(job.job_title)
            if matched_keywords:
                job.keywords_matched = matched_keywords
                filtered_jobs.append(job)

        print(f"  [Workday] Found {len(filtered_jobs)} matching jobs (out of {len(all_jobs)} total)")
        return filtered_jobs

    async def _extract_jobs(self) -> List[Job]:
        """Extract jobs from current Workday page."""
        jobs: List[Job] = []

        for selector in self.JOB_LIST_SELECTORS:
            try:
                elements = await self.page.query_selector_all(selector)
                if not elements:
                    continue

                for element in elements:
                    job = await self._parse_workday_job(element)
                    if job and job.job_url not in self.seen_urls:
                        self.seen_urls.add(job.job_url)
                        jobs.append(job)

                if jobs:
                    break  # Found jobs with this selector

            except Exception as e:
                print(f"    Selector {selector} error: {e}")
                continue

        return jobs

    async def _parse_workday_job(self, element) -> Optional[Job]:
        """Parse a Workday job element."""
        try:
            # Get href - might be on element or child
            href = await element.get_attribute('href')
            if not href:
                link = await element.query_selector('a')
                if link:
                    href = await link.get_attribute('href')

            if not href:
                return None

            job_url = self.normalize_url(href)

            # Get title
            title = await element.text_content()
            title = self.clean_text(title) if title else ""

            if not title or len(title) < 3:
                return None

            # Try to get location from nearby element
            location = await self._get_workday_location(element)

            # Extract job ID from URL if possible
            job_id = self._extract_workday_job_id(job_url) or self.generate_job_id(job_url, title)

            return Job(
                job_id=job_id,
                job_title=title,
                job_url=job_url,
                company_name=self.company_name,
                company_career_url=self.career_url,
                location=location,
            )

        except Exception:
            return None

    async def _get_workday_location(self, element) -> str:
        """Extract location from Workday job listing."""
        try:
            # Look for location in sibling/parent elements
            parent = await element.evaluate_handle('el => el.closest("li, article, div[data-automation-id]")')
            if parent:
                location_el = await parent.query_selector('[data-automation-id="locationText"], [class*="location"]')
                if location_el:
                    text = await location_el.text_content()
                    return self.clean_text(text) if text else ""
        except Exception:
            pass
        return ""

    def _extract_workday_job_id(self, url: str) -> Optional[str]:
        """Extract Workday job ID from URL."""
        # Workday URLs often have job ID in path or query
        patterns = [
            r'/job/([A-Za-z0-9_-]+)',
            r'jobPostingId=([A-Za-z0-9_-]+)',
            r'R-(\d+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return f"WD_{match.group(1)}"
        return None

    async def _handle_pagination(self) -> bool:
        """Handle Workday pagination."""
        for selector in self.PAGINATION_SELECTORS:
            try:
                button = await self.page.query_selector(selector)
                if button:
                    is_disabled = await button.get_attribute('disabled')
                    aria_disabled = await button.get_attribute('aria-disabled')

                    if is_disabled or aria_disabled == 'true':
                        continue

                    is_visible = await button.is_visible()
                    if not is_visible:
                        continue

                    await button.click()

                    # Wait for new content
                    try:
                        await self.page.wait_for_load_state('networkidle', timeout=10000)
                    except PlaywrightTimeout:
                        pass

                    await asyncio.sleep(1.5)
                    return True

            except Exception:
                continue

        return False
