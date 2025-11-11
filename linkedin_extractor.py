#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Universal LinkedIn Profile Extractor
Extracts founder profiles using Playwright and AI
"""

import pandas as pd
import asyncio
import json
import sys
import time
from datetime import datetime, timedelta

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

from playwright.async_api import async_playwright
from anthropic import Anthropic

# Cache expiration: 3 months
CACHE_EXPIRATION_DAYS = 90

def is_cache_expired(timestamp_str):
    """Check if cache entry is older than 3 months"""
    try:
        cache_time = datetime.fromisoformat(timestamp_str)
        age = datetime.now() - cache_time
        return age > timedelta(days=CACHE_EXPIRATION_DAYS)
    except:
        # If timestamp is invalid or missing, consider it expired
        return True

async def extract_with_ai(page_text, linkedin_url, anthropic_client):
    """Use Claude to extract structured data from LinkedIn profile"""

    prompt = f"""You are viewing a LinkedIn profile page. Extract information from this text content.

TEXT FROM LINKEDIN PROFILE:
{page_text[:20000]}

YOUR TASK: Extract the person's professional background information.

Look for these sections on LinkedIn:
- EDUCATION section: Universities, degrees, years attended
- EXPERIENCE section: Job titles, company names, employment duration
- SKILLS section: List of professional skills
- ABOUT/HEADLINE: Professional summary or headline at top of profile

Return ONLY this JSON format (no markdown, no explanation, no code blocks):
{{"education": [{{"school": "name", "degree": "degree type", "years": "2020-2024"}}], "experience": [{{"company": "name", "title": "job title", "duration": "2 years", "description": "what they did"}}], "skills": ["skill1", "skill2", "skill3"], "about": "professional headline"}}

CRITICAL RULES:
1. Extract REAL data you see in the text
2. Education: Look for university names, degree types, graduation years
3. Experience: Look for company names, job titles, years worked
4. Skills: Look for listed skills (usually many skills listed)
5. About: The headline or summary at top of profile
6. If you cannot find data for a section, use empty array [] or empty string ""
7. Return ONLY the JSON object, nothing else

