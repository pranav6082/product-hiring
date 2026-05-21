"""
LinkedIn feed scraper — reads Pranav's feed for hiring signals.
Run: python scraper.py
Expects: LINKEDIN_COOKIES_FILE and DATABASE_URL in .env
"""

import os
import json
import re
import uuid
import psycopg2
from datetime import datetime, timezone
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]
COOKIES_FILE = os.environ.get("LINKEDIN_COOKIES_FILE", "linkedin_cookies.json")

# Keywords that signal real urgency
URGENCY_KEYWORDS = [
    "urgent", "immediately", "immediate joiner", "asap", "as soon as possible",
    "critical hire", "priority hire", "been looking", "months open",
    "backfill", "last minute", "closing soon", "interviewing now",
]

# Keywords that indicate a post is about hiring
HIRING_KEYWORDS = [
    "hiring", "we're looking", "we are looking", "looking for a",
    "join our team", "open role", "open position", "job opening",
    "now hiring", "actively hiring", "we need a", "seeking a",
    "referral", "refer someone", "know anyone", "DM me",
    "product manager", "product designer", "data analyst",
    "PM role", "design role", "head of product",
    "chief of staff", "entrepreneur in residence", "founding team",
    "co-founder", "failed founder", "ex-founder", "former founder",
    "founding member", "early team", "0 to 1", "zero to one",
]

# Domains to classify
PM_KEYWORDS = ["product manager", "pm ", " pm,", "head of product", "vp product", "cpo", "group pm", "senior pm"]
DESIGN_KEYWORDS = ["product designer", "ux designer", "ui designer", "design lead", "head of design"]
DATA_KEYWORDS = ["data analyst", "data scientist", "analytics", "product data", "growth analyst"]
STRATEGY_KEYWORDS = [
    "chief of staff", "entrepreneur in residence", "eir", "head of strategy",
    "founding team", "founding member", "co-founder", "cofounder",
    "failed founder", "ex-founder", "former founder", "early team",
    "0 to 1", "zero to one", "venture builder", "new venture",
]
TARGET_FUNCTIONS = {"pm", "design", "data", "strategy"}

LOCATION_ALIASES = {
    "bangalore": ("Bengaluru", "Karnataka", "India"),
    "bengaluru": ("Bengaluru", "Karnataka", "India"),
    "mumbai": ("Mumbai", "Maharashtra", "India"),
    "delhi": ("Delhi", "Delhi", "India"),
    "gurgaon": ("Gurugram", "Haryana", "India"),
    "gurugram": ("Gurugram", "Haryana", "India"),
    "hyderabad": ("Hyderabad", "Telangana", "India"),
    "pune": ("Pune", "Maharashtra", "India"),
    "chennai": ("Chennai", "Tamil Nadu", "India"),
    "noida": ("Noida", "Uttar Pradesh", "India"),
}


def classify_domain(text: str) -> str:
    t = text.lower()
    if any(k in t for k in PM_KEYWORDS):
        return "pm"
    if any(k in t for k in DESIGN_KEYWORDS):
        return "design"
    if any(k in t for k in DATA_KEYWORDS):
        return "data"
    if any(k in t for k in STRATEGY_KEYWORDS):
        return "strategy"
    return "other"


def extract_urgency(text: str) -> list[str]:
    t = text.lower()
    return [k for k in URGENCY_KEYWORDS if k in t]


def is_hiring_post(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in HIRING_KEYWORDS)


def extract_location(text: str) -> str:
    locations = [
        "bangalore", "bengaluru", "mumbai", "delhi", "gurgaon", "gurugram",
        "hyderabad", "pune", "chennai", "noida", "remote", "india",
        "new york", "san francisco", "london", "singapore",
    ]
    t = text.lower()
    for loc in locations:
        if loc in t:
            return loc.title()
    return "India"


def normalize_location(raw_location: str, raw_text: str) -> tuple[str, str, str, str, float]:
    combined = f"{raw_location} {raw_text}".lower()
    if "remote" in combined or "work from home" in combined or "wfh" in combined:
        return ("Remote", "Remote", "India", "remote", 0.95)
    if "hybrid" in combined:
        city = extract_location(combined)
        return (city, "Hybrid", "India", "hybrid", 0.9)
    for alias, (city, region, country) in LOCATION_ALIASES.items():
        if alias in combined:
            return (city, region, country, "onsite", 0.9)
    return ("India", "Unknown", "India", "unknown", 0.6)


