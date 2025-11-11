#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Universal Founder Analyzer
AI-powered analysis of founders using multi-model approach
"""

import pandas as pd
import sys
import time
import json
from datetime import datetime, timedelta
from anthropic import Anthropic
from openai import OpenAI
from pydantic import BaseModel, Field
from typing import Literal
from pathlib import Path

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
        # If timestamp is invalid or missing, consider it expired
        return True

def create_cache_key(company_name, website_url, linkedin_url):
    """Create unique cache key from company name, website, and founder LinkedIn"""
    # Handle NaN/None values
    company = str(company_name) if pd.notna(company_name) else ""
    website = str(website_url) if pd.notna(website_url) else ""
    linkedin = str(linkedin_url) if pd.notna(linkedin_url) else ""
    return f"{company}||{website}||{linkedin}"

def load_llm_cache(cache_file):
    """Load LLM scoring cache from file"""
    try:
        with open(cache_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

def save_llm_cache(cache, cache_file):
    """Save LLM scoring cache to file"""
    with open(cache_file, 'w', encoding='utf-8') as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)

# Pydantic models for structured outputs
class ScoreResponse(BaseModel):
    """Structured response for scoring with reasoning"""
    score: int = Field(ge=1, le=10, description="Score from 1-10")
    reasoning: str = Field(description="Detailed reasoning for the score")

class ComponentScore(BaseModel):
    """Individual component score with reasoning"""
    score: int = Field(ge=1, le=10, description="Score from 1-10")
    reasoning: str = Field(description="Brief reasoning for the score")

class FinalAssessment(BaseModel):
    """Structured final investment assessment with component scores"""
    decision: Literal["INVEST", "PASS", "REVISIT"] = Field(description="Investment decision")
    founder_problem_fit: ComponentScore = Field(description="Founder-problem fit score and reasoning")
    product_clarity: ComponentScore = Field(description="Product clarity score and reasoning")
    market_momentum: ComponentScore = Field(description="Market momentum score and reasoning")
    key_strengths: str = Field(description="Key strengths (2-3 bullet points)")
    key_concerns: str = Field(description="Key concerns (2-3 bullet points)")

def call_haiku(prompt, anthropic_client, max_tokens=300):
    """Call Claude Haiku for fast scoring"""
    try:
        time.sleep(0.5)
        message = anthropic_client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text.strip()
    except Exception as e:
        print(f"  ⚠️  Haiku API error: {str(e)}")
        return "Error: Could not analyze"

def call_haiku_structured(prompt, anthropic_client, response_model: type[BaseModel], max_tokens=1200, retry_tokens=1800, max_retries=2):
    """Call Claude Haiku with structured output using Pydantic model with retry logic"""

    for attempt in range(max_retries):
        try:
            time.sleep(0.5)

            # Use higher token limit on retries
            current_max_tokens = retry_tokens if attempt > 0 else max_tokens

            # Convert Pydantic model to JSON schema
            schema = response_model.model_json_schema()

            # Add schema instruction to prompt
            structured_prompt = f"""{prompt}

IMPORTANT: Respond with a valid JSON object matching this exact schema:
{json.dumps(schema, indent=2)}