Extract now:"""

    try:
        time.sleep(0.5)
        message = anthropic_client.messages.create(
            model='claude-sonnet-4-5-20250929',
            max_tokens=2000,
            temperature=0,
            messages=[{"role": "user", "content": prompt}]
        )

        text = message.content[0].text.strip()

        # Try to find JSON in the response
        start_idx = text.find('{')
        end_idx = text.rfind('}') + 1

        if start_idx == -1 or end_idx <= start_idx:
            print(f"      ⚠️  No JSON found in AI response")
            return {"education": [], "experience": [], "skills": [], "about": ""}

        json_str = text[start_idx:end_idx]
        data = json.loads(json_str)

        # Validate structure
        if not isinstance(data.get('education'), list):
            data['education'] = []
        if not isinstance(data.get('experience'), list):
            data['experience'] = []
        if not isinstance(data.get('skills'), list):
            data['skills'] = []
        if not isinstance(data.get('about'), str):
            data['about'] = ""

        return data

    except json.JSONDecodeError as e:
        print(f"      ⚠️  JSON parse error: {str(e)}")
        return {"education": [], "experience": [], "skills": [], "about": ""}
    except Exception as e:
        print(f"      ⚠️  AI extraction error: {str(e)}")
        return {"education": [], "experience": [], "skills": [], "about": ""}

async def extract_profile(page, linkedin_url, cache, anthropic_client, save_cache_func, max_retries=3):
    """Extract a single LinkedIn profile with retry logic"""

    # Check cache and timestamp
    if linkedin_url in cache:
        cache_entry = cache[linkedin_url]

        # Handle old cache format (no timestamp) - treat as expired
        if not isinstance(cache_entry, dict) or 'timestamp' not in cache_entry:
            print(f"      ⚠️  Cache entry outdated (no timestamp), re-extracting...")
        elif is_cache_expired(cache_entry['timestamp']):
            print(f"      ⚠️  Cache expired (>3 months old), re-extracting...")
        else:
            # Cache is valid, use it
            cache_age_days = (datetime.now() - datetime.fromisoformat(cache_entry['timestamp'])).days
            print(f"      ✓ Using cached data ({cache_age_days} days old)")
            return cache_entry['data']

    # Retry loop
    for attempt in range(max_retries):
        try:
            if attempt > 0:
                wait_time = 5 * (2 ** attempt)  # Exponential backoff: 10s, 20s, 40s
                print(f"      🔄 Retry {attempt + 1}/{max_retries} after {wait_time}s...")
                await asyncio.sleep(wait_time)

            print(f"      → Navigating to profile...")

            # Increased timeout to 60s and added retry-friendly settings
            await page.goto(
                linkedin_url,
                wait_until='domcontentloaded',
                timeout=60000  # Increased from 30s to 60s
            )

            # Add visual indicator - green border to show Todor Ultra Fast is working
            await page.evaluate("""() => {
                document.body.style.border = '8px solid #00ff00';
                document.body.style.boxShadow = '0 0 30px #00ff00';
            }""")

            # Wait a bit for content to load
            await asyncio.sleep(3)

            # Scroll down to load lazy-loaded content (visibly!)
            print(f"      → Scrolling to load all sections...")
            for i in range(4):
                await page.evaluate("window.scrollBy(0, 1000)")
                await asyncio.sleep(1.5)

            # Get visible text from page
            print(f"      → Getting page text...")
            page_text = await page.evaluate("""() => {
                return document.body.innerText;
            }""")

            # Extract with AI
            print(f"      → Extracting with AI...")
            data = await extract_with_ai(page_text, linkedin_url, anthropic_client)

            # Cache it with timestamp
            cache[linkedin_url] = {
                'timestamp': datetime.now().isoformat(),
                'data': data
            }
            save_cache_func(cache)

            return data

        except Exception as e:
            error_msg = str(e)

            # Check if it's a timeout or abort error
            if 'Timeout' in error_msg or 'ERR_ABORTED' in error_msg:
                print(f"      ⚠️  Error: {error_msg}")

                # If not last attempt, will retry
                if attempt < max_retries - 1:
                    continue
                else:
                    print(f"      ❌ Failed after {max_retries} attempts - skipping profile")
                    return None
            else:
                # For other errors, fail immediately
                print(f"      ❌ Unexpected error: {error_msg}")
                return None

    return None

async def extract_all_profiles(input_file, output_file, cache_file, config, num_companies):
    """Main extraction function"""

    # Initialize Anthropic client
    anthropic_client = Anthropic(api_key=config['anthropic_api_key'])

    # Load cache
    def load_cache():
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}

    def save_cache(cache):
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)

    cache = load_cache()
    print(f"   📦 Cache contains: {len(cache)} profiles")

    # Load data - detect file type and read accordingly
    import os
    file_extension = os.path.splitext(input_file)[1].lower()
    if file_extension == '.csv':
        df = pd.read_csv(input_file)
    elif file_extension in ['.xlsx', '.xls']:
        df = pd.read_excel(input_file)
    else:
        raise ValueError(f"Unsupported file type: {file_extension}")

    # Keep product companies AND empty/uncertain ones (filter out service companies)
    df_product = df[(df['Product Company'] == 'product_company') | (df['Product Company'].isna())].copy()
    df_extract = df_product.head(num_companies).copy()

    # Initialize columns
    df_extract['linkedin_education'] = ''
    df_extract['linkedin_experience'] = ''
    df_extract['linkedin_skills'] = ''
    df_extract['linkedin_about'] = ''

    print(f"\n🌐 Opening Chrome...")
    print(f"   You will have {config['linkedin_login_timeout']} seconds to log into LinkedIn")

    async with async_playwright() as p:
        # Launch browser with visual enhancements
        browser = await p.chromium.launch(
            headless=False,
            slow_mo=300,  # Slow down actions by 300ms for visibility
            args=['--start-maximized']
        )

        context = await browser.new_context(
            viewport=None,
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )

        page = await context.new_page()

        # Go to LinkedIn
        await page.goto('https://www.linkedin.com')

        print(f"\n⏳ You have {config['linkedin_login_timeout']} SECONDS to log into LinkedIn...")
        print("   Log into LinkedIn in the browser window")
        print(f"   After {config['linkedin_login_timeout']} seconds, extraction will start automatically")
        print("\n   Waiting", end="", flush=True)

        # Wait for login
        for i in range(config['linkedin_login_timeout']):
            await asyncio.sleep(1)
            if i % 10 == 0:
                print(".", end="", flush=True)

        print(" Done!")
        print("\n✅ Starting extraction...")
        print("\n" + "=" * 80)
        print("EXTRACTING PROFILES")
        print("=" * 80 + "\n")

        # Track statistics
        stats = {'success': 0, 'failed': 0, 'cached': 0, 'skipped': 0}

        # Extract each profile
        for counter, (idx, row) in enumerate(df_extract.iterrows(), start=1):
            company_name = row['Company Name']
            founder_name = f"{row['First Name']} {row['Last Name']}"
            linkedin_url = row['LinkedIn']

            print(f"\n[{counter}/{len(df_extract)}] {company_name}")
            print(f"   Founder: {founder_name}")

            if pd.isna(linkedin_url) or not linkedin_url:
                print(f"      ⚠️  No LinkedIn URL, skipping")
                stats['skipped'] += 1
                continue

            print(f"      URL: {linkedin_url}")

            # Check if already extracted (from previous run)
            if pd.notna(df_extract.at[idx, 'linkedin_education']) and df_extract.at[idx, 'linkedin_education']:
                print(f"      ✓ Already extracted, skipping")
                stats['cached'] += 1
                continue

            # Extract
            data = await extract_profile(page, linkedin_url, cache, anthropic_client, save_cache)

            if data:
                df_extract.at[idx, 'linkedin_education'] = json.dumps(data.get('education', []), ensure_ascii=False)
                df_extract.at[idx, 'linkedin_experience'] = json.dumps(data.get('experience', []), ensure_ascii=False)
                df_extract.at[idx, 'linkedin_skills'] = json.dumps(data.get('skills', []), ensure_ascii=False)
                df_extract.at[idx, 'linkedin_about'] = data.get('about', '')
                stats['success'] += 1
                print(f"      ✅ Extraction successful")

                # Save progress after each successful extraction
                df_extract.to_excel(output_file, index=False)
            else:
                stats['failed'] += 1

            # Delay between profiles
            await asyncio.sleep(3)

        await browser.close()

        # Print final statistics
        print("\n" + "=" * 80)
        print("EXTRACTION STATISTICS")
        print("=" * 80)
        print(f"   ✅ Successful: {stats['success']}")
        print(f"   💾 From cache: {stats['cached']}")
        print(f"   ❌ Failed: {stats['failed']}")
        print(f"   ⏭️  Skipped (no URL): {stats['skipped']}")
        print(f"   📊 Total processed: {counter}/{len(df_extract)}")

    # Save results
    print("\n" + "=" * 80)
    print("SAVING RESULTS")
    print("=" * 80)

    df_extract.to_excel(output_file, index=False)
    print(f"\n   Extracted: {len(df_extract)} profiles")
    print(f"   Cache: {len(cache)} profiles")
