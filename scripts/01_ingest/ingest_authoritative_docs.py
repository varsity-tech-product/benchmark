#!/usr/bin/env python3
"""
Scrape educational content from authoritative financial sources.

Sources:
- SEC Investor.gov
- CFPB Consumer Resources
- FINRA Investor Education

Respects robots.txt and implements rate limiting.
"""

import argparse
import hashlib
import json
import sys
import time
import urllib.robotparser
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

# Base paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
RAW_DATA_DIR = PROJECT_ROOT / "data" / "00_raw"

# Rate limiting
REQUEST_DELAY = 1.0  # seconds between requests

# User agent
USER_AGENT = "QuantTutorBenchmark/1.0 (Educational research; contact@example.com)"


class RespectfulScraper:
    """Web scraper that respects robots.txt and implements rate limiting."""

    def __init__(self, base_url: str, output_dir: Path, delay: float = REQUEST_DELAY):
        self.base_url = base_url
        self.output_dir = output_dir
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.robot_parser = urllib.robotparser.RobotFileParser()
        self.last_request_time = 0
        self.visited_urls: set[str] = set()
        self.metadata: list[dict] = []

        # Load robots.txt
        self._load_robots_txt()

        # Ensure output directory exists
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _load_robots_txt(self):
        """Load and parse robots.txt for the domain."""
        parsed = urlparse(self.base_url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

        try:
            self.robot_parser.set_url(robots_url)
            self.robot_parser.read()
            print(f"Loaded robots.txt from {robots_url}")
        except Exception as e:
            print(f"Warning: Could not load robots.txt: {e}")

    def can_fetch(self, url: str) -> bool:
        """Check if URL is allowed by robots.txt."""
        return self.robot_parser.can_fetch(USER_AGENT, url)

    def _rate_limit(self):
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self.last_request_time = time.time()

    def fetch_page(self, url: str) -> Optional[str]:
        """
        Fetch a page respecting robots.txt and rate limits.

        Returns:
            HTML content or None if blocked/error
        """
        if url in self.visited_urls:
            return None

        if not self.can_fetch(url):
            print(f"  Blocked by robots.txt: {url}")
            return None

        self._rate_limit()

        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            self.visited_urls.add(url)
            return response.text
        except requests.RequestException as e:
            print(f"  Error fetching {url}: {e}")
            return None

    def save_page(self, url: str, html: str, metadata: dict = None) -> Path:
        """
        Save HTML content to file.

        Returns:
            Path to saved file
        """
        # Create filename from URL hash
        url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
        parsed = urlparse(url)
        path_slug = parsed.path.strip("/").replace("/", "_")[:50] or "index"
        filename = f"{path_slug}_{url_hash}.html"

        filepath = self.output_dir / filename

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)

        # Track metadata
        page_meta = {
            "url": url,
            "filename": filename,
            "title": self._extract_title(html),
            **(metadata or {}),
        }
        self.metadata.append(page_meta)

        return filepath

    def _extract_title(self, html: str) -> str:
        """Extract page title from HTML."""
        soup = BeautifulSoup(html, "lxml")
        title_tag = soup.find("title")
        return title_tag.get_text(strip=True) if title_tag else ""

    def save_metadata(self):
        """Save metadata index file."""
        metadata_path = self.output_dir / "metadata.json"
        with open(metadata_path, "w") as f:
            json.dump(self.metadata, f, indent=2)
        print(f"Saved metadata to {metadata_path}")


def scrape_sec_investor_gov():
    """
    Scrape educational content from SEC's Investor.gov.

    Focus on investor education articles and guides.
    """
    print("\n" + "=" * 60)
    print("Scraping SEC Investor.gov...")
    print("=" * 60)

    output_dir = RAW_DATA_DIR / "sec"
    scraper = RespectfulScraper("https://www.investor.gov", output_dir)

    # Key educational pages to scrape
    pages = [
        "/introduction-investing",
        "/introduction-investing/investing-basics",
        "/introduction-investing/investing-basics/what-is-risk",
        "/introduction-investing/investing-basics/investment-products",
        "/introduction-investing/general-resources/glossary",
        "/additional-resources/general-resources/publications/ask-save-invest",
        "/protect-your-investments",
        "/protect-your-investments/fraud",
        "/research-before-you-invest",
        "/research-before-you-invest/research",
        "/financial-tools-calculators/calculators/compound-interest-calculator",
    ]

    count = 0
    for path in tqdm(pages, desc="Fetching pages"):
        url = urljoin(scraper.base_url, path)
        html = scraper.fetch_page(url)
        if html:
            scraper.save_page(url, html, {"section": "investor_education"})
            count += 1

    scraper.save_metadata()
    print(f"✓ Scraped {count} pages from SEC Investor.gov")
    return count > 0


