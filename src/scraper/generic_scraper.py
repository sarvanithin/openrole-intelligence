"""
Generic Playwright-based scraper that works with most career pages.
Uses heuristics to find job listings and handles JavaScript-rendered content.
"""
import asyncio
import re
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse, parse_qs, urlencode

from playwright.async_api import async_playwright, Page, Browser, TimeoutError as PlaywrightTimeout

from config import (
    SCRAPE_DELAY_SECONDS,
    PAGE_LOAD_TIMEOUT_MS,
    MAX_PAGES_PER_COMPANY,
    MAX_RETRIES,
)
from scraper.base_scraper import BaseScraper, Job


class GenericScraper(BaseScraper):
    """
    Generic scraper using Playwright for JavaScript-heavy career pages.
    Uses smart heuristics to detect job listings and pagination.
    """

    # Common selectors for job listings across different sites
    JOB_LISTING_SELECTORS = [
        # Common job card patterns
        '[data-job-id]',
        '[data-automation-id*="job"]',
        '[data-testid*="job"]',
        '[data-test*="job"]',
        # Direct job links
        'a[href*="/job/"]',
        'a[href*="/jobs/"]',
        'a[href*="/position/"]',
        'a[href*="/requisition/"]',
        'a[href*="/opening/"]',
        'a[href*="/career/"]',
        'a[href*="/posting/"]',
        'a[href*="/vacancy/"]',
        'a[href*="jobId="]',
        'a[href*="job-id="]',
        'a[href*="positionId="]',
        # Class-based patterns
        '.job-listing a',
        '.job-card a',
        '.job-item a',
        '.job-result a',
        '.job-row a',
        '.jobs-list a',
        '.career-listing a',
        '.position-listing a',
        '.opening-listing a',
        '.vacancy-listing a',
        # Modern SPA patterns
        '[class*="JobCard"] a',
        '[class*="job-card"] a',
        '[class*="jobCard"] a',
        '[class*="JobItem"] a',
        '[class*="job-item"] a',
        '[class*="jobItem"] a',
        '[class*="JobResult"] a',
        '[class*="job-result"] a',
        '[class*="jobResult"] a',
        '[class*="PositionCard"] a',
        '[class*="position-card"] a',
        '[class*="positionCard"] a',
        # List items
        '[class*="job"] a[href]',
        '[class*="Job"] a[href]',
        '[class*="position"] a[href]',
        '[class*="Position"] a[href]',
        '[class*="career"] a[href]',
        '[class*="Career"] a[href]',
        # Article/li/div based
        'article a[href*="job"]',
        'article[class*="job"] a',
        'li[class*="job"] a',
        'li[class*="Job"] a',
        'div[class*="search-result"] a',
        'div[class*="SearchResult"] a',
        'tr[class*="job"] a',
        # Title-based
        '.job-title a',
        'a.job-link',
        'a.job-title',
        'a.position-link',
        'h2 a[href*="job"]',
        'h3 a[href*="job"]',
        'h4 a[href*="job"]',
        # Role/listitem patterns
        '[role="listitem"] a[href*="job"]',
        '[role="listitem"] a[href*="career"]',
        '[role="article"] a',
        # Table-based job listings
        'table a[href*="job"]',
        'table a[href*="career"]',
        # Grid patterns
        '[class*="grid"] a[href*="job"]',
        '[class*="Grid"] a[href*="job"]',
    ]


    # Selectors for pagination elements
    PAGINATION_SELECTORS = [
        # Next page buttons
        'a[aria-label*="next" i]',
        'button[aria-label*="next" i]',
        'a[title*="next" i]',
        'button[title*="next" i]',
        'a.next',
        'button.next',
        '.pagination a.next',
        '.pagination button.next',
        'a[rel="next"]',
        '[class*="next-page"]',
        '[class*="nextPage"]',
        'a:has-text("Next")',
        'button:has-text("Next")',
        'a:has-text("→")',
        'button:has-text("→")',
        'a:has-text(">")',
        'button:has-text(">")',
        # Load more buttons
        'button:has-text("Load More")',
        'button:has-text("Show More")',
        'button:has-text("View More")',
        'a:has-text("Load More")',
        '[class*="load-more"]',
        '[class*="loadMore"]',
        '[class*="show-more"]',
        '[class*="showMore"]',
    ]

    # Selectors to exclude (non-job links)
    EXCLUDE_PATTERNS = [
        r'/login',
        r'/sign-in',
        r'/signin',
        r'/register',
        r'/apply',
        r'/saved',
        r'/alerts',
        r'/profile',
        r'/account',
        r'/privacy',
        r'/terms',
        r'/cookie',
        r'/accessibility',
        r'#',  # Hash links
        r'javascript:',
        r'mailto:',
    ]

    def __init__(self, company_name: str, career_url: str):
        super().__init__(company_name, career_url)
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.seen_urls: set = set()

    async def scrape(self) -> List[Job]:
        """
        Scrape all job listings from the career page with pagination.

        Returns:
            List of Job objects matching our keywords.
        """
        all_jobs: List[Job] = []

        async with async_playwright() as p:
            # Launch browser with realistic settings
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
                await self._navigate_with_retry(self.career_url)
                
                # Wait extra time for SPAs to render
                await asyncio.sleep(3)
                
                # Try scroll-to-load for lazy-loading content
                await self._scroll_to_load_content()

                # Scrape current page and handle pagination
                page_count = 0
                while page_count < MAX_PAGES_PER_COMPANY:
                    page_count += 1
                    print(f"  Scraping page {page_count} for {self.company_name}...")

                    # Extract jobs from current page
                    page_jobs = await self._extract_jobs_from_page()
                    all_jobs.extend(page_jobs)

                    # Try to go to next page
                    has_more = await self._handle_pagination()
                    if not has_more:
                        print(f"  No more pages found for {self.company_name}")
                        break

                    # Delay between pages
                    await asyncio.sleep(SCRAPE_DELAY_SECONDS)

            except Exception as e:
                print(f"  Error scraping {self.company_name}: {e}")

            finally:
                await self.browser.close()


        # Filter jobs by keywords
        filtered_jobs = []
        for job in all_jobs:
            matched_keywords = self.matches_keywords(job.job_title)
            if matched_keywords:
                job.keywords_matched = matched_keywords
                filtered_jobs.append(job)

        print(f"  Found {len(filtered_jobs)} matching jobs for {self.company_name} (out of {len(all_jobs)} total)")
        return filtered_jobs

    async def _navigate_with_retry(self, url: str) -> bool:
        """Navigate to URL with retry logic."""
        for attempt in range(MAX_RETRIES):
            try:
                await self.page.goto(
                    url,
                    wait_until='networkidle',
                    timeout=PAGE_LOAD_TIMEOUT_MS,
                )
                # Wait a bit for any dynamic content
                await asyncio.sleep(1)
                return True
            except PlaywrightTimeout:
                if attempt < MAX_RETRIES - 1:
                    print(f"  Timeout on attempt {attempt + 1}, retrying...")
                    await asyncio.sleep(2)
                else:
                    print(f"  Failed to load {url} after {MAX_RETRIES} attempts")
                    raise
        return False

    async def _scroll_to_load_content(self):
        """Scroll down to trigger lazy-loading of job listings."""
        try:
            # Get initial height
            initial_height = await self.page.evaluate('document.body.scrollHeight')
            
            # Scroll down in steps to trigger lazy loading
            for i in range(5):
                await self.page.evaluate('window.scrollBy(0, window.innerHeight)')
                await asyncio.sleep(0.5)
            
            # Wait for any lazy content to load
            await asyncio.sleep(1)
            
            # Scroll back to top
            await self.page.evaluate('window.scrollTo(0, 0)')
            await asyncio.sleep(0.5)
            
            # Check if new content loaded
            final_height = await self.page.evaluate('document.body.scrollHeight')
            if final_height > initial_height:
                print(f"    Triggered lazy loading: {final_height - initial_height}px new content")
        except Exception as e:
            # Not critical, continue anyway
            pass


    async def _extract_jobs_from_page(self) -> List[Job]:
        """Extract all job listings from the current page."""
        jobs: List[Job] = []

        # Try each job listing selector
        for selector in self.JOB_LISTING_SELECTORS:
            try:
                elements = await self.page.query_selector_all(selector)
                if elements:
                    for element in elements:
                        job = await self._parse_job_element(element)
                        if job and job.job_url not in self.seen_urls:
                            self.seen_urls.add(job.job_url)
                            jobs.append(job)

                    if jobs:
                        # Found jobs with this selector, stop trying others
                        break
            except Exception:
                continue

        # If no jobs found with specific selectors, try a more generic approach
        if not jobs:
            jobs = await self._extract_jobs_generic()

        return jobs

    async def _parse_job_element(self, element) -> Optional[Job]:
        """Parse a job element to extract job details."""
        try:
            # Get the link URL
            href = await element.get_attribute('href')
            if not href or self._should_exclude_url(href):
                return None

            job_url = self.normalize_url(href)

            # Get the job title (link text or nearby title element)
            title = await self._extract_title(element)
            if not title:
                return None

            # Try to extract location if available
            location = await self._extract_location(element)

            # Generate unique job ID
            job_id = self.generate_job_id(job_url, title)

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

    async def _extract_title(self, element) -> str:
        """Extract job title from element or its context."""
        # Try getting text content of the link
        text = await element.text_content()
        if text:
            text = self.clean_text(text)
            if len(text) > 5 and len(text) < 200:
                return text

        # Try aria-label
        aria_label = await element.get_attribute('aria-label')
        if aria_label:
            return self.clean_text(aria_label)

        # Try title attribute
        title_attr = await element.get_attribute('title')
        if title_attr:
            return self.clean_text(title_attr)

        return ""

    async def _extract_location(self, element) -> str:
        """Try to extract location information near the job element."""
        try:
            # Look for location in parent element
            parent = await element.evaluate_handle('el => el.parentElement')
            if parent:
                parent_text = await parent.text_content()
                # Try to find location patterns
                location_patterns = [
                    r'(?:Location[:\s]*)([\w\s,]+)',
                    r'([\w\s]+,\s*[A-Z]{2})',  # City, State format
                    r'(Remote|Hybrid|On-?site)',
                ]
                for pattern in location_patterns:
                    match = re.search(pattern, parent_text or '', re.IGNORECASE)
                    if match:
                        return self.clean_text(match.group(1))
        except Exception:
            pass
        return ""

    async def _extract_jobs_generic(self) -> List[Job]:
        """
        Fallback: Extract jobs by looking for all links that look like job postings.
        """
        jobs: List[Job] = []

        try:
            # Get all links on the page
            links = await self.page.query_selector_all('a[href]')

            for link in links:
                href = await link.get_attribute('href')
                if not href or self._should_exclude_url(href):
                    continue

                # Check if URL looks like a job posting
                if not self._looks_like_job_url(href):
                    continue

                text = await link.text_content()
                text = self.clean_text(text) if text else ""

                # Skip if text is too short or too long
                if not text or len(text) < 5 or len(text) > 200:
                    continue

                job_url = self.normalize_url(href)
                if job_url in self.seen_urls:
                    continue

                self.seen_urls.add(job_url)
                job_id = self.generate_job_id(job_url, text)

                jobs.append(Job(
                    job_id=job_id,
                    job_title=text,
                    job_url=job_url,
                    company_name=self.company_name,
                    company_career_url=self.career_url,
                ))

        except Exception as e:
            print(f"    Generic extraction error: {e}")

        return jobs

    def _should_exclude_url(self, url: str) -> bool:
        """Check if URL should be excluded based on patterns."""
        url_lower = url.lower()
        for pattern in self.EXCLUDE_PATTERNS:
            if re.search(pattern, url_lower):
                return True
        return False

    def _looks_like_job_url(self, url: str) -> bool:
        """Check if URL looks like a job posting URL."""
        url_lower = url.lower()
        job_patterns = [
            r'/job[s]?/',
            r'/job[s]?$',
            r'/position[s]?/',
            r'/career[s]?/',
            r'/opening[s]?/',
            r'/requisition/',
            r'/vacancy/',
            r'/posting/',
            r'/opportunity/',
            r'/role[s]?/',
            r'job[-_]?id=',
            r'requisition[-_]?id=',
            r'position[-_]?id=',
            r'/apply/',
            r'#job',  # Hash-based URLs like Plaid
            r'#role',
            r'#opening',
            r'/details/',
            r'/view/',
        ]
        return any(re.search(p, url_lower) for p in job_patterns)


    async def _handle_pagination(self) -> bool:
        """
        Try to navigate to the next page of results.

        Returns:
            True if successfully navigated to next page, False otherwise.
        """
        # First, try clicking a "next" or "load more" button
        for selector in self.PAGINATION_SELECTORS:
            try:
                button = await self.page.query_selector(selector)
                if button:
                    is_disabled = await button.get_attribute('disabled')
                    aria_disabled = await button.get_attribute('aria-disabled')

                    if is_disabled or aria_disabled == 'true':
                        continue

                    # Check if visible
                    is_visible = await button.is_visible()
                    if not is_visible:
                        continue

                    # Click the button
                    await button.click()

                    # Wait for navigation or content load
                    try:
                        await self.page.wait_for_load_state('networkidle', timeout=10000)
                    except PlaywrightTimeout:
                        pass

                    await asyncio.sleep(1)  # Extra wait for dynamic content
                    return True

            except Exception:
                continue

        # Try URL-based pagination (incrementing page number)
        return await self._try_url_pagination()

    async def _try_url_pagination(self) -> bool:
        """Try to paginate by modifying the URL."""
        current_url = self.page.url
        parsed = urlparse(current_url)
        query_params = parse_qs(parsed.query)

        # Common pagination parameter names
        page_params = ['page', 'p', 'pg', 'pageNumber', 'start', 'offset']

        for param in page_params:
            if param in query_params:
                try:
                    current_page = int(query_params[param][0])
                    query_params[param] = [str(current_page + 1)]

                    new_query = urlencode(query_params, doseq=True)
                    new_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{new_query}"

                    if new_url != current_url:
                        await self._navigate_with_retry(new_url)
                        return True
                except (ValueError, IndexError):
                    continue

        return False
