#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Website Scraper - Pure Playwright with Screenshots
Extracts product information from company websites using browser automation
"""

import asyncio
import sys
import json
import base64
from datetime import datetime, timedelta
from urllib.parse import urlparse

from playwright.async_api import async_playwright

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

# Cache expiration: 3 months
CACHE_EXPIRATION_DAYS = 90

def is_cache_expired(timestamp_str):
    """Check if cache entry is older than 3 months"""
    try:
        cache_time = datetime.fromisoformat(timestamp_str)
        age = datetime.now() - cache_time
        return age > timedelta(days=CACHE_EXPIRATION_DAYS)
    except:
        return True

def load_website_cache(cache_file):
    """Load website scraping cache from file"""
    try:
        with open(cache_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

def save_website_cache(cache, cache_file):
    """Save website scraping cache to file"""
    with open(cache_file, 'w', encoding='utf-8') as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


async def scrape_with_playwright(url, browser):
    """
    Scrape website using Playwright with full-page screenshot
    Always captures both text content and visual context

    Returns:
        dict with 'text', 'screenshot' (bytes), 'method', 'success'
    """
    try:
        page = await browser.new_page()

        # Navigate to URL
        await page.goto(url, wait_until='networkidle', timeout=15000)

        # Wait for dynamic content to load
        await asyncio.sleep(2)

        # Extract visible text from body
        body_text = await page.inner_text('body')

        # Clean up text - remove duplicates and keep meaningful lines
        lines = [line.strip() for line in body_text.split('\n') if line.strip()]
        unique_lines = []
        seen = set()
        for line in lines:
            if line not in seen and len(line) > 10:
                unique_lines.append(line)
                seen.add(line)
                if len(unique_lines) >= 20:  # Limit to top 20 lines
                    break

        text = '\n'.join(unique_lines[:20])

        # Take full-page screenshot
        screenshot_bytes = await page.screenshot(full_page=True, type='png')

        await page.close()

        return {
            'success': True,
            'text': text,
            'screenshot': screenshot_bytes,
            'method': 'playwright',
            'char_count': len(text)
        }

    except Exception as e:
        return {
            'success': False,
            'text': '',
            'screenshot': None,
            'method': 'playwright',
            'error': str(e)
        }


async def scrape_website(url, browser=None):
    """
    Scrape website using Playwright with screenshot

    Args:
        url: Website URL to scrape
        browser: Playwright browser instance (optional, will create if needed)

    Returns:
        dict with 'text', 'screenshot' (bytes or None), 'method'
    """
    # Validate URL
    if not url or not url.startswith('http'):
        return {
            'text': '',
            'screenshot': None,
            'method': 'none',
            'error': 'Invalid URL'
        }

    print(f"      → Scraping: {urlparse(url).netloc}")

    # Create browser if not provided
    should_close_browser = False
    if browser is None:
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(headless=True)
        should_close_browser = True

    result = await scrape_with_playwright(url, browser)

    if should_close_browser:
        await browser.close()

    if result['success']:
        print(f"      ✓ Success ({result['char_count']} chars, screenshot captured)")
    else:
        print(f"      ⚠️  Failed: {result.get('error', 'Unknown error')}")

    return {
        'text': result.get('text', ''),
        'screenshot': result.get('screenshot'),
        'method': result.get('method', 'playwright')
    }


async def scrape_all_websites(urls, show_progress=True, cache_file=None):
    """
    Scrape multiple websites efficiently with shared browser and caching

    Args:
        urls: List of URLs to scrape
        show_progress: Print progress messages
        cache_file: Path to cache file for storing scraped data (optional)

    Returns:
        dict mapping URL -> scrape result
    """
    results = {}

    # Load cache if provided
    cache = load_website_cache(cache_file) if cache_file else {}
    if cache_file and cache:
        print(f"   📦 Website cache contains: {len(cache)} entries")

    # Check which URLs need scraping
    urls_to_scrape = []
    for url in urls:
        if url in cache:
            cache_entry = cache[url]
            if isinstance(cache_entry, dict) and 'timestamp' in cache_entry:
                if not is_cache_expired(cache_entry['timestamp']):
                    # Use cached data
                    cache_age_days = (datetime.now() - datetime.fromisoformat(cache_entry['timestamp'])).days
                    if show_progress:
                        print(f"\n[Cached] {url}")
                        print(f"      ✓ Using cached data ({cache_age_days} days old)")

                    # Decode screenshot from base64
                    cached_data = cache_entry['data'].copy()
                    if cached_data.get('screenshot_base64'):
                        cached_data['screenshot'] = base64.b64decode(cached_data['screenshot_base64'])
                        del cached_data['screenshot_base64']
                    else:
                        cached_data['screenshot'] = None

                    results[url] = cached_data
                    continue

        # Not in cache or expired
        urls_to_scrape.append(url)

    # Scrape remaining URLs
    if urls_to_scrape:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)

            for idx, url in enumerate(urls_to_scrape):
                # Calculate overall progress
                overall_idx = urls.index(url) + 1
                if show_progress:
                    print(f"\n[{overall_idx}/{len(urls)}] Scraping: {url}")

                result = await scrape_website(url, browser)
                results[url] = result

                # Save to cache if successful and cache_file provided
                if cache_file and result.get('text'):
                    # Encode screenshot as base64 for JSON storage
                    cache_data = result.copy()
                    if cache_data.get('screenshot'):
                        cache_data['screenshot_base64'] = base64.b64encode(cache_data['screenshot']).decode('utf-8')
                        del cache_data['screenshot']
                    else:
                        cache_data['screenshot_base64'] = None

                    cache[url] = {
                        'timestamp': datetime.now().isoformat(),
                        'data': cache_data
                    }
                    save_website_cache(cache, cache_file)

                # Small delay between requests
                await asyncio.sleep(1)

            await browser.close()

    return results


# Test function
async def test_scraper():
    """Test the scraper with a sample URL"""
    test_url = "https://huex.ai"

    print(f"Testing scraper with: {test_url}\n")
    result = await scrape_website(test_url)

    print(f"\nResult:")
    print(f"  Method: {result['method']}")
    print(f"  Text length: {len(result['text'])} chars")
    print(f"  Has screenshot: {result['screenshot'] is not None}")
    print(f"\nExtracted text preview:")
    print(result['text'][:500])


if __name__ == "__main__":
    # Run test
    asyncio.run(test_scraper())
