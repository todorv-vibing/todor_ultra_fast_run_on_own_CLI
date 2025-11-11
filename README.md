# 🦸 Todor Ultra Fast - Universal Startup Analysis Pipeline

**Superhero-Powered Startup Analysis at Lightning Speed!** ⚡

Automated LinkedIn extraction and AI-powered founder analysis for any country.

---

## 📋 What This Does

This pipeline automatically:
1. **Filters** companies (keeps product companies and uncertain ones, skips service companies)
2. **Extracts** LinkedIn profiles of startup founders with visual browser automation
3. **Scrapes** company websites with Playwright for product information
4. **Analyzes** founders using multi-model AI (Claude Haiku 4.5 & Sonnet 4.5)
5. **Scores** startups across 3 core dimensions (founder-problem fit, product clarity, market momentum)
6. **Ranks** companies by investment potential
7. **Generates** timestamped executive summary reports

---

## 🎯 Quick Start

### Step 1: Setup (One-Time)

1. **Install Python dependencies:**
   ```bash
   pip install pandas openpyxl playwright anthropic openai
   playwright install chromium
   ```

2. **Configure API Keys:**
   - Open `config.json`
   - Add your Anthropic and OpenAI API keys
   - Save the file

### Step 2: Run the Pipeline

1. **Open terminal** in the `Todor Ultra Fast` folder

2. **Run the master script:**
   ```bash
   python run_pipeline.py
   ```

3. **Follow the prompts:**
   - Paste your Excel file path when asked
   - Enter country name for output labeling (e.g., "estonia", "bulgaria")
   - Confirm to proceed
   - Log into LinkedIn in the browser (60 seconds)
   - Watch the automation work with visual feedback!

4. **Review results** in the `outputs/` folder with timestamped filenames!

---

## 📂 Folder Structure

```
Todor Ultra Fast/
├── config.json              # API keys configuration
├── run_pipeline.py          # Main entry point (run this!)
├── linkedin_extractor.py    # LinkedIn extraction with visual feedback
├── founder_analyzer.py      # AI analysis engine
├── website_scraper.py       # Pure Playwright website scraping
├── README.md                # This file
├── outputs/                 # All results (timestamped)
│   ├── {country}_founders_enriched_YYYYMMDD_HHMM.xlsx
│   ├── {country}_founders_analyzed_YYYYMMDD_HHMM.xlsx
│   ├── {country}_founders_summary_YYYYMMDD_HHMM.txt
│   └── screenshots/
│       └── {country}_YYYYMMDD_HHMM/
│           ├── Company1.png
│           └── Company2.png
└── {country}_cache.json     # LinkedIn profile cache per country
```

---

## 📊 Input Requirements

### Excel File Structure