Return ONLY the JSON object, no other text."""

            message = anthropic_client.messages.create(
                model='claude-haiku-4-5-20251001',
                max_tokens=current_max_tokens,
                messages=[{"role": "user", "content": structured_prompt}]
            )

            response_text = message.content[0].text.strip()

            # Check for empty response BEFORE parsing
            if not response_text or len(response_text) < 10:
                if attempt < max_retries - 1:
                    print(f"  ⚠️  Empty response, retrying with {retry_tokens} tokens ({attempt + 1}/{max_retries})...")
                    time.sleep(1)
                    continue
                else:
                    raise ValueError("Empty response after all retries")

            # Extract JSON from response (handle markdown code blocks)
            if '```json' in response_text:
                start = response_text.find('```json') + 7
                end = response_text.rfind('```')
                response_text = response_text[start:end].strip()
            elif '```' in response_text:
                start = response_text.find('```') + 3
                end = response_text.rfind('```')
                response_text = response_text[start:end].strip()

            # Parse and validate with Pydantic
            return response_model.model_validate_json(response_text)

        except Exception as e:
            if attempt < max_retries - 1:
                print(f"  ⚠️  API error: {str(e)[:100]}, retrying with {retry_tokens} tokens ({attempt + 1}/{max_retries})...")
                time.sleep(1)
                continue
            else:
                print(f"  ⚠️  Structured API error after {max_retries} attempts: {str(e)[:100]}")
                # Return default valid response
                if response_model == ScoreResponse:
                    return ScoreResponse(score=5, reasoning=f"Error: {str(e)[:200]}")
                elif response_model == FinalAssessment:
                    return FinalAssessment(
                        decision="PASS",
                        founder_problem_fit=ComponentScore(score=5, reasoning="Unable to assess due to error"),
                        product_clarity=ComponentScore(score=5, reasoning="Unable to assess due to error"),
                        market_momentum=ComponentScore(score=5, reasoning="Unable to assess due to error"),
                        key_strengths="Unable to assess due to error",
                        key_concerns=f"Error occurred: {str(e)[:200]}"
                    )
                raise

def call_sonnet(prompt, anthropic_client, max_tokens=800):
    """Call Claude Sonnet for final assessment"""
    try:
        time.sleep(0.5)
        message = anthropic_client.messages.create(
            model='claude-sonnet-4-5-20250929',
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text.strip()
    except Exception as e:
        print(f"  ⚠️  Sonnet API error: {str(e)}")
        return "Error: Could not analyze"

def score_founder_problem_fit(education_json, experience_json, about, company_description, company_name, anthropic_client):
    """
    Score founder-problem fit: How well does this founder's unique background
    position them to solve THIS specific problem?
    """
    # Handle missing data
    if (not education_json or education_json == '[]') and (not experience_json or experience_json == '[]'):
        return 3, "No education or experience data available"

    prompt = f"""Evaluate the FOUNDER-PROBLEM FIT for this startup on a 1-10 scale.

COMPANY: {company_name}
ABOUT: {about}
DESCRIPTION: {company_description}

FOUNDER'S EDUCATION:
{education_json}

FOUNDER'S EXPERIENCE:
{experience_json}

EVALUATION CRITERIA (consider holistically):

1. DOMAIN EXPERTISE OR NOVEL PERSPECTIVE - Most Important
   PATH A - Industry Insider:
   - Has the founder personally experienced this problem?
   - Do they have deep domain knowledge from education OR experience?
   - Have they worked in this industry/sector before?

   PATH B - Disruptive Outsider:
   - Does the founder bring a FRESH perspective from a different industry?
   - Are they applying proven solutions from one domain to solve problems in another?
   - Is their "outsider status" actually an ADVANTAGE for innovation?
   - Do they have a contrarian insight that insiders would miss?

   BOTH PATHS CAN SCORE 10/10. Insiders have depth. Outsiders have fresh eyes.

2. RELEVANT TRANSFERABLE SKILLS - Very Important
   - Does their background give them skills that TRANSFER to this problem?
   - Technical skills (engineering, data, design)
   - Cross-domain pattern recognition
   - Building/scaling skills from other industries
   - First principles thinking ability

3. UNIQUE INSIGHT OR UNFAIR ADVANTAGE - Important
   - Does their background give them a unique perspective?
   - Have they spotted something others missed due to their experience?
   - Insider knowledge/access OR outsider contrarian view?
   - Are they uniquely positioned to execute on this insight?

4. CREDIBILITY & EXECUTION CAPABILITY - Important
   - Can they convince customers/investors they can solve this?
   - Do they have a track record of building/shipping things?
   - Demonstrated ability to learn quickly and execute?

KEY INSIGHTS:
- Industry insiders can win by having lived the problem deeply
- Industry outsiders can win by bringing novel solutions from other domains
- Examples of OUTSIDER SUCCESS:
  * Airbnb founders (designers disrupting hospitality)
  * Stripe founders (no payments experience, fresh perspective on developer tools)
  * Tesla (tech people disrupting automotive)

BOTH PATHS ARE VALID. Look for either:
- Deep domain expertise + problem intimacy
- OR fresh perspective + transferable skills + novel insight

SCORING GUIDE:
10 = Perfect fit. Either deep insider with lived experience OR brilliant outsider
     with novel insight and transferable skills that perfectly map to this problem.
