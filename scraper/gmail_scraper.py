"""
Gmail-based LinkedIn hiring signal scraper.

Reads LinkedIn notification emails from the last 48 hours,
extracts hiring signals, and writes them to Neon DB.

Auth: uses GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN
      (set as env vars or GitHub Actions secrets)

Run: python gmail_scraper.py
"""

import os
import re
import uuid
import base64
import json
import psycopg2
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from dotenv import load_dotenv

import requests

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]
GMAIL_CLIENT_ID = os.environ["GMAIL_CLIENT_ID"]
GMAIL_CLIENT_SECRET = os.environ["GMAIL_CLIENT_SECRET"]
GMAIL_REFRESH_TOKEN = os.environ["GMAIL_REFRESH_TOKEN"]

# LinkedIn email senders
LINKEDIN_SENDERS = [
    "jobs-noreply@linkedin.com",
    "notifications@linkedin.com",
    "jobalerts-noreply@linkedin.com",
    "hit-reply@linkedin.com",
]

URGENCY_KEYWORDS = [
    "urgent", "immediately", "immediate joiner", "asap", "as soon as possible",
    "critical hire", "priority hire", "been looking", "months open",
    "backfill", "closing soon", "interviewing now", "actively hiring",
]

HIRING_KEYWORDS = [
    "hiring", "we're looking", "we are looking", "looking for a",
    "join our team", "open role", "open position", "job opening",
    "now hiring", "we need a", "seeking a", "referral",
    "product manager", "product designer", "data analyst",
    "pm role", "design role", "head of product",
]

PM_KEYWORDS = ["product manager", "pm ", " pm,", "head of product", "vp product", "cpo", "group pm", "senior pm"]
DESIGN_KEYWORDS = ["product designer", "ux designer", "ui designer", "design lead", "head of design"]
DATA_KEYWORDS = ["data analyst", "data scientist", "analytics", "product data", "growth analyst"]


# ── Gmail auth ────────────────────────────────────────────────────────────────

def get_access_token() -> str:
    resp = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": GMAIL_CLIENT_ID,
        "client_secret": GMAIL_CLIENT_SECRET,
        "refresh_token": GMAIL_REFRESH_TOKEN,
        "grant_type": "refresh_token",
    })
    resp.raise_for_status()
    return resp.json()["access_token"]


def gmail_get(access_token: str, path: str, params: dict = None) -> dict:
    resp = requests.get(
        f"https://gmail.googleapis.com/gmail/v1/users/me/{path}",
        headers={"Authorization": f"Bearer {access_token}"},
        params=params or {},
    )
    resp.raise_for_status()
    return resp.json()


# ── Email parsing ─────────────────────────────────────────────────────────────

class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.chunks = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False
        if tag in ("td", "div", "p", "br", "li", "tr"):
            self.chunks.append(" ")

    def handle_data(self, data):
        if not self._skip:
            self.chunks.append(data)

    def get_text(self) -> str:
        return re.sub(r"\s+", " ", "".join(self.chunks)).strip()


def decode_body(part: dict) -> str:
    data = part.get("body", {}).get("data", "")
    if not data:
        return ""
    return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")


def get_email_text(payload: dict) -> str:
    mime = payload.get("mimeType", "")
    if mime == "text/plain":
        return decode_body(payload)
    if mime == "text/html":
        parser = TextExtractor()
        parser.feed(decode_body(payload))
        return parser.get_text()
    # multipart: prefer plain, fall back to html
    parts = payload.get("parts", [])
    for part in parts:
        if part.get("mimeType") == "text/plain":
            text = decode_body(part)
            if text:
                return text
    for part in parts:
        text = get_email_text(part)
        if text:
            return text
    return ""


def extract_links(payload: dict) -> list[str]:
    """Pull all hrefs from HTML parts."""
    links = []
    mime = payload.get("mimeType", "")
    if mime == "text/html":
        html = decode_body(payload)
        links += re.findall(r'href=["\']([^"\']+linkedin\.com[^"\']*)["\']', html)
    for part in payload.get("parts", []):
        links += extract_links(part)
    return links


# ── Signal extraction ─────────────────────────────────────────────────────────

def classify_domain(text: str) -> str:
    t = text.lower()
    if any(k in t for k in PM_KEYWORDS):
        return "pm"
    if any(k in t for k in DESIGN_KEYWORDS):
        return "design"
    if any(k in t for k in DATA_KEYWORDS):
        return "data"
    return "other"


def extract_urgency(text: str) -> list[str]:
    t = text.lower()
    return [k for k in URGENCY_KEYWORDS if k in t]


def is_hiring_email(subject: str, body: str) -> bool:
    combined = (subject + " " + body).lower()
    return any(k in combined for k in HIRING_KEYWORDS)


def extract_title(text: str) -> str:
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
    domain = classify_domain(text)
    return {"pm": "Product Manager", "design": "Product Designer", "data": "Data Analyst", "other": "Open Role"}[domain]


def extract_company(text: str) -> str:
    match = re.search(r'\bat\s+([A-Z][A-Za-z0-9&\s]+?)[\s,\.\!]', text)
    if match:
        candidate = match.group(1).strip()
        if 2 < len(candidate) < 50:
            return candidate
    return "Unknown"


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


