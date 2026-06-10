"""
Multi-source hiring pipeline.
Runs all sources, deduplicates, saves to Neon.
Usage: python pipeline.py [--source all|jobspy|greenhouse|lever|naukri|hn]
Designed to run every 10-30 minutes via scheduler.
"""

import os
import re
import sys
import uuid
import requests
import psycopg2
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

SOURCE_ARG = sys.argv[1].replace("--source=", "").replace("--source ", "") if len(sys.argv) > 1 else "all"

# Classify based on TITLE ONLY — keeps noise out.
# Keywords must appear in the job title itself, not buried in description.
# v0.1 SCOPE: PM only. Other domain dicts retained for v0.2+ but not classified into.
TITLE_DOMAINS = {
    "pm": [
        "product manager", "head of product", "vp of product", "vp product",
        "chief product", "cpo", "group product manager", "group pm",
        "director of product", "director, product", "director product management",
        "lead product manager", "principal product manager", "staff product manager",
        "associate product manager", "associate pm", "senior product manager", "sr product manager",
        "sr. product manager", "lead pm", "principal pm", "staff pm", "group pm",
        "founding product manager", "founding pm",
        "tpm", "product lead", "product director", "product head", "product management", "product strategy",
        "growth product manager", "platform product manager", "ai product manager",
        "product lead", "product management lead", "product management director",
        "product management head", "product management vp", "product management chief",
        "product owner", "product lead", "product specialist", "product strategy manager",
        "product portfolio manager", "product development manager", "product innovation manager",
    ],
    "design": [
        "product designer", "ux designer", "ui designer", "ui/ux designer",
        "ux/ui designer", "design lead", "head of design", "design manager",
        "visual designer",
    ],
    "data": [
        "product analyst", "growth analyst", "data analyst", "analytics manager",
        "product data", "analytics lead", "growth manager",
    ],
    "strategy": [
        # v0.4 scope: Founder's Office / Chief of Staff only.
        "chief of staff", "chief of staff to", "cos",
        "founder's office", "founders office", "founder office", "office of the founder", "fo",
        "chief of staff, ", "chief of staff to the", "chief of staff to a",
        "office of founder", "office of ceo", "office of the ceo",
        "entrepreneur in residence", "eir", " eir ",
        "head of special projects", "special projects lead", "special projects manager",
        "chief of staff to ceo", "chief of staff to founder", "chief of staff to the ceo", "chief of staff to the founder",
        "founding team", "founding member", "founding associate", "founding operations",
    ],
}

# Hard blocklist — if any of these appear in title, REJECT before any domain match.
# Engineering, marketing, sales, ops, etc. are explicitly out of v0.1.
ENGINEERING_TITLES = [
    "software development engineer", "software engineer", "full stack engineer",
    "fullstack engineer", "backend engineer", "frontend engineer",
    "back-end engineer", "front-end engineer", "back end developer", "front end developer",
    "devops engineer", "data engineer", "analytics engineer",
    "site reliability", " sre ", "sre,", "qa engineer", "test engineer",
    "automation engineer", "android engineer", "ios engineer",
    "mobile engineer", "embedded engineer", "machine learning engineer",
    "ml engineer", "ai engineer", "security engineer",
    "platform engineer", "infrastructure engineer", "cloud engineer",
    "solutions architect", "software architect", "systems architect",
    "principal engineer", "staff engineer", "senior software", "sr software",
    "sr. software", "lead engineer", "lead developer",
    "software developer", "java developer", "python developer",
    "node.js developer", "react developer", "rust developer",
    "blockchain", "smart contract", "wordpress",
]

NON_PRODUCT_ROLES = [
    # Marketing / Sales / GTM
    "marketing manager", "growth marketing", "sales manager", "sales development",
    "account manager", "account executive", "customer success",
    "business development", "partner manager", "campaign manager",
    "performance marketing", "content marketing", "brand manager",
    # Project / programme (≠ product)
    "project manager", "project management", "programme manager", "program manager",
    # HR / Finance / Legal / Ops
    "hr ", "human resources", "talent acquisition", "recruiter",
    "finance manager", "controller", "tax manager", "legal counsel",
    "compliance", "audit", "operations manager", "ops manager",
    # Misc roles that have leaked through
    "implementation lead", "implementation manager", "delivery manager",
    "technical writer", "executive assistant", "office manager",
    # Corporate strategy titles — explicitly out of scope per SPEC.md v0.4
    "strategy consultant", "management consultant",
    "vp strategy", "vp of strategy", "vp, strategy",
    "director of strategy", "chief strategy officer",
    "chief business officer",
    "head of strategy",
]

# Block words — if title contains these, skip regardless of domain match
NOISE_TITLES = [
    "industrial", "manufacturing", "safety", "footwear", "gloves", "warehouse",
    "intern", "internship", "fresher", "graduate trainee", "trainee",
    "seo", "social media", "content", "copywriter", "sales analyst",
    "security analyst", "pricing analyst", "marketing analyst", "financial analyst",
    "business analyst", "system analyst", "hr analyst",
    "printer operator", "delivery driver",
]
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


def normalize_location(raw_location: str) -> tuple[str, str, str, str, float]:
    loc = (raw_location or "").lower()
    if "remote" in loc:
        return ("Remote", "Remote", "India", "remote", 0.95)
    if "hybrid" in loc:
        return ("India", "Hybrid", "India", "hybrid", 0.85)
    for alias, (city, region, country) in LOCATION_ALIASES.items():
        if alias in loc:
            return (city, region, country, "onsite", 0.9)
    return ("India", "Unknown", "India", "unknown", 0.6)


