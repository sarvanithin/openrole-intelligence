"""
Plaid careers page scraper.
Handles plaid.com/careers - a React/Next.js site with unique job card structure.

Based on screenshot analysis:
- Jobs shown as cards with location on top, title below
- "See role" buttons link to job details
- Hash-based URL navigation (#search)
- Department sections (Engineering, etc.)

DEBUG VERSION - Enhanced logging to troubleshoot extraction issues.
"""
import asyncio
import re
from typing import List, Optional
from urllib.parse import urlparse

from playwright.async_api import async_playwright, Page, Browser

from config import PAGE_LOAD_TIMEOUT_MS, SCRAPE_DELAY_SECONDS
from scraper.base_scraper import BaseScraper, Job


class PlaidScraper(BaseScraper):
    """
    Specialized scraper for Plaid's careers page.
    Plaid uses a custom Next.js site with job cards and "See role" buttons.
    """

    # Plaid job link selectors - ordered by specificity
    JOB_LINK_SELECTORS = [
        # Direct job links
        'a[href*="/careers/openings/"]',
        'a[href*="/careers/"][href*="/engineering/"]',
        'a[href*="/careers/"][href*="/data/"]',
        'a[href*="/careers/"][href*="/product/"]',
        # "See role" type links
        'a:has-text("See role")',
        'a:has-text("View role")',
        'a:has-text("Apply")',
        # Generic career links
        'a[href*="/careers/"][href*="-"]',
    ]

    def __init__(self, company_name: str, career_url: str):
        super().__init__(company_name, career_url)
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.seen_urls: set = set()
        self.seen_titles: set = set()

    async def scrape(self) -> List[Job]:
        """Scrape all job listings from Plaid's careers page."""
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
                print(f"  [Plaid] Navigating to {self.career_url}")
                
                await self.page.goto(
                    self.career_url,
                    wait_until='networkidle',
                    timeout=PAGE_LOAD_TIMEOUT_MS,
                )
                
                # Wait for React to render
                print(f"  [Plaid] Waiting 8s for React to render...")
                await asyncio.sleep(8)
                
                # Handle cookie consent if present
                await self._dismiss_cookie_banner()
                
                # Scroll to load all content
                await self._scroll_to_load_all()

                # Extract jobs
                print(f"  [Plaid] Extracting jobs...")
                jobs = await self._extract_job_links()
                
                if not jobs:
                    print(f"  [Plaid] Primary extraction failed, trying deep search...")
                    jobs = await self._deep_search_for_jobs()
                
                all_jobs.extend(jobs)

            except Exception as e:
                print(f"  [Plaid] Error: {e}")

            finally:
                await self.browser.close()

        # Debug: Print extracted titles
        if all_jobs:
            print(f"  [Plaid] First 5 extracted titles:")
            for job in all_jobs[:5]:
                print(f"    - '{job.job_title}'")
        
        # Filter by keywords
        filtered_jobs = []
        for job in all_jobs:
            matched_keywords = self.matches_keywords(job.job_title)
            if matched_keywords:
                job.keywords_matched = matched_keywords
                filtered_jobs.append(job)

        print(f"  [Plaid] Found {len(filtered_jobs)} matching jobs (out of {len(all_jobs)} total)")
        return filtered_jobs

    async def _dismiss_cookie_banner(self):
        """Try to dismiss any cookie banners."""
        try:
            cookie_selectors = [
                'button:has-text("Ok")',
                'button:has-text("Accept")',
                'button:has-text("Got it")',
                '[class*="cookie"] button',
                '[class*="consent"] button',
            ]
            for sel in cookie_selectors:
                btn = await self.page.query_selector(sel)
                if btn and await btn.is_visible():
                    await btn.click()
                    await asyncio.sleep(1)
                    print(f"    Dismissed cookie banner")
                    break
        except Exception:
            pass

    async def _scroll_to_load_all(self):
        """Scroll to load all job content."""
        try:
            print(f"  [Plaid] Scrolling to load content...")
            
            for _ in range(15):
                await self.page.evaluate('window.scrollBy(0, 500)')
                await asyncio.sleep(0.3)
            
            # Scroll back to top
            await self.page.evaluate('window.scrollTo(0, 0)')
            await asyncio.sleep(1)
        except Exception:
            pass

    async def _extract_job_links(self) -> List[Job]:
        """Extract jobs by finding career links."""
        jobs: List[Job] = []
        
        for selector in self.JOB_LINK_SELECTORS:
            try:
                elements = await self.page.query_selector_all(selector)
                
                if elements:
                    print(f"    Trying selector: {selector} -> {len(elements)} elements")
                
                for element in elements:
                    job = await self._parse_job_link(element, selector)
                    if job and job.job_url not in self.seen_urls:
                        self.seen_urls.add(job.job_url)
                        jobs.append(job)
                
            except Exception as e:
                continue
        
        # Deduplicate
        unique_jobs = []
        seen = set()
        for job in jobs:
            if job.job_url not in seen:
                seen.add(job.job_url)
                unique_jobs.append(job)
        
        print(f"    Total unique jobs extracted: {len(unique_jobs)}")
        return unique_jobs

    async def _parse_job_link(self, element, selector: str) -> Optional[Job]:
        """Parse a link element to extract job info."""
        try:
            href = await element.get_attribute('href')
            if not href:
                return None
            
            job_url = self.normalize_url(href)
            
            # Must be a careers URL
            if '/careers/' not in job_url:
                return None
            
            # Skip main careers page and non-job links
            if job_url.rstrip('/').endswith('/careers'):
                return None
            if any(x in job_url.lower() for x in ['#', 'javascript:', '/apply', '/share']):
                return None
            
            # Get title - depends on what kind of link this is
            title = ""
            
            link_text = await element.text_content()
            link_text = self.clean_text(link_text) if link_text else ""
            
            # If this is a "See role" button, get title from parent/sibling
            if link_text.lower() in ['see role', 'view role', 'apply']:
                title = await self._get_title_from_card(element)
            else:
                # Link text is the title
                title = link_text
            
            # If still no title, try URL
            if not title or len(title) < 3:
                title = self._title_from_url(job_url)
            
            if not title or len(title) < 3 or len(title) > 150:
                return None
            
            # Get location
            location = await self._get_location_from_card(element)
            
            job_id = self._extract_job_id(job_url)
            
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

    async def _get_title_from_card(self, element) -> str:
        """Get job title from the card containing this element."""
        try:
            # Navigate up to find card/container
            parent = await element.evaluate_handle('''el => {
                let p = el.parentElement;
                for (let i = 0; i < 5 && p; i++) {
                    p = p.parentElement;
                }
                return p;
            }''')
            
            if parent:
                # Look for title-like elements
                title_selectors = ['h2', 'h3', 'h4', 'strong', '[class*="title"]', '[class*="name"]']
                for sel in title_selectors:
                    try:
                        title_el = await parent.query_selector(sel)
                        if title_el:
                            text = await title_el.text_content()
                            text = self.clean_text(text) if text else ""
                            if text and len(text) > 3 and text.lower() not in ['see role', 'apply']:
                                return text
                    except Exception:
                        continue
        except Exception:
            pass
        return ""

    async def _get_location_from_card(self, element) -> str:
        """Get location from the card containing this element."""
        try:
            parent = await element.evaluate_handle('el => el.closest("div, article, li")')
            if parent:
                location_indicators = ['new york', 'san francisco', 'remote', 'us', 'united states']
                
                # Try specific selectors
                for sel in ['[class*="location"]', 'span', 'p']:
                    try:
                        loc_el = await parent.query_selector(sel)
                        if loc_el:
                            text = await loc_el.text_content()
                            text = self.clean_text(text).lower() if text else ""
                            for indicator in location_indicators:
                                if indicator in text:
                                    return self.clean_text(await loc_el.text_content())
                    except Exception:
                        continue
        except Exception:
            pass
        return ""

    def _title_from_url(self, url: str) -> str:
        """Extract a readable title from URL path."""
        try:
            # Get path segments
            path = urlparse(url).path
            segments = [s for s in path.split('/') if s and s not in ['careers', 'openings']]
            if segments:
                # Convert slug to title
                slug = segments[-1]
                title = slug.replace('-', ' ').replace('_', ' ').title()
                return title
        except Exception:
            pass
        return ""

    def _extract_job_id(self, url: str) -> str:
        """Extract or generate job ID for Plaid."""
        # Try to get from URL path
        match = re.search(r'/careers/[^/]+/([^/?#]+)', url)
        if match:
            return f"PL_{match.group(1)[:30]}"
        
        match = re.search(r'/openings/([^/?#]+)', url)
        if match:
            return f"PL_{match.group(1)[:30]}"
        
        return self.generate_job_id(url, "")

    async def _deep_search_for_jobs(self) -> List[Job]:
        """Deep search: get all links and filter for career-like ones."""
        jobs: List[Job] = []
        
        try:
            # Get ALL links on page
            all_links = await self.page.query_selector_all('a[href]')
            print(f"    Deep search: Found {len(all_links)} total links")
            
            career_link_count = 0
            for link in all_links:
                href = await link.get_attribute('href')
                if not href:
                    continue
                
                href_lower = href.lower()
                
                # Only process career-like URLs
                if '/careers/' not in href_lower:
                    continue
                if href.rstrip('/').endswith('/careers'):
                    continue
                if any(x in href_lower for x in ['#', 'javascript:']):
                    continue
                
                career_link_count += 1
                
                job = await self._parse_job_link(link, "deep_search")
                if job and job.job_url not in self.seen_urls:
                    self.seen_urls.add(job.job_url)
                    jobs.append(job)
            
            print(f"    Deep search: Found {career_link_count} career links, extracted {len(jobs)} jobs")
            
        except Exception as e:
            print(f"    Deep search error: {e}")
        
        return jobs