def normalize_seniority(title: str) -> tuple[str, float]:
    t = title.lower()
    if any(k in t for k in ["chief", "cpo", "vp", "vice president", "head of", "director"]):
        return ("executive", 0.95)
    if any(k in t for k in ["principal", "staff", "group"]):
        return ("staff", 0.9)
    if any(k in t for k in ["lead", "senior", "sr "]):
        return ("senior", 0.9)
    if any(k in t for k in ["associate", "junior", "jr "]):
        return ("junior", 0.9)
    return ("mid", 0.75)


def normalize_company(raw_company: str) -> tuple[str, float]:
    norm = re.sub(r"\s+", " ", raw_company or "").strip()
    if not norm:
        return ("Unknown", 0.3)
    return (norm.title() if norm.islower() else norm, 0.85)


def normalize_title(raw_title: str, domain: str) -> tuple[str, str, float]:
    title = re.sub(r"\s+", " ", raw_title or "").strip()
    if not title:
        defaults = {"pm": "Product Manager", "design": "Product Designer", "data": "Product Data Analyst"}
        return (defaults.get(domain, "Open Role"), domain, 0.4)
    norm_function = domain if domain in TARGET_FUNCTIONS else "other"
    return (title, norm_function, 0.85)


def compute_review_status(norm_function: str, confidence: float, norm_company: str, norm_title: str) -> tuple[str, bool]:
    if norm_function not in TARGET_FUNCTIONS:
        return ("rejected", False)
    if norm_company == "Unknown" or norm_title.lower() in {"open role", "hiring"}:
        return ("pending", True)
    if confidence >= 0.82:
        return ("approved", False)
    return ("pending", True)


def is_explicit_india_remote(raw_text: str, location: str) -> bool:
    combined = f"{raw_text} {location}".lower()
    required_signals = [
        "india remote", "remote india", "india timezone", "ist timezone",
        "within india", "anywhere in india", "india-based remote", "remote (india)",
        "work from india", "india candidates only",
    ]
    return any(sig in combined for sig in required_signals)


def is_us_or_non_india_listing(raw_text: str, location: str) -> bool:
    combined = f"{raw_text} {location}".lower()
    us_signals = [
        "united states", " usa", " us ", "new york", "san francisco", "seattle",
        "austin", "california", "boston", "chicago", "los angeles",
    ]
    india_signals = [
        "india", "bengaluru", "bangalore", "mumbai", "delhi", "gurgaon", "gurugram",
        "hyderabad", "pune", "chennai", "noida",
    ]
    has_us = any(sig in combined for sig in us_signals)
    has_india = any(sig in combined for sig in india_signals)
    return has_us and not has_india


def extract_company(text: str, poster_company: str) -> str:
    if poster_company:
        return poster_company
    # Look for "at <Company>" pattern
    match = re.search(r'\bat\s+([A-Z][A-Za-z0-9&\s]+?)[\s,\.\!]', text)
    if match:
        return match.group(1).strip()
    return "Unknown"