8-9 = Strong fit. Clear relevant background (insider path) OR compelling cross-domain
     advantage (outsider path).
6-7 = Moderate fit. Some relevant background but missing either depth OR novel insight.
4-5 = Weak fit. Generic background with no clear insider advantage or outsider innovation.
1-3 = Poor fit. Neither domain expertise nor credible fresh perspective.

IMPORTANT: Provide ONE holistic score (1-10) considering all criteria together.
Do NOT calculate sub-scores or show internal point breakdowns."""

    result = call_haiku_structured(prompt, anthropic_client, ScoreResponse, max_tokens=1200, retry_tokens=1800)
    return result.score, result.reasoning

def resize_image_if_needed(image_bytes, max_dimension=7500):
    """Resize image if either dimension exceeds max_dimension to comply with API limits"""
    try:
        from PIL import Image
        import io

        img = Image.open(io.BytesIO(image_bytes))
        width, height = img.size

        # Check if resizing is needed
        if width <= max_dimension and height <= max_dimension:
            return image_bytes

        # Calculate new dimensions (maintain aspect ratio)
        if width > height:
            new_width = max_dimension
            new_height = int(height * (max_dimension / width))
        else:
            new_height = max_dimension
            new_width = int(width * (max_dimension / height))

        print(f"  📐 Resizing image from {width}x{height} to {new_width}x{new_height}")

        # Resize with high-quality resampling
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

        # Save to bytes
        output = io.BytesIO()
        img.save(output, format='PNG', optimize=True)
        return output.getvalue()

    except Exception as e:
        print(f"  ⚠️  Image resize error: {str(e)}, using original")
        return image_bytes

def score_product_clarity(website_data, anthropic_client):
    """
    Score product clarity (1-10) using website content and optional screenshot

    Args:
        website_data: dict with 'text', 'screenshot' (bytes or None), 'method'
        anthropic_client: Anthropic API client
    """
    import base64

    if not website_data or not website_data.get('text'):
        return 3, "No product information available"

    # Build prompt with website content
    text_prompt = f"""Score the product clarity on a 1-10 scale.

WEBSITE CONTENT:
{website_data['text']}

EVALUATION CRITERIA (consider holistically):

1. VALUE PROPOSITION CLARITY - Most Important
   - What specific problem does this product/service solve?
   - Is the problem statement clear and concrete?
   - Can you understand WHAT they do in one sentence?

2. PROBLEM SEVERITY & PAIN - Very Important
   - Is this solving a PAINFUL problem or just a "nice-to-have"?
   - How severe/critical is this problem for the customer?
   - Would customers actively seek a solution or just stumble upon it?
   - Does this address a real pain point that costs time, money, or frustration?

3. TARGET CUSTOMER CLARITY - Important
   - Who exactly is this for? (specific customer segment)
   - Is the target customer clearly defined?
   - B2B or B2C? What industry/vertical?

4. PRODUCT/SERVICE SPECIFICITY - Important
   - What exactly is being built/sold?
   - Is it a platform, tool, service, marketplace, SaaS?
   - Can you visualize what the product actually does?

SCORING GUIDE:
10 = Crystal clear value prop solving severe, painful problem for well-defined customers
8-9 = Clear value prop addressing real pain, target customer identified
6-7 = Understandable product but pain severity unclear or target customer vague
4-5 = Vague value prop, unclear if problem is actually painful
1-3 = Cannot understand what they do or who it's for

KEY: Prioritize PAIN SEVERITY over novelty. A clear solution to a painful, urgent problem
scores higher than a clever solution to a mild inconvenience.

IMPORTANT: Provide ONE holistic score (1-10) considering all criteria together.
Do NOT calculate sub-scores or show internal point breakdowns like "X/40"."""

    # Check if we have a screenshot to send
    screenshot_bytes = website_data.get('screenshot')

    if screenshot_bytes:
        # Use Vision API with screenshot (Sonnet supports images + structured output)
        try:
            time.sleep(0.5)
            # Resize image if needed to comply with API limits (8000px max)
            screenshot_bytes = resize_image_if_needed(screenshot_bytes, max_dimension=7500)
            screenshot_base64 = base64.b64encode(screenshot_bytes).decode('utf-8')

            # Add JSON schema instruction
            schema = ScoreResponse.model_json_schema()
            structured_prompt = f"""{text_prompt}

