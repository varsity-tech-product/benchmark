"""
News full-text scraper.

Given a URL, fetches the page and extracts clean article body text.
Uses aiohttp for async concurrent fetching.
"""

import asyncio
import random
import re
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

# Browser-like user agents to avoid basic bot detection
_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

# Tags to remove before extracting text
_STRIP_TAGS = {
    "script",
    "style",
    "nav",
    "footer",
    "header",
    "aside",
    "iframe",
    "noscript",
}

# Patterns indicating ad/promo content to filter out
_AD_PATTERNS = re.compile(
    r"(免责声明|广告|推广|点击查看|立即下载|扫码关注|版权所有|Copyright)",
    re.IGNORECASE,
)

# Request timeout per page (seconds)
_TIMEOUT = 10

# Max concurrent requests
_CONCURRENCY = 10


async def fetch_article_text(url: str, session: aiohttp.ClientSession) -> Optional[str]:
    """
    Fetch a single URL and extract the main article body text.

    Args:
        url: full URL of the news article
        session: shared aiohttp session

    Returns:
        Cleaned article text, or None on failure.
    """
    headers = {"User-Agent": random.choice(_USER_AGENTS)}
    try:
        async with session.get(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=_TIMEOUT)
        ) as resp:
            if resp.status != 200:
                return None
            html = await resp.text(errors="replace")
    except Exception:
        return None

    return _extract_text(html)


def _extract_text(html: str) -> Optional[str]:
    """Parse HTML and extract clean article body text."""
    soup = BeautifulSoup(html, "lxml")

    # Remove unwanted tags
    for tag in soup.find_all(_STRIP_TAGS):
        tag.decompose()

    # Try to find the main content container
    article = (
        soup.find("article")
        or soup.find(
            "div", class_=re.compile(r"(article|content|post|body|text|detail)", re.I)
        )
        or soup.find(
            "div", id=re.compile(r"(article|content|post|body|text|detail)", re.I)
        )
    )

    if article is None:
        # Fallback: use the whole body
        article = soup.find("body")

    if article is None:
        return None

    # Extract paragraphs
    paragraphs = []
    for p in article.find_all(["p", "div"]):
        text = p.get_text(strip=True)
        if not text or len(text) < 10:
            continue
        if _AD_PATTERNS.search(text):
            continue
        paragraphs.append(text)

    if not paragraphs:
        return None

    full_text = "\n".join(paragraphs)

    # Limit to ~2000 chars to keep token budget reasonable
    if len(full_text) > 2000:
        full_text = full_text[:2000] + "..."

    return full_text


async def fetch_many(urls: list[str]) -> dict[str, Optional[str]]:
    """
    Fetch multiple URLs concurrently with rate limiting.

    Args:
        urls: list of article URLs

    Returns:
        dict mapping URL -> extracted text (or None on failure)
    """
    sem = asyncio.Semaphore(_CONCURRENCY)
    results = {}

    async with aiohttp.ClientSession() as session:

        async def _fetch_one(url: str):
            async with sem:
                # Small random delay to avoid burst
                await asyncio.sleep(random.uniform(0.1, 0.5))
                text = await fetch_article_text(url, session)
                results[url] = text

        await asyncio.gather(*[_fetch_one(u) for u in urls])

    return results