def normalize_seniority(title: str) -> tuple[str, float]:
    t = title.lower()
    if any(k in t for k in ["chief", "cpo", "vp", "vice president", "head of", "director", "chief of staff", "entrepreneur in residence", "eir"]):
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


def clean_title(raw_title: str) -> str:
    """Strip common pollution from titles: source suffixes, application prefixes."""
    if not raw_title:
        return raw_title
    t = raw_title
    # Remove "Job Application for " prefix (Greenhouse)
    t = re.sub(r'^Job\s+Application\s+for\s+', '', t, flags=re.IGNORECASE)
    # Remove trailing source/board attribution: " - iimjobs.com", " | jobs.lever.co", " - Naukri.com"
    t = re.sub(r'\s*[-|]\s*(?:iimjobs\.com|naukri\.com|instahyre\.com|wellfound\.com|'
               r'jobs\.lever\.co|job-boards\.greenhouse\.io|greenhouse\.io|lever\.co|'
               r'linkedin\.com|in\.linkedin\.com|glassdoor\.com|indeed\.com|'
               r'builtinnyc\.com|adzuna\.in|theorg\.com).*$', '',
               t, flags=re.IGNORECASE)
    # Remove "View Jobs" trailing fragment from search engines
    t = re.sub(r'\s+View\s+Jobs?\s*$', '', t, flags=re.IGNORECASE)
    # Collapse whitespace
    t = re.sub(r'\s+', ' ', t).strip()
    return t


JUNK_COMPANY_NAMES = {
    "unknown", "usa", "us", "uk", "canada", "europe", "global",
    "remote", "india", "group", "lead", "senior", "junior", "nan",
    "wellfound.com", "iimjobs.com", "instahyre.com", "linkedin.com",
}

def compute_review_status(domain: str, confidence: float, norm_company: str, norm_title: str,
                          india_hiring: str = "unknown") -> tuple[str, bool]:
    if domain not in {"pm", "design", "data", "strategy"}:
        return ("rejected", False)
    # India hiring is required to be confirmed for auto-approval. Unknown → pending.
    if india_hiring == "rejected":
        return ("rejected", False)
    if india_hiring == "unknown":
        return ("pending", True)
    co = (norm_company or "").lower().strip()
    if co in JUNK_COMPANY_NAMES or len(co) < 2:
        return ("pending", True)
    if norm_title.lower() in {"open role", "hiring"}:
        return ("pending", True)
    # india_hiring == "confirmed" + clean fields → auto-approve
    if confidence >= 0.75:
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


INDIA_CITIES = {
    "bengaluru", "bangalore", "mumbai", "delhi", "gurgaon", "gurugram",
    "hyderabad", "pune", "chennai", "noida", "kolkata", "ahmedabad", "kochi",
    "thiruvananthapuram", "trivandrum", "jaipur", "indore", "coimbatore",
}

# Companies with publicly-known India offices — eligible for "Remote" PM roles.
# Lowercase, normalized, partial-match-friendly. Add as we discover more.
# v0.1 starter list — see SPEC.md for the canonical list.
INDIA_OFFICE_COMPANIES = {
    # Indian product startups
    "phonepe", "razorpay", "zepto", "swiggy", "cred", "groww", "zomato",
    "locus", "workindia", "moengage", "chargebee", "browserstack", "freshworks",
    "postman", "hasura", "setu", "smallcase", "fi-money", "fi money", "jupiter",
    "olamoney", "ola", "meesho", "khatabook", "loopai", "dezerv", "juspay",
    "recko", "simpl", "cashfree", "niyo", "kreditbee", "mswipe", "zetwerk",
    "darwinbox", "rupeek", "open financial", "uni cards", "ofbusiness",
    "elasticrun", "delhivery", "acko", "slice", "policybazaar", "cure.fit",
    "urban company", "unacademy", "byju", "vedantu", "pharmeasy", "1mg",
    "lenskart", "nykaa", "dream11", "games24x7", "mobile premier league",
    "paytm", "bharatpe", "pine labs", "whatfix", "innovaccer", "zoho",
    "thoughtspot", "krafton", "krafton india", "appsforbharat", "shaadi",
    "shaadi.com", "hevodata", "hevo data", "hevo", "smytten", "tala",
    "oscilar", "z1tech", "z1 tech", "sigmoid",
    # Global companies with major India product offices
    "microsoft", "google", "amazon", "salesforce", "atlassian", "servicenow",
    "adobe", "linkedin", "stripe", "walmart labs", "flipkart", "uber",
    "booking.com", "cisco", "oracle", "sap", "vmware", "twilio", "snowflake",
    "databricks", "confluent",

    "gohighlevel",  # auto-added by monitor (confirmed 15x)
    "jumpcloud",  # auto-added by monitor (confirmed 3x)
    "eltropyinc",  # auto-added by monitor (confirmed 2x)
    "smartsheet",  # auto-added by monitor (confirmed 2x)
    "conga",  # auto-added by monitor (confirmed 2x)

    "info edge",  # auto-added by monitor (confirmed 2x)
    "fundamento",  # auto-added by monitor (confirmed 2x)
    "gleanwork",  # auto-added by monitor (confirmed 2x)

    "saviynt",  # auto-added by monitor (confirmed 2x)

    "clari",  # auto-added by monitor (confirmed 2x)

    "highlevel",  # auto-added by monitor (confirmed 3x)
    # VC funds with India offices — for EIR roles (v0.4)
    "peak xv", "peak xv partners", "sequoia india",
    "accel india", "accel",
    "lightspeed india", "lightspeed venture",
    "matrix partners india", "matrix partners",
    "blume ventures", "blume",
    "elevation capital",
    "nexus venture partners", "nexus venture",
    "stellaris venture partners", "stellaris",
    "kalaari capital", "kalaari",
    "3one4 capital", "3one4",
    "chiratae ventures", "chiratae",
    "bessemer venture partners",
}