def scrape_cfpb():
    """
    Scrape consumer resources from CFPB.

    Focus on consumer finance education content.
    """
    print("\n" + "=" * 60)
    print("Scraping CFPB Consumer Resources...")
    print("=" * 60)

    output_dir = RAW_DATA_DIR / "cfpb"
    scraper = RespectfulScraper("https://www.consumerfinance.gov", output_dir)

    # Key educational pages
    pages = [
        "/consumer-tools/money-as-you-grow/",
        "/consumer-tools/educator-tools/",
        "/ask-cfpb/",
        "/consumer-tools/debt-collection/",
        "/consumer-tools/credit-reports-and-scores/",
        "/consumer-tools/mortgages/",
        "/consumer-tools/auto-loans/",
        "/consumer-tools/credit-cards/",
        "/consumer-tools/student-loans/",
        "/consumer-tools/bank-accounts/",
        "/consumer-tools/money-transfers/",
        "/consumer-tools/fraud/",
    ]

    count = 0
    for path in tqdm(pages, desc="Fetching pages"):
        url = urljoin(scraper.base_url, path)
        html = scraper.fetch_page(url)
        if html:
            scraper.save_page(url, html, {"section": "consumer_tools"})
            count += 1

    scraper.save_metadata()
    print(f"✓ Scraped {count} pages from CFPB")
    return count > 0


def scrape_finra():
    """
    Scrape investor education from FINRA.

    Focus on investor education and alerts.
    """
    print("\n" + "=" * 60)
    print("Scraping FINRA Investor Education...")
    print("=" * 60)

    output_dir = RAW_DATA_DIR / "finra"
    scraper = RespectfulScraper("https://www.finra.org", output_dir)

    # Key educational pages
    pages = [
        "/investors",
        "/investors/learn-to-invest",
        "/investors/learn-to-invest/types-investments",
        "/investors/learn-to-invest/types-investments/stocks",
        "/investors/learn-to-invest/types-investments/bonds",
        "/investors/learn-to-invest/types-investments/mutual-funds",
        "/investors/learn-to-invest/types-investments/exchange-traded-funds-etfs",
        "/investors/learn-to-invest/key-investing-concepts",
        "/investors/learn-to-invest/key-investing-concepts/asset-allocation",
        "/investors/learn-to-invest/key-investing-concepts/diversification",
        "/investors/protect-your-money",
        "/investors/protect-your-money/fraud",
        "/investors/tools-and-calculators",
        "/investors/alerts",
    ]

    count = 0
    for path in tqdm(pages, desc="Fetching pages"):
        url = urljoin(scraper.base_url, path)
        html = scraper.fetch_page(url)
        if html:
            scraper.save_page(url, html, {"section": "investor_education"})
            count += 1

    scraper.save_metadata()
    print(f"✓ Scraped {count} pages from FINRA")
    return count > 0


def main():
    parser = argparse.ArgumentParser(
        description="Scrape authoritative financial education sources"
    )
    parser.add_argument(
        "--source",
        choices=["sec", "cfpb", "finra", "all"],
        default="all",
        help="Which source to scrape (default: all)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=REQUEST_DELAY,
        help=f"Delay between requests in seconds (default: {REQUEST_DELAY})",
    )

    args = parser.parse_args()

    results = {}

    if args.source in ["sec", "all"]:
        results["sec"] = scrape_sec_investor_gov()

    if args.source in ["cfpb", "all"]:
        results["cfpb"] = scrape_cfpb()

    if args.source in ["finra", "all"]:
        results["finra"] = scrape_finra()

    # Summary
    print("\n" + "=" * 60)
    print("Scraping Summary")
    print("=" * 60)
    for source, success in results.items():
        status = "✓ Success" if success else "✗ Failed"
        print(f"  {source}: {status}")

    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