Your Excel file must have these columns:
- `First Name`
- `Last Name`
- `Company Name`
- `LinkedIn` (LinkedIn profile URL)
- `Company Website`
- `Product Company` (values: "product_company" = analyzed, empty/blank = analyzed as uncertain, other = filtered out)
- `Position` (founder's role)
- `LinkedIn Company Description` (optional but recommended)

### Country Name Prompt

When the pipeline runs, you'll be asked to enter a country name:
- This is used for labeling all output files
- Examples: "estonia", "bulgaria", "romania", "croatia"
- The name is automatically lowercased for consistency

### Timestamped Outputs

All output files include timestamps in format `YYYYMMDD_HHMM`:

**Example:**
- Run on Oct 30, 2025 at 10:45 AM → `estonia_founders_analyzed_20251030_1045.xlsx`
- Run on Nov 5, 2025 at 2:30 PM → `estonia_founders_analyzed_20251105_1430.xlsx`

**Benefits:**
- Never overwrite previous analysis runs
- Track progress over time (Week 1, Week 2, etc.)
- Compare results across different dates
- Keep historical records of your analysis

---

## 🎯 How It Works

### Stage 1: LinkedIn Extraction (With Visual Feedback!)

**NEW: Enhanced User Experience**
- Opens Chrome browser with visual indicators
- Gives you 60 seconds to log into LinkedIn
- **Green glowing border** around each profile during extraction
- **Slowed-down actions** (300ms delay) so you can see the automation work
- Sequential progress counter: [1/5], [2/5], [3/5]...

**What's Extracted:**
- Education (universities, degrees, years)
- Work Experience (companies, titles, duration)
- Skills
- Professional headline/about section

**Technical Details:**
- Uses Playwright for browser automation
- Claude Sonnet 4.5 for intelligent AI extraction
- Scrolls through profiles to load lazy-loaded content
- Caches results per country (avoid re-extraction)
- ~30 seconds per profile

### Stage 2: Website Scraping (Pure Playwright!)

**Upgraded to 100% Playwright approach for consistency:**
- **Always uses Playwright** with full-page screenshots
- **Visual context:** Every website captured with screenshot for AI analysis
- **Consistent data:** Same quality for all sites (JS-heavy or static)
- **Cost:** ~$0.011 per company
- **Speed:** ~5 seconds per website
- **Screenshots:** Saved to `outputs/screenshots/{country}_{timestamp}/`

**What's Extracted:**
- Hero text and value proposition
- Product features and descriptions
- Target customer information
- Visual layout and design (via screenshot)

**How it Works:**
```
For each website:
  1. Launch Playwright browser (headless)
  2. Navigate to company website
  3. Wait for JavaScript to load (2 seconds)
  4. Capture full-page screenshot
  5. Extract visible text content
  6. Send both to Claude Vision API for analysis
  7. Save screenshot to timestamped folder
```

### Stage 3: AI Analysis

**3 Core Scoring Dimensions:**

1. **Founder-Problem Fit (45% weight)**
   - **Two Valid Paths:**
     - **Path A - Industry Insider:** Deep domain expertise, lived the problem
     - **Path B - Disruptive Outsider:** Fresh perspective from different industry
   - **Evaluation considers holistically:**
     - Domain expertise OR novel perspective
     - Relevant transferable skills (technical, cross-domain, building/scaling)
     - Unique insight or unfair advantage
     - Credibility & execution capability
   - **Examples of outsider success:** Airbnb (designers → hospitality), Stripe (no payments exp), Tesla (tech → automotive)

2. **Product Clarity (10% weight)**
   - **Uses website screenshots + text** for visual product understanding
   - **Evaluation considers holistically:**
     - **Value proposition clarity:** What problem does it solve? Can you understand it in one sentence?
     - **Problem severity & pain:** Is this a PAINFUL problem customers actively seek to solve, or just a nice-to-have?
     - **Target customer clarity:** Who exactly is this for? B2B/B2C? Industry/vertical?
     - **Product specificity:** What exactly is being built? Platform, tool, service, SaaS?
   - **Key principle:** Pain severity matters more than novelty - solving urgent problems beats clever solutions to mild inconveniences

3. **Market Momentum (45% weight)**
   - **Evaluation considers holistically:**
     - **Market timing & tailwinds:** High-priority verticals include:
       * AI Infrastructure (developer tools, synthetic data, big data)
       * AI-Enabled Apps (video/image AI, voice AI, consumer AI apps)
       * Security & Observability (network security, identity-driven, data center)
       * Physical AI & Robotics (hardware renaissance, physical world automation)
       * Fintech/Crypto (digital assets for institutions, stablecoins)
       * Silicon Photonics (AI compute acceleration)
       * E-commerce Reinvention (AI agents for commerce, voice transactions)
     - **Market size & growth:** Multi-billion opportunity, >20% growth, massive TAMs
     - **Competitive positioning:** Early in new category, proprietary data, vulnerable incumbents
     - **Traction indicators:** Customer evidence, revenue metrics, product-market fit signals
   - **Key principle:** Platform shifts (AI, voice, video, crypto) create new company categories. Look for "fundamental resets" and infrastructure plays over consumer apps

**AI Models Used:**
- **Claude Haiku 4.5:** Fast scoring (founder fit, product, market)
- **Claude Sonnet 4.5:** Deep analysis for LinkedIn extraction and final assessments
- **Cost per company:** ~$0.02-0.05

**Final Score:** Weighted average of all 3 dimensions (1-10 scale)

**Investment Philosophy:**
- **Founder-Problem Fit (45%)** - The right person for THIS problem (insider depth OR outsider innovation)
- **Market Momentum (45%)** - The right market timing (markets matter more than products since products pivot)
- **Product Clarity (10%)** - Solving a painful problem (products will evolve, but pain must be real)

**Why these weights?** Early-stage startups pivot products frequently, but founder-market fit and market timing are more stable indicators of success.

### Stage 4: Final Assessment

- Uses Claude Sonnet 4.5 for comprehensive assessment
- Provides investment recommendation
- Identifies key strengths and concerns
- Ranks all companies by final score

---

## 📈 Output Files

### 1. `{country}_founders_enriched_{timestamp}.xlsx`
- Original data + extracted LinkedIn information
- All education, experience, skills in JSON format
- Website data JSON with screenshot references

### 2. `{country}_founders_analyzed_{timestamp}.xlsx`
- All scores for each founder
- Individual dimension scores
- Reasoning for each score
- Final assessment
- **Use this for detailed analysis**

### 3. `{country}_founders_summary_{timestamp}.txt`
- Executive summary with statistics
- Top 10 ranked companies
- Score distribution
- Component averages
- **Use this for quick overview**

### 4. `screenshots/{country}_{timestamp}/`
- Full-page screenshots of each company website
- Used for visual AI analysis
- Reference for manual review
- Organized by company name

---

## 💡 Usage Tips

### Best Practices

1. **Internet Connection:** Ensure stable connection for LinkedIn extraction and website scraping
2. **LinkedIn Login:** Have your credentials ready for the 60-second window
3. **Visual Feedback:** Watch the green glowing border as profiles are extracted!
4. **API Rate Limits:** The pipeline respects rate limits (0.5s delay between calls)
5. **Batch Size:** Works best with 10-100 companies at once
6. **Cache:** Cache persists between runs - you won't re-extract same profiles
7. **Timestamps:** Run multiple analyses on same country without overwriting

### Troubleshooting

**Problem:** "File not found" error
- **Solution:** Check that you pasted the full path to your Excel file

**Problem:** Can't see LinkedIn extraction happening
- **Solution:**
  - Check if profiles are cached (delete `{country}_cache.json` to force re-extraction)
  - Look for the green glowing border around the browser

**Problem:** "No companies to analyze after filtering"
- **Solution:** Check that your Excel has `Product Company` column with value "product_company" or empty/blank cells
- **Note:** Service companies (any value other than "product_company" or blank) are automatically filtered out

**Problem:** API errors
- **Solution:** Verify your API keys in `config.json` are correct and active

---

## 🔒 Data Privacy

- All data stays on your computer
- LinkedIn profiles are cached locally
- Screenshots saved locally in outputs folder
- No data is sent anywhere except to AI APIs for analysis (Anthropic & OpenAI)
- Cache files and screenshots can be deleted anytime

---

## 🆘 Support

If you encounter issues:
1. Check this README for troubleshooting
2. Verify your `config.json` has valid API keys
3. Ensure all dependencies are installed (`playwright install chromium`)
4. Check that your Excel file has required columns
5. Try deleting cache file to force fresh extraction

---

## 📝 Example Workflow

```bash
# 1. Navigate to the folder
cd "C:\Users\todor\Todor Ultra Fast"

# 2. Run the pipeline
python run_pipeline.py

# You'll see the superhero banner:
# ⚡️ ═══════════════════════════════════════════════════════════ ⚡️
#                   🦸 TODOR ULTRA FAST 🦸
#       Superhero-Powered Startup Analysis at Lightning Speed!
# ⚡️ ═══════════════════════════════════════════════════════════ ⚡️

# 3. When prompted, paste your Excel file path:
Enter path to Excel file: C:\Users\todor\Downloads\Estonia 2025 persons export.xlsx

# 4. Enter country name:
Enter country name for labeling outputs: estonia

# 5. Confirm:
Proceed? (yes/no): yes

# 6. Log into LinkedIn when browser opens (60 seconds)
# Watch the green glowing border as profiles are extracted!

# 7. Wait for completion (~5-10 minutes for 5-10 companies)

# 8. Check outputs folder for timestamped results!
```

---

## 🎨 Customization

### Adjust Login Time

Edit `config.json`:
```json
{
  "linkedin_login_timeout": 90,  // Change from 60 to 90 seconds
  "api_delay": 0.5               // Delay between API calls
}
```

### Change Scoring Weights

Edit `founder_analyzer.py` line ~335 with `final_score` calculation:
```python
final_score = (
    founder_fit_score * 0.45 +  # Founder-Problem Fit: 45%
    prod_score * 0.10 +          # Product Clarity: 10%
    market_score * 0.45          # Market Momentum: 45%
)
```

### Adjust Visual Feedback Speed

Edit `linkedin_extractor.py` line 172:
```python
slow_mo=300  # Increase to 500 for slower, more visible actions
```

---

## 🚀 Version History

**Version:** 2.0
**Last Updated:** 2025-10-30

### What's New in 2.0:
- ✨ **Superhero Logo** - Fun ASCII art banner on startup
- 🎨 **Visual Feedback** - Green glowing border during LinkedIn extraction
- 📸 **Pure Playwright** - 100% Playwright website scraping with screenshots
- 📅 **Timestamped Outputs** - Never overwrite previous analysis runs
- ⚡ **Improved UX** - Slowed-down browser actions for visibility
- 🧹 **Clean Structure** - Removed obsolete test files
- 📊 **Sequential Counters** - Clear progress tracking [1/5], [2/5]...

**Created:** 2025-10-28
**Author:** Todor with Claude Code

---

**Happy analyzing! 🎯⚡🦸**