def has_india_office(company: str) -> bool:
    """True if the company has a publicly-known India office."""
    co = (company or "").lower().strip()
    if not co or co in JUNK_COMPANY_NAMES:
        return False
    # Match either exact-equal or whitelist substring inside the company name
    for known in INDIA_OFFICE_COMPANIES:
        if known == co or known in co or co in known:
            return True
    return False

INDIA_REMOTE_SIGNALS = [
    "india remote", "remote india", "ist timezone", "india timezone",
    "within india", "anywhere in india", "india-based", "india office",
    "india team", "we hire in india", "from india", "based in india",
    "(india)", "india,", "employee india",
]

US_ONLY_SIGNALS = [
    "united states only", "us only", "usa only", "us-based only",
    "must be us citizen", "us residents only", "must reside in the us",
    "us residents", "(us)", "us-only", "us authorization",
    "uk only", "uk-based only", "remote (us)", "remote – us", "remote, us",
    "remote (us only)", "remote, united states", "remote in the us",
    "remote (united states", "remote (florida", "remote (texas",
    "remote (california", "remote (canada",
]


def detect_work_mode(raw_text: str, location: str) -> str:
    """Returns 'remote', 'hybrid', 'onsite', or 'unknown'."""
    combined = f"{raw_text} {location}".lower()
    if "hybrid" in combined: return "hybrid"
    if any(s in combined for s in ["fully remote","100% remote","work from home",
                                   "remote-first","remote first"]):
        return "remote"
    if "remote" in combined: return "remote"
    if any(s in combined for s in ["onsite","on-site","in-office","wfo",
                                   "fully onsite","work from office"]):
        return "onsite"
    return "unknown"


def detect_india_city(raw_text: str, location: str) -> str | None:
    combined = f"{raw_text} {location}".lower()
    for city in INDIA_CITIES:
        if city in combined: return city
    return None


def classify_india_hiring(raw_text: str, location: str, company: str = "") -> str:
    """
    Returns one of: 'confirmed' | 'unknown' | 'rejected'.

    confirmed: clear positive India hiring signal
    unknown:   ambiguous — needs enrichment to verify
    rejected:  explicit non-India only

    v0.1 hardening: for remote roles, require either India city/text OR a
    company that's on the India-office whitelist. Pure "Remote" from a
    non-whitelisted company → unknown (will need enrichment to verify).
    """
    combined = f"{raw_text} {location}".lower()

    has_india_city = bool(detect_india_city(raw_text, location))
    has_india_remote = any(s in combined for s in INDIA_REMOTE_SIGNALS)
    has_india_word = "india" in combined

    has_us_only = any(s in combined for s in US_ONLY_SIGNALS)
    company_has_india_office = has_india_office(company)

    work_mode = detect_work_mode(raw_text, location)

    # Onsite + Indian city → confirmed (office exists there)
    if work_mode in ("onsite", "hybrid") and has_india_city:
        return "confirmed"

    # Onsite + non-India city, no India signal → rejected
    if work_mode == "onsite" and not (has_india_city or has_india_word):
        non_india_cities = ["new york","san francisco","seattle","austin","boston",
                            "chicago","los angeles","denver","toronto","london",
                            "berlin","paris","amsterdam","dublin","madrid"]
        if any(c in combined for c in non_india_cities):
            return "rejected"

    # Remote + explicit India signal → confirmed
    if work_mode == "remote" and (has_india_city or has_india_remote):
        return "confirmed"

    # Remote + India-office company (no explicit India language but company has India presence)
    # → confirmed (e.g. Microsoft remote role can hire from India)
    if work_mode == "remote" and company_has_india_office:
        return "confirmed"

    # Remote + explicit US-only signal (and no India mention) → rejected
    if work_mode == "remote" and has_us_only and not has_india_word:
        return "rejected"

    # Remote without India signal AND company not in whitelist → unknown
    # (Per spec: US-remote ≠ India-remote. Default to unknown unless we have evidence.)
    if work_mode == "remote" and not (has_india_city or has_india_remote
                                       or company_has_india_office):
        return "unknown"

    # Has any India mention → confirmed
    if has_india_city or has_india_remote:
        return "confirmed"

    # Explicitly US-only / UK-only / EU-only → rejected
    if has_us_only:
        return "rejected"

    return "unknown"


def has_india_signal(raw_text: str, location: str) -> bool:
    return classify_india_hiring(raw_text, location) == "confirmed"


# Legacy compatibility — used by HN scraper
def is_us_or_non_india_listing(raw_text: str, location: str) -> bool:
    return classify_india_hiring(raw_text, location) == "rejected"