def extract_title(text: str) -> str:
    # Try to find "hiring a/an <Title>" or "looking for a <Title>"
    patterns = [
        r'hiring\s+(?:a\s+|an\s+)?([A-Z][A-Za-z\s]+?)(?:\s+at|\s+for|\s+to|[,\.\!])',
        r'looking for\s+(?:a\s+|an\s+)?([A-Z][A-Za-z\s]+?)(?:\s+at|\s+for|\s+to|[,\.\!])',
        r'open(?:ing)?\s+for\s+(?:a\s+|an\s+)?([A-Z][A-Za-z\s]+?)(?:\s+at|\s+for|\s+to|[,\.\!])',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            title = match.group(1).strip()
            if 3 < len(title) < 60:
                return title
    # Fallback: domain-based
    domain = classify_domain(text)
    defaults = {"pm": "Product Manager", "design": "Product Designer", "data": "Data Analyst", "other": "Open Role"}
    return defaults[domain]


def get_or_create_source(cur, name: str) -> str:
    cur.execute("SELECT id FROM sources WHERE name = %s", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    source_id = str(uuid.uuid4())
    cur.execute("INSERT INTO sources (id, name, is_active) VALUES (%s, %s, true)", (source_id, name))
    return source_id


def get_or_create_person(cur, name: str, linkedin_url: str | None) -> str:
    if linkedin_url:
        cur.execute("SELECT id FROM people WHERE linkedin_url = %s", (linkedin_url,))
    else:
        cur.execute("SELECT id FROM people WHERE name = %s", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    person_id = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO people (id, name, linkedin_url) VALUES (%s, %s, %s)",
        (person_id, name, linkedin_url),
    )
    return person_id


def get_or_create_job(
    cur,
    title: str,
    company: str,
    location: str,
    domain: str,
    job_url: str | None,
    raw_employment_type: str | None,
    norm_title: str,
    norm_company: str,
    norm_location_city: str,
    norm_location_region: str,
    norm_location_country: str,
    norm_remote_type: str,
    norm_seniority: str,
    norm_function: str,
    normalization_confidence: float,
    needs_review: bool,
    review_status: str,
) -> str:
    cur.execute(
        "SELECT id FROM jobs WHERE company = %s AND title = %s",
        (company, title),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    job_id = str(uuid.uuid4())
    cur.execute(
        """
        INSERT INTO jobs (
          id, title, company, location, domain, job_url,
          raw_title, raw_company, raw_location, raw_employment_type,
          norm_title, norm_company, norm_location_city, norm_location_region, norm_location_country,
          norm_remote_type, norm_seniority, norm_function, normalization_confidence,
          needs_review, review_status
        ) VALUES (
          %s, %s, %s, %s, %s, %s,
          %s, %s, %s, %s,
          %s, %s, %s, %s, %s,
          %s, %s, %s, %s,
          %s, %s
        )
        """,
        (
            job_id, title, company, location, domain, job_url,
            title, company, location, raw_employment_type,
            norm_title, norm_company, norm_location_city, norm_location_region, norm_location_country,
            norm_remote_type, norm_seniority, norm_function, normalization_confidence,
            needs_review, review_status,
        ),
    )
    return job_id


def signal_exists(cur, signal_url: str) -> bool:
    cur.execute("SELECT 1 FROM signals WHERE signal_url = %s", (signal_url,))
    return cur.fetchone() is not None


def save_signal(cur, job_id: str, source_id: str, person_id: str | None,
                signal_url: str | None, profile_url: str | None,
                raw_text: str, urgency: list[str], post_date: datetime | None):
    cur.execute(
        """
        INSERT INTO signals
          (job_id, source_id, person_id, signal_url, profile_url,
           raw_text, urgency_signals, post_date, validated)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, true)
        """,
        (job_id, source_id, person_id, signal_url, profile_url,
         raw_text, urgency, post_date),
    )


def scrape_feed(page) -> list[dict]:
    print("Navigating to LinkedIn feed...")
    try:
        page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        if "ERR_HTTP_RESPONSE_CODE_FAILURE" in str(e):
            raise Exception("LinkedIn returned HTTP error (likely 429 rate limit). Wait a few hours and retry.")
        raise
    page.wait_for_timeout(5000)

    # Confirm we're actually on the feed, not a login/challenge page
    current_url = page.url
    print(f"Current URL: {current_url}")
    if "login" in current_url or "checkpoint" in current_url or "authwall" in current_url:
        raise Exception(f"LinkedIn redirected to auth/challenge page: {current_url}. Re-export cookies.")

    # Wait for feed to load
    try:
        page.wait_for_selector("div.feed-shared-update-v2, div[data-urn]", timeout=15000)
    except Exception:
        print("Warning: standard feed selectors not found, proceeding anyway...")

    posts = []
    seen_urls = set()

    # Scroll and collect posts
    for scroll in range(8):
        page.evaluate("window.scrollBy(0, 1200)")
        page.wait_for_timeout(2500)

        # Guard against navigation mid-scroll
        if "login" in page.url or "checkpoint" in page.url:
            print(f"Redirected mid-scroll to {page.url}, stopping.")
            break

        try:
            # Try multiple selector strategies for LinkedIn's changing DOM
            post_elements = (
                page.query_selector_all('div[data-urn*="activity"]') or
                page.query_selector_all('div.feed-shared-update-v2') or
                page.query_selector_all('li.occludable-update')
            )
        except Exception:
            print(f"  Selector error on scroll {scroll+1}, continuing...")
            continue

        for el in post_elements:
            try:
                raw_text = el.inner_text()
                if not raw_text or not is_hiring_post(raw_text):
                    continue

                # Post URL
                link_el = el.query_selector('a[href*="/feed/update/"]')
                post_url = link_el.get_attribute("href").split("?")[0] if link_el else None
                if post_url in seen_urls:
                    continue
                if post_url:
                    seen_urls.add(post_url)

                # Poster name + profile — try multiple selector patterns
                actor_el = el.query_selector(
                    '.update-components-actor__name span[aria-hidden="true"], '
                    '.feed-shared-actor__name, '
                    '.update-components-actor__name'
                )
                poster_name = actor_el.inner_text().strip() if actor_el else "Unknown"

                profile_link = el.query_selector(
                    'a[href*="/in/"].update-components-actor__meta-link, '
                    'a[href*="/in/"].feed-shared-actor__meta-link, '
                    'a.update-components-actor__meta-link, '
                    'a.app-aware-link[href*="/in/"]'
                )
                profile_url = None
                if profile_link:
                    href = profile_link.get_attribute("href")
                    if href and "/in/" in href:
                        profile_url = ("https://www.linkedin.com" + href if href.startswith("/") else href).split("?")[0]

                # Poster's company (subtitle)
                subtitle_el = el.query_selector('.update-components-actor__description, .feed-shared-actor__description')
                poster_subtitle = subtitle_el.inner_text().strip() if subtitle_el else ""

                posts.append({
                    "raw_text": raw_text,
                    "post_url": post_url,
                    "poster_name": poster_name,
                    "profile_url": profile_url,
                    "poster_subtitle": poster_subtitle,
                })
                print(f"  Found hiring post from {poster_name}: {raw_text[:80]}...")

            except Exception as e:
                continue

        print(f"  Scroll {scroll + 1}/8 — {len(posts)} hiring posts so far")

    return posts


def run():
    print("Starting LinkedIn feed scraper...")

    # Load and normalize cookies
    with open(COOKIES_FILE) as f:
        raw_cookies = json.load(f)

    # Normalize domains so li_at is sent on all linkedin.com requests
    cookies = []
    for c in raw_cookies:
        cookie = dict(c)
        if cookie.get("domain", "").endswith("linkedin.com"):
            cookie["domain"] = ".linkedin.com"
        cookies.append(cookie)

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    source_id = get_or_create_source(cur, "linkedin_feed")
    conn.commit()

    saved = 0

    # Use storage state if available (preferred), else fall back to cookies
    storage_file = COOKIES_FILE.replace("cookies.json", "storage.json")
    use_storage = os.path.exists(storage_file)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        ctx_kwargs = dict(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            timezone_id="Asia/Kolkata",
        )
        ctx = browser.new_context(**ctx_kwargs)
        if use_storage:
            print(f"Using storage state from {storage_file}")
            # Load cookies from storage state and normalize domains
            with open(storage_file) as sf:
                storage_data = json.load(sf)
            normalized = []
            for c in storage_data.get("cookies", []):
                cookie = dict(c)
                if cookie.get("domain", "").endswith("linkedin.com"):
                    cookie["domain"] = ".linkedin.com"
                normalized.append(cookie)
            ctx.add_cookies(normalized)
        else:
            ctx.add_cookies(cookies)
        page = ctx.new_page()
        Stealth().apply_stealth_sync(page)

        posts = scrape_feed(page)
        browser.close()

    print(f"\nProcessing {len(posts)} hiring posts...")

    for post in posts:
        try:
            raw_text = post["raw_text"]
            post_url = post["post_url"]

            if post_url and signal_exists(cur, post_url):
                print(f"  Skipping duplicate: {post_url}")
                continue

            title = extract_title(raw_text)
            company = extract_company(raw_text, post["poster_subtitle"])
            location = extract_location(raw_text)
            domain = classify_domain(raw_text)
            urgency = extract_urgency(raw_text)
            if is_us_or_non_india_listing(raw_text, location) and not is_explicit_india_remote(raw_text, location):
                continue
            norm_title, norm_function, title_conf = normalize_title(title, domain)
            norm_company, company_conf = normalize_company(company)
            norm_city, norm_region, norm_country, norm_remote_type, loc_conf = normalize_location(location, raw_text)
            norm_seniority, seniority_conf = normalize_seniority(title)
            normalization_confidence = round((title_conf + company_conf + loc_conf + seniority_conf) / 4, 3)
            review_status, needs_review = compute_review_status(
                norm_function, normalization_confidence, norm_company, norm_title
            )

            person_id = get_or_create_person(cur, post["poster_name"], post["profile_url"])
            job_id = get_or_create_job(
                cur,
                title,
                company,
                location,
                domain,
                None,
                None,
                norm_title,
                norm_company,
                norm_city,
                norm_region,
                norm_country,
                norm_remote_type,
                norm_seniority,
                norm_function,
                normalization_confidence,
                needs_review,
                review_status,
            )
            save_signal(
                cur, job_id, source_id, person_id,
                post_url, post["profile_url"],
                raw_text, urgency, datetime.now(timezone.utc),
            )
            conn.commit()
            saved += 1
            print(f"  Saved: {title} at {company} ({location}) — posted by {post['poster_name']}")

        except Exception as e:
            conn.rollback()
            print(f"  Error saving post: {e}")

    cur.close()
    conn.close()
    print(f"\nDone. {saved} new signals saved.")


if __name__ == "__main__":
    run()
