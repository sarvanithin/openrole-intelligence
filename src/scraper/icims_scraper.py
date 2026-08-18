"""
iCIMS career site scraper.
Handles sites hosted on *.icims.com domains.

iCIMS URL patterns:
- {company}.icims.com/jobs/{id}/job
- careers-{company}.icims.com/jobs/search
"""
import asyncio
import re
from typing import List
from urllib.parse import urljoin

from playwright.async_api import async_playwright

from config import PAGE_LOAD_TIMEOUT_MS, MAX_PAGES_PER_COMPANY, SCRAPE_DELAY_SECONDS
from scraper.base_scraper import BaseScraper, Job


class ICIMSScraper(BaseScraper):
    """
    Scraper for iCIMS career sites.
    Uses browser automation due to dynamic content.
    """

    # iCIMS-specific selectors
    JOB_SELECTORS = [
        'a.iCIMS_JobTitle',
        '.iCIMS_JobsTable a[href*="/jobs/"]',
        '.iCIMS_Anchor[href*="/jobs/"]',
        'a[href*="/jobs/"][href*="/job"]',
        '.job-results-list a',
        '[class*="job-title"] a',
        '.jobTitle a',
        'table.iCIMS_JobsTable a',
        '.iCIMS_JobListings a',
    ]

    PAGINATION_SELECTORS = [
        'a.iCIMS_Paging_Next',
        'a[aria-label*="next" i]',
        '[class*="pagination"] a:has-text("Next")',
        'a:has-text(">")',
        '.iCIMS_Paging a.iCIMS_Next',
    ]

    def __init__(self, company_name: str, career_url: str):
        super().__init__(company_name, career_url)
        self.seen_urls: set = set()

    async def scrape(self) -> List[Job]:
        """Scrape jobs from iCIMS career site."""
        all_jobs: List[Job] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
            )

            context = await browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )

            page = await context.new_page()

            try:
                print(f"  [iCIMS] Navigating to {self.career_url}")
                await page.goto(self.career_url, wait_until='networkidle', timeout=PAGE_LOAD_TIMEOUT_MS)
                await asyncio.sleep(3)

                page_count = 0
                while page_count < MAX_PAGES_PER_COMPANY:
                    page_count += 1
                    print(f"  [iCIMS] Scraping page {page_count}...")

                    # Extract jobs from current page
                    page_jobs = await self._extract_jobs(page)
                    
                    new_count = 0
                    for job in page_jobs:
                        if job.job_url not in self.seen_urls:
                            self.seen_urls.add(job.job_url)
                            all_jobs.append(job)
                            new_count += 1

                    if new_count == 0 and page_count > 1:
                        break

                    # Try pagination
                    has_next = await self._go_to_next_page(page)
                    if not has_next:
                        break

                    await asyncio.sleep(SCRAPE_DELAY_SECONDS)

            except Exception as e:
                print(f"  [iCIMS] Error: {e}")
            finally:
                await browser.close()

        # Filter by keywords
        filtered_jobs = []
        for job in all_jobs:
            matched_keywords = self.matches_keywords(job.job_title)
            if matched_keywords:
                job.keywords_matched = matched_keywords
                filtered_jobs.append(job)

        print(f"  [iCIMS] Found {len(filtered_jobs)} matching jobs (out of {len(all_jobs)} total)")
        return filtered_jobs

    async def _extract_jobs(self, page) -> List[Job]:
        """Extract jobs from current page."""
        jobs: List[Job] = []

        for selector in self.JOB_SELECTORS:
            elements = await page.query_selector_all(selector)
            if not elements:
                continue

            for element in elements:
                try:
                    href = await element.get_attribute('href')
                    if not href or '/jobs/' not in href.lower():
                        continue

                    job_url = self.normalize_url(href)
                    
                    title = await element.text_content()
                    title = self.clean_text(title) if title else ""
                    
                    if not title or len(title) < 3:
                        continue

                    job_id = self._extract_job_id(job_url)

                    jobs.append(Job(
                        job_id=job_id,
                        job_title=title,
                        job_url=job_url,
                        company_name=self.company_name,
                        company_career_url=self.career_url,
                    ))
                except Exception:
                    continue

            if jobs:
                break

        return jobs

    async def _go_to_next_page(self, page) -> bool:
        """Navigate to next page of results."""
        for selector in self.PAGINATION_SELECTORS:
            try:
                button = await page.query_selector(selector)
                if button:
                    is_visible = await button.is_visible()
                    if is_visible:
                        await button.click()
                        await page.wait_for_load_state('networkidle', timeout=10000)
                        await asyncio.sleep(2)
                        return True
            except Exception:
                continue
        return False

    def _extract_job_id(self, url: str) -> str:
        """Extract iCIMS job ID from URL."""
        match = re.search(r'/jobs/(\d+)', url)
        if match:
            return f"IC_{match.group(1)}"
        return self.generate_job_id(url, "")
