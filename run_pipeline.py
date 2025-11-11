#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Todor Ultra Fast - Universal Startup Analysis Pipeline
Extracts LinkedIn data and analyzes founders from any country
"""

import os
import sys
import json
import base64
import pandas as pd
import asyncio
import time
from pathlib import Path
from datetime import datetime

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

# Import analysis modules
from linkedin_extractor import extract_all_profiles
from founder_analyzer import analyze_all_founders

# Get script directory
SCRIPT_DIR = Path(__file__).parent.absolute()

def load_config():
    """Load API keys from config.json"""
    config_path = SCRIPT_DIR / "config.json"
    with open(config_path, 'r') as f:
        return json.load(f)

def extract_country_from_filename(file_path):
    """
    Extract country name from filename format: country_restofname.xlsx
    Example: bulgaria_companies_2024.xlsx -> "bulgaria"
    """
    filename = os.path.basename(file_path)

    # Extract country (everything before first underscore)
    if '_' not in filename:
        raise ValueError(f"Invalid filename format: {filename}. Expected format: country_restofname.xlsx")

    country = filename.split('_')[0].lower()

    if not country:
        raise ValueError(f"Could not extract country from filename: {filename}")

    return country

def print_logo():
    """Display the Todor Ultra Fast superhero logo in ASCII art"""
    logo = """
    ⚡️ ═══════════════════════════════════════════════════════════════════ ⚡️
                          🦸 TODOR ULTRA FAST 🦸
              Superhero-Powered Startup Analysis at Lightning Speed!
    ⚡️ ═══════════════════════════════════════════════════════════════════ ⚡️
    """
    print(logo)

def main():
    print("=" * 80)
    print("TODOR ULTRA FAST - UNIVERSAL STARTUP ANALYSIS PIPELINE")
    print("=" * 80)
    print_logo()
    print()

    # Load configuration
    print("📋 Loading configuration...")
    try:
        config = load_config()
        print("   ✅ Config loaded successfully")
    except Exception as e:
        print(f"   ❌ Error loading config.json: {e}")
        print("   Please ensure config.json exists in the same folder")
        return

    # Get input file from user
    print("\n📂 Please provide the path to your Excel file")
    print("   (You can drag and drop the file, or paste the full path)")
    input_file = input("\nEnter path to Excel file: ").strip().strip('"')

    # Validate file exists
    if not os.path.exists(input_file):
        print(f"\n❌ Error: File not found: {input_file}")
        return

    # Extract country from filename
    try:
        country = extract_country_from_filename(input_file)
        print(f"\n🌍 Country: {country.upper()}")
    except ValueError as e:
        print(f"\n❌ Error: {e}")
        return

    # Set up output paths with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")  # Format: 20251030_1045

    # Create timestamped run folder: outputs/country_timestamp/
    outputs_base = SCRIPT_DIR / "outputs"
    run_folder = outputs_base / f"{country}_{timestamp}"
    run_folder.mkdir(parents=True, exist_ok=True)

    cache_file = SCRIPT_DIR / f"{country}_cache.json"
    llm_cache_file = SCRIPT_DIR / f"{country}_llm_cache.json"
    website_cache_file = SCRIPT_DIR / f"{country}_website_cache.json"
    enriched_file = run_folder / f"{country}_founders_enriched_{timestamp}.xlsx"
    analyzed_file = run_folder / f"{country}_founders_analyzed_{timestamp}.xlsx"
    summary_file = run_folder / f"{country}_founders_analyzed_{timestamp}_summary.txt"

    # Load and filter data
    print(f"\n📊 Loading data from: {os.path.basename(input_file)}")
    try:
        # Detect file type and read accordingly
        file_extension = os.path.splitext(input_file)[1].lower()
        if file_extension == '.csv':
            df = pd.read_csv(input_file)
        elif file_extension in ['.xlsx', '.xls']:
            df = pd.read_excel(input_file)
        else:
            raise ValueError(f"Unsupported file type: {file_extension}. Please use .csv, .xlsx, or .xls files.")

        # Keep product companies AND empty/uncertain ones (filter out service companies)
        df_product = df[(df['Product Company'] == 'product_company') | (df['Product Company'].isna())].copy()

        # Count what was filtered
        num_product = sum(df['Product Company'] == 'product_company')
        num_uncertain = sum(df['Product Company'].isna())
        num_service = len(df) - num_product - num_uncertain
        num_companies = len(df_product)

        print(f"   ℹ️  Filtered out {num_service} service companies")
        print(f"   ✅ Keeping {num_companies} companies ({num_product} product, {num_uncertain} uncertain)")
    except Exception as e:
        print(f"   ❌ Error reading Excel file: {e}")
        return

    if num_companies == 0:
        print("   ⚠️  No companies to analyze after filtering")
        return

    # ============================================================================
    # STEP 1: LINKEDIN EXTRACTION
    # ============================================================================
    print("\n" + "=" * 80)
    print("STEP 1: LINKEDIN PROFILE EXTRACTION")
    print("=" * 80)

    try:
        asyncio.run(extract_all_profiles(
            input_file=input_file,
            output_file=str(enriched_file),
            cache_file=str(cache_file),
            config=config,
            num_companies=num_companies
        ))
        print(f"\n✅ Extraction complete! Saved to: {enriched_file.name}")
    except Exception as e:
        print(f"\n❌ Extraction failed: {e}")
        return

    # ============================================================================
    # STEP 2: WEBSITE SCRAPING
    # ============================================================================
    print("\n" + "=" * 80)
    print("STEP 2: WEBSITE SCRAPING FOR PRODUCT INFO")
    print("=" * 80)

    try:
        from website_scraper import scrape_all_websites

        # Load enriched data
        df_enriched = pd.read_excel(enriched_file)

        # Get company websites (filter out NaN values)
        websites = df_enriched['Company Website'].dropna().astype(str).tolist()
        # Remove empty strings and duplicates
        websites = list(set([w for w in websites if w and w.strip()]))
        print(f"\n   Found {len(websites)} valid websites to scrape")

        # Scrape all websites (with caching)
        website_results = asyncio.run(scrape_all_websites(
            websites,
            show_progress=True,
            cache_file=str(website_cache_file)
        ))

        # Create screenshots directory inside the run folder
        screenshots_dir = run_folder / "screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)

        # Save screenshots to separate files and prepare JSON data
        def serialize_website_data(row):
            url = row['Company Website']

            # Handle NaN/None URLs
            if pd.isna(url) or not url:
                return json.dumps({'text': '', 'screenshot': None, 'method': 'none'})

            url = str(url)  # Ensure it's a string
            company_name = row['Company Name']
            data = website_results.get(url, {})

            # If no data found for this URL, return empty structure
            if not data:
                return json.dumps({'text': '', 'screenshot': None, 'method': 'not_scraped'})

            if data.get('screenshot'):
                # Save screenshot to file
                # Sanitize company name for filename
                safe_name = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in company_name)
                safe_name = safe_name.replace(' ', '_')[:50]  # Limit length
                screenshot_filename = f"{safe_name}.png"
                screenshot_path = screenshots_dir / screenshot_filename

                # Write screenshot bytes to file
                with open(screenshot_path, 'wb') as f:
                    f.write(data['screenshot'])

                # Replace screenshot bytes with filename reference
                data = data.copy()
                data['screenshot'] = screenshot_filename
            else:
                # Ensure screenshot is None if not present (not bytes)
                data = data.copy()
                data['screenshot'] = None

            return json.dumps(data)

        df_enriched['website_data_json'] = df_enriched.apply(serialize_website_data, axis=1)

        # Save updated enriched file
        df_enriched.to_excel(enriched_file, index=False)
        print(f"\n✅ Website scraping complete!")

        # Count screenshots saved
        num_screenshots = len(list(screenshots_dir.glob("*.png")))
        if num_screenshots > 0:
            print(f"   📸 Saved {num_screenshots} screenshots to: {screenshots_dir.relative_to(SCRIPT_DIR)}")

    except Exception as e:
        print(f"\n⚠️  Website scraping failed: {e}")
        print("   Continuing with analysis using LinkedIn data only...")

    # ============================================================================
    # STEP 3: AI ANALYSIS
    # ============================================================================
    print("\n" + "=" * 80)
    print("STEP 3: AI-POWERED FOUNDER ANALYSIS")
    print("=" * 80)

    try:
        analyze_all_founders(
            input_file=str(enriched_file),
            output_file=str(analyzed_file),
            summary_file=str(summary_file),
            config=config,
            llm_cache_file=str(llm_cache_file)
        )
        print(f"\n✅ Analysis complete! Results saved to:")
        print(f"   📊 {analyzed_file.name}")
        print(f"   📝 {summary_file.name}")
    except Exception as e:
        print(f"\n❌ Analysis failed: {e}")
        return

    # ============================================================================
    # PIPELINE COMPLETE
    # ============================================================================
    print("\n" + "=" * 80)
    print("🎉 PIPELINE COMPLETE!")
    print("=" * 80)
    print(f"\n📁 All outputs saved to: {run_folder.relative_to(SCRIPT_DIR)}")
    print(f"   1. {enriched_file.name} - LinkedIn data")
    print(f"   2. {analyzed_file.name} - AI analysis & scores")
    print(f"   3. {summary_file.name} - Executive summary")
    print()
    print("✨ You can now review the results and make investment decisions!")
    print()

if __name__ == "__main__":
    main()