def classify(title: str) -> str | None:
    """
    Returns 'pm', 'strategy', or None.
    Hard-rejects engineering / non-product / noise before any domain match.
    """
    t = title.lower()
    if any(b in t for b in ENGINEERING_TITLES):
        return None
    if any(b in t for b in NON_PRODUCT_ROLES):
        return None
    if any(b in t for b in NOISE_TITLES):
        return None
    for kw in TITLE_DOMAINS["pm"]:
        if kw in t:
            return "pm"
    for kw in TITLE_DOMAINS["strategy"]:
        if kw in t:
            return "strategy"
    return None

def get_source_id(name):
    cur.execute("SELECT id FROM sources WHERE name = %s", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    sid = str(uuid.uuid4())
    cur.execute("INSERT INTO sources (id, name, is_active) VALUES (%s, %s, true)", (sid, name))
    return sid

def job_exists(company, title, job_url=None):
    if job_url:
        cur.execute("SELECT id FROM jobs WHERE job_url = %s", (job_url,))
        row = cur.fetchone()
        if row:
            return row[0]
    cur.execute("SELECT id FROM jobs WHERE company = %s AND title = %s", (company, title))
    row = cur.fetchone()
    return row[0] if row else None

def signal_exists_for_source(job_id, source_id):
    cur.execute("SELECT 1 FROM signals WHERE job_id = %s AND source_id = %s", (job_id, source_id))
    return cur.fetchone() is not None

# India-only job boards — any URL from these is confirmed India regardless of text signals.
INDIA_ONLY_BOARDS = ["iimjobs.com", "instahyre.com"]

def save_job_signal(title, company, location, domain, job_url, source_id, raw_text, employment_type=None, require_india=False):
    try:
        # Clean up common title pollution before saving
        title = clean_title(title)

        # India-only boards bypass the classification check — they only list India roles.
        if any(board in (job_url or "") for board in INDIA_ONLY_BOARDS):
            india_hiring = "confirmed"
        else:
            india_hiring = classify_india_hiring(raw_text, location, company)
        if india_hiring == "rejected":
            return False
        # `require_india` flag → only save confirmed (used by JobSpy/HN which pull globally)
        if require_india and india_hiring != "confirmed":
            return False

        norm_company, company_conf = normalize_company(company)
        norm_city, norm_region, norm_country, norm_remote_type, loc_conf = normalize_location(location)
        norm_seniority, seniority_conf = normalize_seniority(title)
        normalization_confidence = round((company_conf + loc_conf + seniority_conf) / 3, 3)
        review_status, needs_review = compute_review_status(
            domain, normalization_confidence, norm_company, title, india_hiring
        )
        job_id = job_exists(company, title, job_url)
        if not job_id:
            job_id = str(uuid.uuid4())
            cur.execute(
                """
                INSERT INTO jobs (
                  id, title, company, location, domain, job_url, employment_type,
                  raw_title, raw_company, raw_location, raw_employment_type,
                  norm_title, norm_company, norm_location_city, norm_location_region, norm_location_country,
                  norm_remote_type, norm_seniority, norm_function, normalization_confidence, needs_review, review_status,
                  india_hiring
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    job_id, title, company, location, domain, job_url, employment_type,
                    title, company, location, employment_type,
                    title, norm_company, norm_city, norm_region, norm_country,
                    norm_remote_type, norm_seniority, domain, normalization_confidence, needs_review, review_status,
                    india_hiring,
                ),
            )
        if signal_exists_for_source(job_id, source_id):
            return False
        cur.execute(
            """INSERT INTO signals (id, job_id, source_id, signal_url, raw_text, urgency_signals, post_date, validated, briefed)
               VALUES (%s,%s,%s,%s,%s,%s,%s,true,false)""",
            (str(uuid.uuid4()), job_id, source_id, job_url, raw_text[:2000], [], datetime.now(timezone.utc)),
        )
        return True
    except Exception as e:
        conn.rollback()
        return False


# ─── Source 1: JobSpy (LinkedIn Jobs + Indeed + Google) ───────────────────────

def run_jobspy(query_suffix: str = ""):
    from jobspy import scrape_jobs
    source_id = get_source_id("jobspy_indeed")
    saved = 0
    searches = [
        # PM (v0.1)
        ("product manager", "India"),
        ("senior product manager", "India"),
        ("senior product manager", "Bangalore"),
        ("staff product manager", "India"),
        ("principal product manager", "India"),
        ("group product manager", "India"),
        ("head of product", "India"),
        ("director of product", "India"),
        ("vp product", "India"),
        # Founder's Office / Chief of Staff (v0.4)
        ("chief of staff", "India"),
        ("chief of staff", "Bangalore"),
        ("founders office", "India"),
        ("entrepreneur in residence", "India"),
    ]
    for term, loc in searches:
        try:
            jobs = scrape_jobs(
                site_name=["indeed", "google"],
                search_term=term,
                location=loc,
                results_wanted=20,
                hours_old=72,
                country_indeed="India",
            )
            for _, row in jobs.iterrows():
                title = str(row.get("title", "")).strip()
                company = str(row.get("company", "Unknown")).strip()
                location = str(row.get("location", "India")).strip()
                job_url = str(row.get("job_url", "")) or None
                description = str(row.get("description", ""))[:1000]
                emp_type = str(row.get("job_type", "")) or None

                domain = classify(title)
                if not domain:
                    continue

                # JobSpy returns global results — require an India city signal
                if save_job_signal(title, company, location, domain, job_url, source_id,
                                   description, emp_type, require_india=True):
                    saved += 1
                    print(f"  [jobspy] {title} @ {company} ({location})")
        except Exception as e:
            print(f"  [jobspy] Error for '{term}': {e}")
    conn.commit()
    print(f"  [jobspy] {saved} new jobs saved")
    return saved


# ─── Source 2: Greenhouse public job boards (known Indian companies) ──────────

GREENHOUSE_COMPANIES = [
    "phonepe", "razorpay", "zepto", "swiggy", "cred", "groww",
    "zomato", "locus", "workindia", "moengage", "chargebee",
    "browserstack", "freshworks", "postman", "hasura", "setu",
    "smallcase", "fi-money", "jupiter", "olamoney",
    # Note: "slice" removed — slice.careers is a US pizza company, not Slice India fintech
]

def run_greenhouse():
    source_id = get_source_id("greenhouse")
    saved = 0
    for company in GREENHOUSE_COMPANIES:
        try:
            url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs?content=true"
            r = requests.get(url, timeout=10)
            if r.status_code != 200:
                continue
            data = r.json()
            for job in data.get("jobs", []):
                title = job.get("title", "").strip()
                location = job.get("location", {}).get("name", "India")
                job_url = job.get("absolute_url", "")
                description = re.sub(r'<[^>]+>', ' ', job.get("content", ""))[:1000]

                domain = classify(title)
                if not domain:
                    continue

                if save_job_signal(title, company.title(), location, domain, job_url, source_id, description):
                    saved += 1
                    print(f"  [greenhouse] {title} @ {company.title()} ({location})")
        except Exception as e:
            print(f"  [greenhouse] Error for {company}: {e}")
    conn.commit()
    print(f"  [greenhouse] {saved} new jobs saved")
    return saved


# ─── Source 3: Lever public job boards (Meesho, HighLevel, others) ────────────

LEVER_COMPANIES = [
    # Indian product startups only — no US/EU companies
    "meesho", "khatabook", "razorpay",
    "loopai", "dezerv", "juspay", "recko", "simpl",
    "cashfree", "niyo", "kreditbee", "mswipe",
    "zetwerk", "darwinbox", "rupeek", "open-financial",
    "uni-cards", "ofbusiness", "elasticrun", "delhivery",
]

def run_lever():
    source_id = get_source_id("lever")
    saved = 0
    for company in LEVER_COMPANIES:
        try:
            url = f"https://api.lever.co/v0/postings/{company}?mode=json"
            r = requests.get(url, timeout=10)
            if r.status_code != 200:
                continue
            jobs = r.json()
            for job in jobs:
                title = job.get("text", "").strip()
                location = job.get("categories", {}).get("location", "India")
                job_url = job.get("hostedUrl", "")
                description = job.get("descriptionPlain", "")[:1000]

                domain = classify(title)
                if not domain:
                    continue

                if save_job_signal(title, company.title(), location, domain, job_url, source_id, description):
                    saved += 1
                    print(f"  [lever] {title} @ {company.title()} ({location})")
        except Exception as e:
            print(f"  [lever] Error for {company}: {e}")
    conn.commit()
    print(f"  [lever] {saved} new jobs saved")
    return saved


# ─── Source 4: Hacker News "Who is Hiring" (Algolia API, no auth) ─────────────

HN_THREAD_IDS = {
    "april_2026": 47601859,
    "march_2026": 47219668,
    "february_2026": 46857488,
}

def run_hn():
    source_id = get_source_id("hn_hiring")
    saved = 0
    for month, item_id in HN_THREAD_IDS.items():
        try:
            # Fetch all top-level comments (job posts) via Algolia HN API
            url = f"https://hn.algolia.com/api/v1/search?tags=comment,story_{item_id}&hitsPerPage=500"
            r = requests.get(url, timeout=15)
            if r.status_code != 200:
                continue
            hits = r.json().get("hits", [])
            for hit in hits:
                text = hit.get("comment_text", "") or ""
                text_plain = re.sub(r'<[^>]+>', ' ', text).strip()
                if not text_plain or len(text_plain) < 50:
                    continue

                # HN is US-heavy — require explicit India city, not just "remote"
                t_lower = text_plain.lower()
                india_cities = ["india", "bengaluru", "bangalore", "mumbai", "delhi",
                                "gurgaon", "gurugram", "hyderabad", "pune", "chennai", "noida"]
                if not any(k in t_lower for k in india_cities):
                    continue

                # Skip if it looks like a US/Canada/UK only listing
                if is_us_or_non_india_listing(text_plain, ""):
                    continue

                # Look for an actual PM title line in the comment (not just keyword match anywhere).
                # HN PM posts typically write "Product Manager", "Senior PM" etc. on its own line
                # or in the second pipe-separated field.
                pm_title_match = re.search(
                    r'\b((?:senior|sr\.?|staff|principal|lead|group|associate|head of|vp(?: of)?|chief|director(?:[ ,]of)?)\s+)?'
                    r'product\s+manager(?:\s*[-–,:]\s*[A-Za-z0-9 &]+)?',
                    text_plain, re.IGNORECASE
                )
                if not pm_title_match:
                    # Also accept "PM" as standalone word
                    if not re.search(r'\bPM\b', text_plain):
                        continue

                # Run through classify on the matched title text only
                title_text = pm_title_match.group(0) if pm_title_match else "Product Manager"
                domain = classify(title_text)
                if not domain:
                    continue

                # Extract company from first line (HN format: "Company | Role | Location")
                first_line = text_plain.split('\n')[0][:100]
                first_line_clean = re.sub(r'&#x[0-9A-Fa-f]+;|https?://\S+', '', first_line)
                parts = [p.strip() for p in re.split(r'\|', first_line_clean)]
                company = parts[0][:80] if parts else "Unknown"
                if re.search(r'&#|https?:|&amp;', company):
                    continue
                # Skip if company looks like a work-mode/location label (not a real name)
                bad_company_words = ["full-time","part-time","remote","onsite","hybrid",
                                     "us-based","us based","uk-based","worldwide"]
                if any(b in company.lower() for b in bad_company_words):
                    continue

                # Use the matched PM title as the displayed title
                title_guess = title_text.strip()[:80]

                location = "India"
                job_url = f"https://news.ycombinator.com/item?id={hit.get('objectID', item_id)}"

                if save_job_signal(title_guess, company, location, domain, job_url, source_id, text_plain[:1000]):
                    saved += 1
                    print(f"  [hn] {title_guess} @ {company}")
        except Exception as e:
            print(f"  [hn] Error for {month}: {e}")
    conn.commit()
    print(f"  [hn] {saved} new jobs saved")
    return saved


# ─── Source 5: Naukri (HTML scrape) ───────────────────────────────────────────

NAUKRI_SEARCHES = [
    ("product-manager-jobs", "product manager"),
    ("product-designer-jobs", "product designer"),
    ("ux-designer-jobs-in-bangalore", "ux designer"),
    ("data-analyst-jobs", "data analyst"),
    ("growth-analyst-jobs", "growth analyst"),
]

def run_naukri():
    source_id = get_source_id("naukri")
    saved = 0
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "system-candidate": "true",
        "appid": "109",
        "clientid": "d3skt0p",
    }
    for slug, search_term in NAUKRI_SEARCHES:
        try:
            # Naukri's internal API
            url = f"https://www.naukri.com/jobapi/v3/search?noOfResults=20&urlType=search_by_keyword&searchType=adv&keyword={requests.utils.quote(search_term)}&location=india&experience=0&k={requests.utils.quote(search_term)}&l=india"
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code != 200:
                continue
            data = r.json()
            jobs = data.get("jobDetails", [])
            for job in jobs:
                title = job.get("title", "").strip()
                company = job.get("companyName", "Unknown").strip()
                location = ", ".join(job.get("placeholders", [{}])[0].get("label", "India").split(",")[:2]) if job.get("placeholders") else "India"
                job_url = job.get("jdURL", "")
                description = job.get("jobDescription", "")[:1000]

                domain = classify(title)
                if not domain:
                    continue

                if save_job_signal(title, company, location, domain, job_url, source_id, description):
                    saved += 1
                    print(f"  [naukri] {title} @ {company} ({location})")
        except Exception as e:
            print(f"  [naukri] Error for '{search_term}': {e}")
    conn.commit()
    print(f"  [naukri] {saved} new jobs saved")
    return saved


# ─── Source 6: Parallel CLI (multi-engine web search) ────────────────────────
# Covers: Greenhouse, Lever, Wellfound/AngelList, iimjobs, Instahyre,
#         company career pages — anything indexed on the web.
# Uses parallel-cli which wraps Google + Bing + alt engines.

PARALLEL_SEARCHES = [
    # PM by seniority (v0.1)
    ("senior product manager India",              ["greenhouse.io", "lever.co", "wellfound.com", "iimjobs.com"]),
    ("staff product manager India",               ["greenhouse.io", "lever.co", "wellfound.com"]),
    ("principal product manager India",           ["greenhouse.io", "lever.co", "wellfound.com"]),
    ("group product manager India",               ["greenhouse.io", "lever.co", "wellfound.com", "iimjobs.com"]),
    ("head of product India",                     ["greenhouse.io", "lever.co", "wellfound.com", "linkedin.com"]),
    ("director of product India",                 ["greenhouse.io", "lever.co", "wellfound.com", "iimjobs.com"]),
    ("vp product India",                          ["greenhouse.io", "lever.co", "wellfound.com", "linkedin.com"]),
    ("chief product officer India",               ["greenhouse.io", "lever.co", "wellfound.com", "linkedin.com"]),
    # PM India-specific boards
    ("product manager bangalore",                 ["iimjobs.com", "instahyre.com"]),
    ("product manager mumbai",                    ["iimjobs.com", "instahyre.com"]),
    ("senior product manager bangalore",          ["iimjobs.com", "instahyre.com"]),
    # Founder's Office / Chief of Staff (v0.4)
    ("chief of staff India startup",              ["greenhouse.io", "lever.co", "wellfound.com", "iimjobs.com", "instahyre.com"]),
    ("chief of staff bangalore",                  ["iimjobs.com", "instahyre.com"]),
    ("founder's office India",                    ["iimjobs.com", "instahyre.com", "wellfound.com"]),
    ("founders office India",                     ["iimjobs.com", "instahyre.com", "wellfound.com"]),
    ("entrepreneur in residence India",           ["wellfound.com", "greenhouse.io", "lever.co"]),
    ("special projects India startup",            ["wellfound.com", "greenhouse.io", "lever.co", "iimjobs.com"]),
]

# Aggregate/search listing pages — skip these, they're not individual jobs
AGGREGATE_URL_PATTERNS = [
    "linkedin.com/jobs/", "jooble.org", "simplyhired",
    "indeed.com/q-", "glassdoor.com/Job/", "glassdoor.com/job/", "monster.com",
    "shine.com/job-search", "naukri.com/", "timesjobs.com",
    "wellfound.com/role/", "wellfound.com/jobs?",         # listing pages
    "iimjobs.com/k/", "iimjobs.com/chief-of-staff-jobs",  # iimjobs search pages
    "iimjobs.com/product-manager-jobs", "iimjobs.com/product-designer-jobs",
    # Profile / aggregate pages that slipped through
    "theorg.com",
    "ambitionbox.com",
    "adzuna.in",
    "adzuna.com",
    "totaljobs.com",
    "builtinnyc.com",
    "builtin.com",
    "reed.co.uk",
    "careerjet",
    # Not job postings — articles, blog posts, forum discussions
    "forbes.com",
    "medium.com",
    "reddit.com",
    "linkedin.com/pulse",
    "linkedin.com/posts",
    "crane.vc",
    "techcrunch.com",
    "yourstory.com",    # news articles, not job listings
]

# Confirmed individual-job URL prefixes — always allow
INDIVIDUAL_JOB_URL_PATTERNS = [
    "greenhouse.io/", "lever.co/", "wellfound.com/company/",
    "wellfound.com/jobs/",           # wellfound.com/jobs/{id}-{slug}
    "iimjobs.com/j/", "instahyre.com/jobs/",
]

# URLs that look like job results but are actually profile pages / not jobs
NON_JOB_URL_PATTERNS = [
    "linkedin.com/in/",      # person profile — not a job post
    "linkedin.com/pub/",
    "theorg.com/org/",       # company org chart
    "linkedin.com/company/", # company page
]


def _is_aggregate_page(url: str) -> bool:
    u = url.lower()
    # Always block person profiles and non-job pages
    if any(p in u for p in NON_JOB_URL_PATTERNS):
        return True
    if any(p in u for p in INDIVIDUAL_JOB_URL_PATTERNS):
        return False
    return any(p in u for p in AGGREGATE_URL_PATTERNS)

# Job board domains to prefer in Parallel searches
JOB_BOARD_DOMAINS = [
    "greenhouse.io", "lever.co", "wellfound.com", "iimjobs.com",
    "instahyre.com", "linkedin.com", "indeed.com", "naukri.com",
]


def _extract_title_from_search_result(result_title: str, snippet: str, domain: str) -> str:
    """
    Best-effort title extraction from a search result title string.
    Strategy: scan all parts, pick the one that looks like a job title, not a company name.
    """
    role_kws_by_domain: dict[str, list[str]] = {
        "pm": ["product manager", "head of product", "vp product", "vp of product",
               "chief product", "cpo", "director of product", "director, product",
               "product mgr"],
        "strategy": ["chief of staff", "founder's office", "founders office", "founder office",
                     "entrepreneur in residence", "eir", "special projects", "strategic initiatives"],
    }
    role_kws = role_kws_by_domain.get(domain, [])

    for sep_pattern in [r"\s+at\s+", r"\s*[-–|·]\s*"]:
        parts = re.split(sep_pattern, result_title, flags=re.IGNORECASE)
        for part in parts:
            part = part.strip()
            if 4 < len(part) < 90 and any(k in part.lower() for k in role_kws):
                return part

    for sep in [" at ", " - ", " | ", " · "]:
        if sep in result_title:
            part = result_title.split(sep)[0].strip()
            if 4 < len(part) < 80 and any(k in part.lower() for k in role_kws):
                return part

    defaults = {"pm": "Product Manager", "design": "Product Designer",
                "data": "Data Analyst", "strategy": "Chief of Staff"}
    return defaults.get(domain, "Open Role")


def _extract_company_from_search_result(result_title: str, url: str) -> str:
    """Best-effort company extraction from result title or URL."""
    import urllib.parse
    try:
        host = urllib.parse.urlparse(url).hostname or ""
        path = urllib.parse.urlparse(url).path or ""

        # lever: jobs.lever.co/{company}/...
        if "lever.co" in host:
            parts = [p for p in path.split("/") if p]
            if parts:
                return parts[0].replace("-", " ").title()

        # greenhouse: boards.greenhouse.io/v1/boards/{company}/...
        if "greenhouse.io" in host:
            parts = [p for p in path.split("/") if p and p not in ("v1", "boards", "jobs")]
            if parts:
                return parts[0].replace("-", " ").title()

        # wellfound: wellfound.com/company/{slug}/jobs/...
        if "wellfound.com" in host:
            # wellfound.com/jobs/{numeric-id}-{slug} — company is NOT in URL, use title below
            if path.startswith("/jobs/"):
                pass  # fall through to title pattern
            else:
                parts = [p for p in path.split("/") if p and p not in ("company", "jobs", "l", "role")]
                if parts:
                    return parts[0].replace("-", " ").title()

        # iimjobs: iimjobs.com/j/{company-slug-role-id}
        # e.g. /j/hevo-chief-of-staff-founders-office-1687459
        # Company is the prefix BEFORE the first role keyword in the slug
        if "iimjobs.com" in host and path.startswith("/j/"):
            slug = path.split("/j/")[-1]
            # First, identify the company part before any role keywords
            role_kws = [
                "chief-of-staff", "product-manager", "product-designer",
                "ux-designer", "data-analyst", "growth-analyst",
                "head-of-product", "vp-product", "founders-office",
                "entrepreneur-in-residence", "founding-member",
                "senior-product", "associate-product", "founding-team",
                "head-sourcing", "head-of", "vp-of", "director-of",
                "lead-product", "associate-director", "product-management"
            ]
            cut = len(slug)
            for kw in role_kws:
                idx = slug.find(kw)
                if idx >= 0:
                    cut = min(cut, idx)
            company_slug = slug[:cut].strip("-")
            # Strip iimjobs qualification suffixes: "-iim-isb-mdi-fms", year ranges
            company_slug = re.sub(r'-(iim|isb|mdi|fms|nit|bits|xlri|iit).*$', '', company_slug)
            # Strip trailing numeric ID if it's a standalone ID after the company name
            company_slug = re.sub(r'-\d+$', '', company_slug) if re.search(r'-\d+$', company_slug) else company_slug
            company_slug = company_slug.strip("-")
            # Reject if too short (< 2 chars), starts with role keyword, or all-caps abbreviation
            if company_slug and len(company_slug) >= 2:
                cleaned_company = company_slug.replace("-", " ").title()
                # Only reject if it's *just* a role keyword or in JUNK_COMPANY_NAMES
                if cleaned_company.lower() not in [kw.replace('-', ' ') for kw in role_kws] and cleaned_company.lower() not in [jc.lower() for jc in JUNK_COMPANY_NAMES]:
                    return cleaned_company
            return "Unknown"

        # instahyre: instahyre.com/jobs/{company}/{role-slug}
        if "instahyre.com" in host:
            parts = [p for p in path.split("/") if p and p not in ("jobs",)]
            if parts:
                return parts[0].replace("-", " ").title()

    except Exception:
        pass

    # Try "Role at Company | Source" or "Role at Company • Location" patterns
    m = re.search(r"\bat\s+([A-Za-z0-9][A-Za-z0-9 &\-\.]+?)(?:\s*[\|\-\·•]|$)", result_title)
    if m:
        return m.group(1).strip()
    # "Company | Role" format
    parts = re.split(r"\s*[\|\-]\s*", result_title)
    if len(parts) >= 2:
        candidate = parts[-1].strip()
        if 2 < len(candidate) < 40 and not any(k in candidate.lower() for k in ["jobs", "hiring", "careers", "vacancy"]):
            return candidate
    return "Unknown"


def run_parallel():
    import subprocess
    import json as _json
    from datetime import timedelta

    source_id = get_source_id("parallel_search")
    saved = 0
    # v0.1: PM-only — fixed 14-day freshness window
    after_date = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%d")

    for query, domains in PARALLEL_SEARCHES:
        try:
            cmd = [
                "parallel-cli", "search",
                "--json",
                "--max-results", "15",
                "--after-date", after_date,
                "-q", query,
            ]
            if domains:
                cmd += ["--include-domains", ",".join(domains)]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
            if result.returncode != 0:
                print(f"  [parallel] search error for '{query}': {result.stderr[:120]}")
                continue

            data = _json.loads(result.stdout)
            items = data.get("results", [])

            for item in items:
                url = (item.get("url") or "").strip()
                snippet = (item.get("excerpt") or item.get("snippet") or "")[:1000]
                result_title = (item.get("title") or "").strip()

                # Skip aggregate search listing pages
                if _is_aggregate_page(url):
                    continue

                combined = f"{result_title} {snippet}"
                domain = classify(combined)
                if not domain:
                    continue
                if is_us_or_non_india_listing(combined, ""):
                    continue

                # v0.1: PM only. Even web-search results must come from known
                # individual-job URLs to avoid articles, blog posts, profile pages.
                # Relaxing known_job_board check for parallel_search to allow more diverse sources.
                # The classification and enrichment steps will handle quality.
                # known_job_board = any(p in url for p in INDIVIDUAL_JOB_URL_PATTERNS + [
                #     "iimjobs.com/j/", "instahyre.com/jobs/",
                #     "cutshort.io/job/", "naukri.com/job-listings",
                #     "in.linkedin.com/jobs/view",
                # ])
                # if not known_job_board:
                #     continue

                title = _extract_title_from_search_result(result_title, snippet, domain)
                company = _extract_company_from_search_result(result_title, url)
                location = normalize_location(snippet + " " + query)[0]

                if save_job_signal(title, company, location, domain, url or None, source_id, snippet):
                    saved += 1
                    print(f"  [parallel] {title} @ {company}")

        except Exception as e:
            print(f"  [parallel] Error for '{query}': {e}")

    conn.commit()
    print(f"  [parallel] {saved} new jobs saved")
    return saved


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"Pipeline starting — source: {SOURCE_ARG} — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    total = 0

    runners = {
        "parallel": run_parallel,   # first — broadest net, multi-engine
        "greenhouse": run_greenhouse,
        "lever": run_lever,
        "jobspy": run_jobspy,
        "hn": run_hn,
        "naukri": run_naukri,
    }

    if SOURCE_ARG == "all":
        for name, fn in runners.items():
            print(f"\n── {name} ──")
            total += fn()
    elif SOURCE_ARG in runners:
        total += runners[SOURCE_ARG]()
    else:
        print(f"Unknown source: {SOURCE_ARG}. Use: all | parallel | greenhouse | lever | jobspy | hn | naukri")

    cur.close()
    conn.close()
    print(f"\nPipeline done. {total} new jobs added.")

if __name__ == "__main__":
    main()
