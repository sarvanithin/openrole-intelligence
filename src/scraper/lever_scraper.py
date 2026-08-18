"""
Lever career site scraper.
Uses the Lever Postings API for reliable JSON data extraction.

Lever URL patterns:
- jobs.lever.co/{company}
- jobs.lever.co/{company}/{posting-slug}
- API: api.lever.co/v0/postings/{company}
"""
import asyncio
import re
from typing import List, Optional
from urllib.parse import urlparse

import requests
from playwright.async_api import async_playwright, Page, Browser

from config import PAGE_LOAD_TIMEOUT_MS
from scraper.base_scraper import BaseScraper, Job


class LeverScraper(BaseScraper):
    """
    Scraper for Lever career sites.
    Prioritizes API calls for reliability, falls back to browser scraping.
    """

    API_BASE = "https://api.lever.co/v0/postings"
    
    # Lever-specific selectors for fallback
    JOB_SELECTORS = [
        '.posting a.posting-btn-submit',
        '.posting-title',
        'a[href*="lever.co"][href*="/"]',
        '.posting-apply a',
        '.content-wrapper a',
        'div[data-qa="posting-name"]',
    ]

    def __init__(self, company_name: str, career_url: str):
        super().__init__(company_name, career_url)
        self.company_slug = self._extract_company_slug(career_url)

    def _extract_company_slug(self, url: str) -> Optional[str]:
        """Extract the Lever company slug from URL."""
        patterns = [
            r'jobs\.lever\.co/([^/\?]+)',
            r'lever\.co/([^/\?]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    async def scrape(self) -> List[Job]:
        """Scrape jobs using API first, then fallback to browser."""
        # Try API first (most reliable)
        if self.company_slug:
            jobs = await self._scrape_via_api()
            if jobs:
                return jobs
            print(f"  [Lever] API failed, falling back to browser for {self.company_name}")

        # Fallback to browser scraping
        return await self._scrape_via_browser()

    async def _scrape_via_api(self) -> List[Job]:
        """Scrape using Lever Postings API."""
        all_jobs: List[Job] = []
        
        try:
            url = f"{self.API_BASE}/{self.company_slug}"
            print(f"  [Lever] Fetching from API: {url}")
            
            response = requests.get(url, timeout=30)
            if response.status_code != 200:
                print(f"  [Lever] API returned {response.status_code}")
                return []
            
            postings = response.json()
            
            # Lever API returns a direct list
            if not isinstance(postings, list):
                postings = postings.get("postings", []) if isinstance(postings, dict) else []
            
            print(f"  [Lever] API returned {len(postings)} postings")
            
            for posting in postings:
                title = posting.get("text", "")
                
                # Check if matches keywords
                matched_keywords = self.matches_keywords(title)
                if not matched_keywords:
                    continue
                
                job_id = f"LV_{posting.get('id', '')}"
                job_url = posting.get("hostedUrl", "") or posting.get("applyUrl", "")
                
                # Extract location
                categories = posting.get("categories", {})
                location = categories.get("location", "") if isinstance(categories, dict) else ""
                
                # Extract posted date from createdAt timestamp
                created_at = posting.get("createdAt")
                posted_date = ""
                if created_at:
                    # createdAt is in milliseconds since epoch
                    try:
                        from datetime import datetime
                        dt = datetime.fromtimestamp(created_at / 1000)
                        posted_date = dt.strftime("%Y-%m-%d")
                    except:
                        posted_date = ""
                
                all_jobs.append(Job(
                    job_id=job_id,
                    job_title=title,
                    job_url=job_url,
                    company_name=self.company_name,
                    company_career_url=self.career_url,
                    location=location,
                    posted_date=posted_date,
                    keywords_matched=matched_keywords,
                ))
            
            print(f"  [Lever] Found {len(all_jobs)} matching jobs via API")
            return all_jobs
            
        except Exception as e:
            print(f"  [Lever] API error: {e}")
            return []

    async def _scrape_via_browser(self) -> List[Job]:
        """Fallback browser scraping for Lever."""
        all_jobs: List[Job] = []
        seen_urls: set = set()

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=['--disable-blink-features=AutomationControlled']
            )
            
            context = await browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            
            page = await context.new_page()

            try:
                await page.goto(self.career_url, wait_until='networkidle', timeout=PAGE_LOAD_TIMEOUT_MS)
                await asyncio.sleep(3)

                # Look for job postings using content wrapper
                postings = await page.query_selector_all('.posting')
                
                for posting in postings:
                    # Get title
                    title_el = await posting.query_selector('h5, .posting-name, [data-qa="posting-name"]')
                    if not title_el:
                        continue
                    
                    title = await title_el.text_content()
                    title = self.clean_text(title) if title else ""
                    
                    if not title:
                        continue
                    
                    # Check keywords
                    matched_keywords = self.matches_keywords(title)
                    if not matched_keywords:
                        continue
                    
                    # Get URL
                    link = await posting.query_selector('a.posting-btn-submit, a')
                    if link:
                        href = await link.get_attribute('href')
                        if href and href not in seen_urls:
                            job_url = self.normalize_url(href)
                            seen_urls.add(job_url)
                            
                            job_id = self._extract_job_id(job_url)
                            
                            # Get location
                            loc_el = await posting.query_selector('.location, .posting-categories')
                            location = ""
                            if loc_el:
                                location = await loc_el.text_content()
                                location = self.clean_text(location) if location else ""
                            
                            all_jobs.append(Job(
                                job_id=job_id,
                                job_title=title,
                                job_url=job_url,
                                company_name=self.company_name,
                                company_career_url=self.career_url,
                                location=location,
                                keywords_matched=matched_keywords,
                            ))

            except Exception as e:
                print(f"  [Lever] Browser error: {e}")
            finally:
                await browser.close()

        print(f"  [Lever] Found {len(all_jobs)} matching jobs via browser")
        return all_jobs

    def _extract_job_id(self, url: str) -> str:
        """Extract Lever job ID from URL."""
        # Lever uses UUID-style IDs
        match = re.search(r'/([a-f0-9-]{36})', url)
        if match:
            return f"LV_{match.group(1)}"
        # Or slug-based
        match = re.search(r'lever\.co/[^/]+/([^/\?]+)', url)
        if match:
            return f"LV_{match.group(1)}"
        return self.generate_job_id(url, "")
