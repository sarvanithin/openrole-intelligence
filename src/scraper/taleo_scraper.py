"""
Oracle Taleo career site scraper.
Handles legacy Taleo sites with *.taleo.net domains.

Taleo URL patterns:
- {company}.taleo.net/careersection/{section}/joblist.ftl
- {company}.taleo.net/careersection/{section}/jobdetail.ftl?job={id}
"""
import asyncio
import re
from typing import List
from urllib.parse import urljoin, parse_qs, urlparse

from playwright.async_api import async_playwright

from config import PAGE_LOAD_TIMEOUT_MS, MAX_PAGES_PER_COMPANY, SCRAPE_DELAY_SECONDS
from scraper.base_scraper import BaseScraper, Job


class TaleoScraper(BaseScraper):
    """
    Scraper for Oracle Taleo career sites.
    Uses browser automation to handle legacy dynamic content.
    """

    # Taleo-specific selectors
    JOB_SELECTORS = [
        'a[id*="requisitionListInterface"]',
        'a.jobTitle-link',
        'td.colTitle a',
        '.jobProperty a[href*="jobdetail"]',
        'a[href*="job="]',
        '.titlelink a',
        '#requisitionList a',
        'table.tablelist a[href*="requisitionListInterface"]',
    ]

    PAGINATION_SELECTORS = [
        'a#next',
        'a[id*="next" i]',
        'a.pagerNextLink',
        'img[alt*="Next"]',
        'a:has-text("Next")',
        '[onclick*="next"]',
    ]

    def __init__(self, company_name: str, career_url: str):
        super().__init__(company_name, career_url)
        self.seen_urls: set = set()

    async def scrape(self) -> List[Job]:
        """Scrape jobs from Taleo career site."""
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
                print(f"  [Taleo] Navigating to {self.career_url}")
                await page.goto(self.career_url, wait_until='networkidle', timeout=PAGE_LOAD_TIMEOUT_MS)
                await asyncio.sleep(5)  # Taleo sites are slow

                page_count = 0
                while page_count < MAX_PAGES_PER_COMPANY:
                    page_count += 1
                    print(f"  [Taleo] Scraping page {page_count}...")

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
                print(f"  [Taleo] Error: {e}")
            finally:
                await browser.close()

        # Filter by keywords
        filtered_jobs = []
        for job in all_jobs:
            matched_keywords = self.matches_keywords(job.job_title)
            if matched_keywords:
                job.keywords_matched = matched_keywords
                filtered_jobs.append(job)

        print(f"  [Taleo] Found {len(filtered_jobs)} matching jobs (out of {len(all_jobs)} total)")
        return filtered_jobs

    async def _extract_jobs(self, page) -> List[Job]:
        """Extract jobs from current page."""
        jobs: List[Job] = []

        # Taleo often uses tables
        rows = await page.query_selector_all('table tr, .requisitionList tr, #requisitionList tr')
        
        for row in rows:
            try:
                # Find job title link in row
                link = await row.query_selector('a[href*="job"], a.jobTitle-link, td.colTitle a')
                if not link:
                    continue

                href = await link.get_attribute('href')
                if not href:
                    continue

                job_url = self.normalize_url(href)
                
                title = await link.text_content()
                title = self.clean_text(title) if title else ""
                
                if not title or len(title) < 3:
                    continue

                job_id = self._extract_job_id(job_url)

                # Try to get location from row
                location = ""
                loc_cell = await row.query_selector('td.colLocation, td:nth-child(3), .locationColumn')
                if loc_cell:
                    location = await loc_cell.text_content()
                    location = self.clean_text(location) if location else ""

                jobs.append(Job(
                    job_id=job_id,
                    job_title=title,
                    job_url=job_url,
                    company_name=self.company_name,
                    company_career_url=self.career_url,
                    location=location,
                ))
            except Exception:
                continue

        # Fallback to direct selectors
        if not jobs:
            for selector in self.JOB_SELECTORS:
                elements = await page.query_selector_all(selector)
                for element in elements:
                    try:
                        href = await element.get_attribute('href')
                        if not href:
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
                        await page.wait_for_load_state('networkidle', timeout=15000)
                        await asyncio.sleep(3)
                        return True
            except Exception:
                continue
        return False

    def _extract_job_id(self, url: str) -> str:
        """Extract Taleo job ID from URL."""
        # Try job= parameter
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        if 'job' in params:
            return f"TL_{params['job'][0]}"
        
        # Try requisition ID pattern
        match = re.search(r'requisition[=/](\d+)', url, re.IGNORECASE)
        if match:
            return f"TL_{match.group(1)}"

        return self.generate_job_id(url, "")