IMPORTANT: Respond with a valid JSON object matching this exact schema:
{json.dumps(schema, indent=2)}

Return ONLY the JSON object, no other text."""

            message = anthropic_client.messages.create(
                model='claude-sonnet-4-5-20250929',
                max_tokens=700,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": screenshot_base64
                            }
                        },
                        {
                            "type": "text",
                            "text": structured_prompt
                        }
                    ]
                }]
            )
            response_text = message.content[0].text.strip()

            # Extract and parse JSON
            if '```json' in response_text:
                start = response_text.find('```json') + 7
                end = response_text.rfind('```')
                response_text = response_text[start:end].strip()
            elif '```' in response_text:
                start = response_text.find('```') + 3
                end = response_text.rfind('```')
                response_text = response_text[start:end].strip()

            result = ScoreResponse.model_validate_json(response_text)
            return result.score, result.reasoning

        except Exception as e:
            print(f"  ⚠️  Vision API error: {str(e)}, falling back to text-only")
            result = call_haiku_structured(text_prompt, anthropic_client, ScoreResponse, max_tokens=1000, retry_tokens=1500)
            return result.score, result.reasoning
    else:
        # Text-only with structured Haiku
        result = call_haiku_structured(text_prompt, anthropic_client, ScoreResponse, max_tokens=1000, retry_tokens=1500)
        return result.score, result.reasoning

def score_market_momentum(about, company_description, company_name, anthropic_client):
    """Score market momentum (1-10)"""
    prompt = f"""Score market momentum/traction on a 1-10 scale.

Company: {company_name}
About: {about}
Description: {company_description}

EVALUATION CRITERIA (consider holistically):

1. MARKET TIMING & TAILWINDS - Most Important
   HIGH-PRIORITY VERTICALS (score 8-10):
   - AI Infrastructure: Developer tools, synthetic data, big data solutions
   - AI-Enabled Applications: Video/image AI, voice AI, consumer apps with AI
   - Security & Observability: Network security, identity-driven security, data center security
   - Physical AI & Robotics: Hardware renaissance, physical world automation
   - Fintech/Crypto Infrastructure: Digital assets for regulated institutions, stablecoins
   - Silicon Photonics: AI compute acceleration
   - E-commerce Reinvention: AI agents for commerce, voice-based transactions

   EMERGING OPPORTUNITIES (score 6-8):
   - Healthcare AI: LLM-powered health tools, medical data utilization
   - Entertainment/Creative AI: AI-generated content, remixing, user-generated experiences
   - Industry-specific AI: Transforming traditional sectors with AI

   Questions to ask:
   - Is this riding a major technology wave (AI, blockchain, new compute)?
   - Is there regulatory tailwind or infrastructure maturation happening now?
   - Are they in a sector undergoing fundamental reset/transformation?

2. MARKET SIZE & GROWTH POTENTIAL - Very Important
   - Is this a multi-billion dollar opportunity?
   - Is the market expanding rapidly (>20% annual growth)?
   - Are they solving problems in large, underserved markets?
   - Examples of massive markets: Physical world (80% of GDP), commerce in AI (multi-trillion),
     services industry ($500B labor market)

3. COMPETITIVE POSITIONING - Important
   - Are they early in a new category or platform shift?
   - Do they have proprietary data or unique access? (e.g., security/observability data)
   - Are incumbents vulnerable due to technology shift?
   - Example: Network security fundamentally resetting due to AI

4. TRACTION INDICATORS - Important
   - Any mentioned customers, revenue, or growth metrics?
   - Evidence of product-market fit in description?
   - Mention of pilots, partnerships, or validation?

SCORING GUIDE:
10 = Perfect timing in high-priority vertical with massive market (e.g., AI infrastructure,
     physical AI, silicon photonics) + clear category-defining potential
8-9 = Strong market in emerging opportunity with large TAM + good timing
6-7 = Decent market but either unclear timing, smaller TAM, or crowded space
4-5 = Market exists but limited growth, unclear opportunity, or very early/unproven
1-3 = Small/declining market, bad timing, or market not clearly defined