def extract_poster_from_subject(subject: str) -> str | None:
    # "Rahul Sharma is hiring a Product Manager at Acme"
    match = re.match(r'^(.+?)\s+(?:is hiring|is looking|posted a job)', subject, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def extract_job_url(links: list[str]) -> str | None:
    for link in links:
        if "/jobs/view/" in link or "/job/" in link:
            return link.split("?")[0]
    return None


def extract_post_url(links: list[str]) -> str | None:
    for link in links:
        if "/feed/update/" in link or "/posts/" in link:
            return link.split("?")[0]
    return None


# ── DB helpers ────────────────────────────────────────────────────────────────

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


def get_or_create_job(cur, title: str, company: str, location: str, domain: str, job_url: str | None) -> str:
    cur.execute("SELECT id FROM jobs WHERE company = %s AND title = %s", (company, title))
    row = cur.fetchone()
    if row:
        return row[0]
    job_id = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO jobs (id, title, company, location, domain, job_url) VALUES (%s, %s, %s, %s, %s, %s)",
        (job_id, title, company, location, domain, job_url),
    )
    return job_id


def signal_exists(cur, signal_url: str | None, raw_text: str) -> bool:
    if signal_url:
        cur.execute("SELECT 1 FROM signals WHERE signal_url = %s", (signal_url,))
        if cur.fetchone():
            return True
    # Deduplicate by text fingerprint (first 200 chars)
    fingerprint = raw_text[:200]
    cur.execute("SELECT 1 FROM signals WHERE LEFT(raw_text, 200) = %s", (fingerprint,))
    return cur.fetchone() is not None


# ── Main ──────────────────────────────────────────────────────────────────────

def fetch_linkedin_emails(access_token: str) -> list[dict]:
    """Fetch LinkedIn emails from the last 48 hours."""
    since = int((datetime.now(timezone.utc) - timedelta(hours=48)).timestamp())
    sender_query = " OR ".join(f"from:{s}" for s in LINKEDIN_SENDERS)
    query = f"({sender_query}) after:{since}"

    result = gmail_get(access_token, "messages", {"q": query, "maxResults": 50})
    messages = result.get("messages", [])
    print(f"Found {len(messages)} LinkedIn emails in last 48h")

    emails = []
    for msg in messages:
        try:
            full = gmail_get(access_token, f"messages/{msg['id']}", {"format": "full"})
            headers = {h["name"].lower(): h["value"] for h in full["payload"]["headers"]}
            emails.append({
                "id": msg["id"],
                "subject": headers.get("subject", ""),
                "from": headers.get("from", ""),
                "payload": full["payload"],
            })
        except Exception as e:
            print(f"  Error fetching message {msg['id']}: {e}")
    return emails


def process_emails(emails: list[dict]) -> list[dict]:
    """Parse emails into hiring signal dicts."""
    signals = []
    for email in emails:
        subject = email["subject"]
        body = get_email_text(email["payload"])
        links = extract_links(email["payload"])

        if not is_hiring_email(subject, body):
            continue

        text = f"{subject} {body}"
        title = extract_title(text)
        company = extract_company(text)
        location = extract_location(text)
        domain = classify_domain(text)
        urgency = extract_urgency(text)
        poster = extract_poster_from_subject(subject)
        job_url = extract_job_url(links)
        post_url = extract_post_url(links) or job_url
        profile_url = next((l for l in links if "/in/" in l), None)
        if profile_url:
            profile_url = profile_url.split("?")[0]

        signals.append({
            "raw_text": text[:2000],
            "title": title,
            "company": company,
            "location": location,
            "domain": domain,
            "urgency": urgency,
            "poster_name": poster or "LinkedIn",
            "profile_url": profile_url,
            "signal_url": post_url,
            "job_url": job_url,
        })
        print(f"  Parsed: {title} at {company} — from: {subject[:60]}")

    return signals


def run():
    print("Starting Gmail scraper...")
    access_token = get_access_token()

    emails = fetch_linkedin_emails(access_token)
    if not emails:
        print("No LinkedIn emails found. Done.")
        return

    signals = process_emails(emails)
    print(f"\n{len(signals)} hiring signals parsed from {len(emails)} emails")

    if not signals:
        print("No hiring signals found in emails. Done.")
        return

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    source_id = get_or_create_source(cur, "linkedin_email")
    conn.commit()

    saved = 0
    for sig in signals:
        try:
            if signal_exists(cur, sig["signal_url"], sig["raw_text"]):
                print(f"  Skipping duplicate: {sig['title']} at {sig['company']}")
                continue

            person_id = get_or_create_person(cur, sig["poster_name"], sig["profile_url"])
            job_id = get_or_create_job(
                cur, sig["title"], sig["company"], sig["location"], sig["domain"], sig["job_url"]
            )
            cur.execute(
                """
                INSERT INTO signals
                  (job_id, source_id, person_id, signal_url, profile_url,
                   raw_text, urgency_signals, post_date, validated)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, true)
                """,
                (job_id, source_id, person_id, sig["signal_url"], sig["profile_url"],
                 sig["raw_text"], sig["urgency"], datetime.now(timezone.utc)),
            )
            conn.commit()
            saved += 1
            print(f"  Saved: {sig['title']} at {sig['company']} ({sig['location']})")
        except Exception as e:
            conn.rollback()
            print(f"  Error saving signal: {e}")

    cur.close()
    conn.close()
    print(f"\nDone. {saved} new signals saved to DB.")


if __name__ == "__main__":
    run()
