"""
Eightfold AI career site scraper.
Handles sites like aexp.eightfold.ai, prudential.eightfold.ai, etc.

Based on DOM inspection (AmEx):
- Job cards have data-test-id="position-card-X"
- Cards are div[role="link"] with class="position-card"
- Title is in class="position-title"
- Location is in class="position-location"
"""
import asyncio
import re
from typing import List, Optional
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright, Page, Browser

from config import (
    SCRAPE_DELAY_SECONDS,
    PAGE_LOAD_TIMEOUT_MS,
    MAX_PAGES_PER_COMPANY,
)
from scraper.base_scraper import BaseScraper, Job


class EightfoldScraper(BaseScraper):
    """
    Specialized scraper for Eightfold AI career sites.
    Uses data-test-id and class selectors from actual DOM structure.
    """

    def __init__(self, company_name: str, career_url: str):
        super().__init__(company_name, career_url)
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.seen_ids: set = set()

    async def scrape(self) -> List[Job]:
        """Scrape all job listings from Eightfold career page."""
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
                print(f"  [Eightfold] Navigating to {self.career_url}")
                
                await self.page.goto(
                    self.career_url,
                    wait_until='networkidle',
                    timeout=PAGE_LOAD_TIMEOUT_MS * 2,
                )
                
                # Wait for React to render
                print(f"  [Eightfold] Waiting 10s for React to render...")
                await asyncio.sleep(10)
                
                # Click "Show more" to expand job list if available
                await self._click_show_more()
                
                # Scroll to load all jobs
                await self._scroll_job_list()

                # Extract jobs using the correct selectors
                print(f"  [Eightfold] Extracting jobs...")
                all_jobs = await self._extract_position_cards()
                
                print(f"  [Eightfold] Total jobs extracted: {len(all_jobs)}")

            except Exception as e:
                print(f"  [Eightfold] Error: {e}")
                import traceback
                traceback.print_exc()

            finally:
                await self.browser.close()

        # Debug: Print extracted titles
        if all_jobs:
            print(f"  [Eightfold] First 5 extracted titles:")
            for job in all_jobs[:5]:
                print(f"    - '{job.job_title}'")
        
        # Filter by keywords
        filtered_jobs = []
        for job in all_jobs:
            matched_keywords = self.matches_keywords(job.job_title)
            if matched_keywords:
                job.keywords_matched = matched_keywords
                filtered_jobs.append(job)

        print(f"  [Eightfold] Found {len(filtered_jobs)} matching jobs (out of {len(all_jobs)} total)")
        return filtered_jobs

    async def _click_show_more(self):
        """Click 'Show more positions' or similar buttons."""
        try:
            show_more_selectors = [
                'button:has-text("Show more")',
                'button:has-text("Load more")',
                'button:has-text("View more")',
                'a:has-text("Show more")',
                '[data-test-id="show-more"]',
            ]
            
            for selector in show_more_selectors:
                try:
                    btn = await self.page.query_selector(selector)
                    if btn and await btn.is_visible():
                        print(f"  [Eightfold] Clicking show more button...")
                        await btn.click()
                        await asyncio.sleep(3)
                except Exception:
                    continue
        except Exception:
            pass

    async def _scroll_job_list(self):
        """Scroll within the job list container to load all jobs."""
        try:
            print(f"  [Eightfold] Scrolling job list...")
            
            # Find the job list container
            container_selectors = [
                '.position-sidebar-scroll-handler',
                'div[class*="position-list"]',
                'div[class*="positions-container"]',
                'div[class*="search-results"]',
            ]
            
            container = None
            for sel in container_selectors:
                container = await self.page.query_selector(sel)
                if container:
                    break
            
            if container:
                # Scroll inside the container 
                for _ in range(10):
                    await container.evaluate('el => el.scrollTop += 500')
                    await asyncio.sleep(0.5)
            else:
                # Fallback: scroll the page
                for _ in range(10):
                    await self.page.evaluate('window.scrollBy(0, 500)')
                    await asyncio.sleep(0.3)
            
            await asyncio.sleep(2)
            
        except Exception as e:
            print(f"  [Eightfold] Scroll error: {e}")

    async def _extract_position_cards(self) -> List[Job]:
        """Extract jobs from position cards using data-test-id."""
        jobs: List[Job] = []
        
        # Primary approach: Find cards by data-test-id pattern
        # Cards are numbered: position-card-0, position-card-1, etc.
        card_index = 0
        while card_index < 500:  # Max 500 to prevent infinite loop
            selector = f'[data-test-id="position-card-{card_index}"]'
            card = await self.page.query_selector(selector)
            
            if not card:
                if card_index == 0:
                    # No cards found with data-test-id, try fallback
                    print(f"    No data-test-id cards found, trying fallback selectors...")
                    return await self._extract_fallback()
                break
            
            job = await self._parse_position_card(card, card_index)
            if job:
                jobs.append(job)
            
            card_index += 1
        
        print(f"    Found {len(jobs)} position cards using data-test-id")
        return jobs

    async def _parse_position_card(self, card, index: int) -> Optional[Job]:
        """Parse a single position card."""
        try:
            # Get title from position-title class
            title_el = await card.query_selector('.position-title, [class*="position-title"], h2, h3')
            title = ""
            if title_el:
                title = await title_el.text_content()
                title = self.clean_text(title) if title else ""
            
            if not title:
                # Try getting text from card itself
                card_text = await card.text_content()
                if card_text:
                    # Get first line as title
                    lines = [l.strip() for l in card_text.split('\n') if l.strip()]
                    title = lines[0] if lines else ""
            
            if not title or len(title) < 3:
                return None
            
            # Get location
            location_el = await card.query_selector('.position-location, [class*="position-location"], [class*="location"]')
            location = ""
            if location_el:
                location = await location_el.text_content()
                location = self.clean_text(location) if location else ""
            
            # Get the link/URL - card itself might be clickable
            # On Eightfold, clicking the card navigates to job detail
            # The URL format is usually based on position ID
            href = await card.get_attribute('href')
            if not href:
                # Try finding a link inside
                link = await card.query_selector('a[href]')
                if link:
                    href = await link.get_attribute('href')
            
            if href:
                job_url = self.normalize_url(href)
            else:
                # Construct URL from current page URL and position index
                # This is a fallback
                job_url = f"{self.career_url}#position-{index}"
            
            # Generate job ID
            job_id = self._extract_job_id(job_url, title, index)
            
            if job_id in self.seen_ids:
                return None
            self.seen_ids.add(job_id)
            
            return Job(
                job_id=job_id,
                job_title=title,
                job_url=job_url,
                company_name=self.company_name,
                company_career_url=self.career_url,
                location=location,
            )
            
        except Exception as e:
            return None

    async def _extract_fallback(self) -> List[Job]:
        """Fallback extraction using class-based selectors."""
        jobs: List[Job] = []
        
        fallback_selectors = [
            '.position-card',
            '[class*="position-card"]',
            'div[role="link"][class*="position"]',
            '[class*="job-card"]',
            'a[href*="/position/"]',
        ]
        
        for selector in fallback_selectors:
            try:
                cards = await self.page.query_selector_all(selector)
                if not cards:
                    continue
                
                print(f"    Fallback selector '{selector}' found {len(cards)} elements")
                
                for i, card in enumerate(cards):
                    job = await self._parse_position_card(card, i)
                    if job:
                        jobs.append(job)
                
                if jobs:
                    break
                    
            except Exception:
                continue
        
        return jobs

    def _extract_job_id(self, url: str, title: str, index: int) -> str:
        """Extract or generate job ID."""
        # Try to get from URL
        patterns = [
            r'/position/([A-Za-z0-9_-]+)',
            r'positionId=([A-Za-z0-9_-]+)',
            r'id[=:]([A-Za-z0-9_-]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return f"EF_{match.group(1)[:20]}"
        
        # Generate from title and index
        return self.generate_job_id(url, title)