KEY PRINCIPLES:
- Platform shifts (AI, voice, video, crypto rails) create new categories of companies
- Markets matter more than products - products pivot but market timing is fundamental
- Look for markets undergoing "fundamental reset" or "step function change"
- Prioritize infrastructure plays and picks-and-shovels over consumer apps (unless
  it's a new category enabled by platform shift)

IMPORTANT: Provide ONE holistic score (1-10) considering all criteria together.
Do NOT calculate sub-scores or show internal point breakdowns."""

    result = call_haiku_structured(prompt, anthropic_client, ScoreResponse, max_tokens=1000, retry_tokens=1500)
    return result.score, result.reasoning

def generate_final_assessment(row, scores, anthropic_client):
    """Generate final assessment with Sonnet using structured output"""
    prompt = f"""Generate a final investment assessment for this startup.

Company: {row['Company Name']}
Founder: {row['First Name']} {row['Last Name']}
Position: {row['Position']}

COMPONENT SCORES & REASONING:
- Founder-Problem Fit: {scores['founder_problem_fit_score']}/10 (45% weight)
  Reasoning: {scores['founder_problem_fit_reasoning']}

- Product Clarity: {scores['product_score']}/10 (10% weight)
  Reasoning: {scores['product_reasoning']}

- Market Momentum: {scores['market_score']}/10 (45% weight)
  Reasoning: {scores['market_reasoning']}

FINAL SCORE: {scores['final_score']:.1f}/10

Provide a structured assessment with:
1. **decision**: Either "INVEST" (7+ final score), "REVISIT" (5-6.9 final score), or "PASS" (<5 final score)
2. **founder_problem_fit**: Include the score and a brief summary of the reasoning (1-2 sentences, ~40 words max)
3. **product_clarity**: Include the score and a brief summary of the reasoning (1-2 sentences, ~40 words max)
4. **market_momentum**: Include the score and a brief summary of the reasoning (1-2 sentences, ~40 words max)
5. **key_strengths**: 2-3 bullet points of key strengths (~15 words per bullet)
6. **key_concerns**: 2-3 bullet points of main concerns (~15 words per bullet)

Be direct, data-driven, and concise. Summarize the provided reasoning tightly."""

    # Add JSON schema instruction
    schema = FinalAssessment.model_json_schema()
    structured_prompt = f"""{prompt}

IMPORTANT: Respond with a valid JSON object matching this exact schema:
{json.dumps(schema, indent=2)}

Return ONLY the JSON object, no other text."""

    try:
        time.sleep(0.5)
        message = anthropic_client.messages.create(
            model='claude-sonnet-4-5-20250929',
            max_tokens=900,
            messages=[{"role": "user", "content": structured_prompt}]
        )
        response_text = message.content[0].text.strip()

        # Extract and parse JSON
        if '```json' in response_text:
            start = response_text.find('```json') + 7
            end = response_text.rfind('```')
            response_text = response_text[start:end].strip()
        elif '```' in response_text:
            start = response_text.find('```') + 3
            end = response_text.rfind('```')
            response_text = response_text[start:end].strip()

        result = FinalAssessment.model_validate_json(response_text)

        # Format as readable text with all component scores
        formatted = f"""**COMPONENT SCORES:**

**Founder-Problem Fit:** {result.founder_problem_fit.score}/10 (Weight: 45%)
{result.founder_problem_fit.reasoning}

**Product Clarity:** {result.product_clarity.score}/10 (Weight: 10%)
{result.product_clarity.reasoning}

**Market Momentum:** {result.market_momentum.score}/10 (Weight: 45%)
{result.market_momentum.reasoning}

**Key Strengths:**
{result.key_strengths}

**Key Concerns:**
{result.key_concerns}"""

        return formatted

    except Exception as e:
        print(f"  ⚠️  Structured assessment error: {str(e)}, using fallback")
        # Fallback to simple format with scores
        return f"""**Founder-Problem Fit:** {scores['founder_problem_fit_score']}/10
{scores['founder_problem_fit_reasoning'][:200]}...

**Product Clarity:** {scores['product_score']}/10
{scores['product_reasoning'][:200]}...

**Market Momentum:** {scores['market_score']}/10
{scores['market_reasoning'][:200]}..."""

def analyze_company(idx, row, total, anthropic_client, screenshots_dir=None, llm_cache=None, llm_cache_file=None):
    """Analyze a single company"""
    company_name = row['Company Name']
    founder_name = f"{row['First Name']} {row['Last Name']}"
    website_url = row.get('Company Website', '')
    linkedin_url = row.get('LinkedIn', '')

    print(f"\n[{idx+1}/{total}] {company_name} - {founder_name}")
    print("-" * 80)

    # Create cache key
    cache_key = create_cache_key(company_name, website_url, linkedin_url)

    # Check cache
    if llm_cache and cache_key in llm_cache:
        cache_entry = llm_cache[cache_key]

        # Check if cache is valid (has timestamp and not expired)
        if isinstance(cache_entry, dict) and 'timestamp' in cache_entry:
            if not is_cache_expired(cache_entry['timestamp']):
                # Use cached scores - print them out for visibility
                cache_age_days = (datetime.now() - datetime.fromisoformat(cache_entry['timestamp'])).days
                print(f"  ✓ Using cached LLM scores ({cache_age_days} days old)")

                cached_data = cache_entry['data']

                # Extract scores
                founder_fit_score = cached_data['founder_problem_fit']['score']
                founder_fit_reason = cached_data['founder_problem_fit']['reasoning']
                prod_score = cached_data['product_clarity']['score']
                prod_reason = cached_data['product_clarity']['reasoning']
                market_score = cached_data['market_momentum']['score']
                market_reason = cached_data['market_momentum']['reasoning']

                # Print cached scores for visibility
                print("  👤 Founder-problem fit (cached):")
                print(f"     {founder_fit_score}/10 - {founder_fit_reason[:60]}...")
                print("  🎯 Product clarity (cached):")
                print(f"     {prod_score}/10 - {prod_reason[:60]}...")
                print("  🚀 Market momentum (cached):")
                print(f"     {market_score}/10 - {market_reason[:60]}...")

                # Calculate final score
                final_score = (
                    founder_fit_score * 0.45 +  # Founder-Problem Fit: 45%
                    prod_score * 0.10 +          # Product Clarity: 10%
                    market_score * 0.45          # Market Momentum: 45%
                )

                scores = {
                    'founder_problem_fit_score': founder_fit_score,
                    'founder_problem_fit_reasoning': founder_fit_reason,
                    'product_score': prod_score,
                    'product_reasoning': prod_reason,
                    'market_score': market_score,
                    'market_reasoning': market_reason,
                    'final_score': final_score
                }

                # Use cached assessment - NO LLM call needed!
                assessment = cached_data.get('final_assessment', '')

                # Handle old cache entries without assessment
                if not assessment:
                    print("  🤖 Generating final assessment (not in cache)...")
                    assessment = generate_final_assessment(row, scores, anthropic_client)

                    # Update cache with the newly generated assessment
                    cached_data['final_assessment'] = assessment
                    llm_cache[cache_key] = {
                        'timestamp': cache_entry['timestamp'],  # Keep original timestamp
                        'data': cached_data
                    }
                    save_llm_cache(llm_cache, llm_cache_file)
                    print("  💾 Updated cache with final assessment")

                print(f"\n  ⭐ FINAL SCORE: {final_score:.1f}/10 (cached)")
                print(f"  💭 {assessment[:100]}...")

                return scores, assessment
            else:
                print(f"  ⚠️  LLM cache expired (>3 months old), re-analyzing...")
        else:
            print(f"  ⚠️  Invalid cache entry format, re-analyzing...")

    # Score each dimension
    print("  👤 Scoring founder-problem fit...")
    founder_fit_score, founder_fit_reason = score_founder_problem_fit(
        row.get('linkedin_education', ''),
        row.get('linkedin_experience', ''),
        row.get('linkedin_about', ''),
        row.get('LinkedIn Company Description', ''),
        company_name,
        anthropic_client
    )
    print(f"     {founder_fit_score}/10 - {founder_fit_reason[:60]}...")

    print("  🎯 Scoring product clarity...")
    # Parse JSON string back to dict (stored as JSON in Excel)
    website_data_json = row.get('website_data_json', '{}')
    try:
        website_data = json.loads(website_data_json) if website_data_json else {}
    except json.JSONDecodeError as e:
        print(f"  ⚠️  JSON parse error: {str(e)}, using empty website data")
        website_data = {}

    # Load screenshot from file if filename is present
    if website_data and website_data.get('screenshot') and screenshots_dir:
        from pathlib import Path
        screenshot_filename = website_data['screenshot']

        try:
            screenshot_path = Path(screenshots_dir) / screenshot_filename

            if screenshot_path.exists():
                with open(screenshot_path, 'rb') as f:
                    website_data['screenshot'] = f.read()
            else:
                print(f"  ⚠️  Screenshot file not found: {screenshot_filename}")
                website_data['screenshot'] = None
        except Exception as e:
            print(f"  ⚠️  Screenshot load error: {str(e)}, removing screenshot")
            website_data['screenshot'] = None

    prod_score, prod_reason = score_product_clarity(
        website_data,
        anthropic_client
    )
    print(f"     {prod_score}/10 - {prod_reason[:60]}...")

    print("  🚀 Scoring market momentum...")
    market_score, market_reason = score_market_momentum(
        row.get('linkedin_about', ''),
        row.get('LinkedIn Company Description', ''),
        company_name,
        anthropic_client
    )
    print(f"     {market_score}/10 - {market_reason[:60]}...")

    # Calculate weighted final score (3 dimensions)
    final_score = (
        founder_fit_score * 0.45 +  # Founder-Problem Fit: 45%
        prod_score * 0.10 +          # Product Clarity: 10%
        market_score * 0.45          # Market Momentum: 45%
    )

    scores = {
        'founder_problem_fit_score': founder_fit_score,
        'founder_problem_fit_reasoning': founder_fit_reason,
        'product_score': prod_score,
        'product_reasoning': prod_reason,
        'market_score': market_score,
        'market_reasoning': market_reason,
        'final_score': final_score
    }

    # Generate final assessment BEFORE caching
    print("  🤖 Generating final assessment...")
    assessment = generate_final_assessment(row, scores, anthropic_client)

    # Save to LLM cache (including final assessment)
    if llm_cache is not None and llm_cache_file:
        cache_data = {
            'founder_problem_fit': {'score': founder_fit_score, 'reasoning': founder_fit_reason},
            'product_clarity': {'score': prod_score, 'reasoning': prod_reason},
            'market_momentum': {'score': market_score, 'reasoning': market_reason},
            'final_assessment': assessment
        }
        llm_cache[cache_key] = {
            'timestamp': datetime.now().isoformat(),
            'data': cache_data
        }
        save_llm_cache(llm_cache, llm_cache_file)

    print(f"\n  ⭐ FINAL SCORE: {final_score:.1f}/10")
    print(f"  💭 {assessment[:100]}...")

    return scores, assessment

def analyze_all_founders(input_file, output_file, summary_file, config, llm_cache_file=None):
    """Main analysis function"""
    from pathlib import Path

    print("\n📊 Loading enriched data...")
    df = pd.read_excel(input_file)
    print(f"   Found {len(df)} companies to analyze\n")

    # Initialize Anthropic client
    anthropic_client = Anthropic(api_key=config['anthropic_api_key'])

    # Load LLM cache
    llm_cache = {}
    if llm_cache_file:
        llm_cache = load_llm_cache(llm_cache_file)
        print(f"   📦 LLM cache contains: {len(llm_cache)} entries\n")

    # Determine screenshots directory
    input_path = Path(input_file)

    # Screenshots are now stored in the same run folder as the enriched file
    # New structure: outputs/country_timestamp/screenshots/
    # Old structure: outputs/screenshots/country_timestamp/ (for backward compatibility)

    # Try new structure first (in same directory as enriched file)
    screenshots_dir = input_path.parent / "screenshots"

    # If new structure doesn't exist, fall back to old structure
    if not screenshots_dir.exists():
        # Extract country and timestamp from enriched filename for legacy structure
        filename = input_path.stem
        parts = filename.split('_')

        if 'enriched' in parts:
            enriched_idx = parts.index('enriched')
            country_parts = parts[:enriched_idx-1]
            timestamp_parts = parts[enriched_idx+1:]

            if timestamp_parts:
                screenshot_dir_name = '_'.join(country_parts + timestamp_parts)
            else:
                screenshot_dir_name = '_'.join(country_parts)
        else:
            screenshot_dir_name = parts[0]

        # Legacy location: outputs/screenshots/country_timestamp/
        screenshots_dir = input_path.parent.parent / "screenshots" / screenshot_dir_name

    print(f"   Screenshots directory: {screenshots_dir}")
    if screenshots_dir.exists():
        screenshot_count = len(list(screenshots_dir.glob("*.png")))
        print(f"   Found {screenshot_count} screenshots")
    else:
        print(f"   ⚠️  Screenshots directory not found!")

    # Initialize result columns
    result_columns = [
        'founder_problem_fit_score', 'founder_problem_fit_reasoning',
        'product_score', 'product_reasoning',
        'market_score', 'market_reasoning',
        'final_score', 'final_assessment'
    ]

    for col in result_columns:
        df[col] = ''

    # Analyze each company
    for idx, row in df.iterrows():
        try:
            scores, assessment = analyze_company(idx, row, len(df), anthropic_client, screenshots_dir, llm_cache, llm_cache_file)

            # Store results
            for key, value in scores.items():
                df.at[idx, key] = value
            df.at[idx, 'final_assessment'] = assessment

        except Exception as e:
            print(f"  ❌ Error analyzing company: {str(e)}")
            continue

    # Save results
    print("\n" + "=" * 80)
    print("💾 Saving results...")
    df.to_excel(output_file, index=False)

    # Generate summary
    print("📝 Generating summary...")

    # Convert numeric columns to proper type for summary calculations
    df['final_score'] = pd.to_numeric(df['final_score'], errors='coerce')
    df['founder_problem_fit_score'] = pd.to_numeric(df['founder_problem_fit_score'], errors='coerce')
    df['product_score'] = pd.to_numeric(df['product_score'], errors='coerce')
    df['market_score'] = pd.to_numeric(df['market_score'], errors='coerce')

    summary = []
    summary.append("=" * 80)
    summary.append("STARTUP ANALYSIS SUMMARY")
    summary.append("=" * 80)
    summary.append("")
    summary.append(f"Total companies analyzed: {len(df)}")
    summary.append(f"Analysis date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    summary.append("")
    summary.append("SCORE DISTRIBUTION:")
    summary.append(f"  Average final score: {df['final_score'].mean():.1f}/10")
    summary.append(f"  Median final score: {df['final_score'].median():.1f}/10")
    summary.append(f"  Highest score: {df['final_score'].max():.1f}/10")
    summary.append(f"  Lowest score: {df['final_score'].min():.1f}/10")
    summary.append("")
    summary.append("LEAD QUALITY BREAKDOWN:")
    summary.append(f"  🔥 HOT (9-10): {len(df[df['final_score'] >= 9])} companies")
    summary.append(f"  💪 STRONG (7-8): {len(df[(df['final_score'] >= 7) & (df['final_score'] < 9)])} companies")
    summary.append(f"  ⚠️  MODERATE (5-6): {len(df[(df['final_score'] >= 5) & (df['final_score'] < 7)])} companies")
    summary.append(f"  ❌ WEAK (<5): {len(df[df['final_score'] < 5])} companies")
    summary.append("")
    summary.append("COMPONENT AVERAGES:")
    summary.append(f"  Founder-Problem Fit (45%): {df['founder_problem_fit_score'].mean():.1f}/10")
    summary.append(f"  Product Clarity (10%): {df['product_score'].mean():.1f}/10")
    summary.append(f"  Market Momentum (45%): {df['market_score'].mean():.1f}/10")
    summary.append("")
    summary.append("=" * 80)
    summary.append("TOP 10 LEADS")
    summary.append("=" * 80)
    summary.append("")

    # Top 10
    top_10 = df.nlargest(10, 'final_score')
    for idx, row in top_10.iterrows():
        summary.append(f"{row['final_score']:.1f}/10 - {row['Company Name']}")
        summary.append(f"  Founder: {row['First Name']} {row['Last Name']} ({row['Position']})")
        summary.append(f"  Website: {row.get('Company Website', 'N/A')}")
        summary.append(f"  Assessment: {row['final_assessment']}")
        summary.append("")

    # Save summary
    with open(summary_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(summary))
