"""
Job enricher — fills employment_type and description_summary for unenriched jobs.
Uses web search results to determine work mode (remote/hybrid/onsite) and write a summary.
Run: python enrich_jobs.py
Scheduled: daily after scraper runs.
"""

import os
import json
import psycopg2
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

# Static enrichment map — built from web search, updated as needed.
# Key: (company_lower, title_lower_fragment)
# Value: (employment_type, description_summary)
ENRICHMENT = {
    ("locus", "senior product manager"): (
        "onsite",
        "Own product vision for Locus Suite (logistics SaaS). 6-10 yrs experience. Work with enterprise clients like Unilever and Nestlé across 30+ countries.",
    ),
    ("locus", "data analyst"): (
        "onsite",
        "Analytics for logistics intelligence and delivery optimization data at Locus (B2B SaaS, 30+ countries).",
    ),
    ("phonepe", "product manager – growth"): (
        "hybrid",
        "Own roadmap for customer growth products at PhonePe. Work with merchant and consumer teams on India's leading payments super-app.",
    ),
    ("phonepe", "senior product manager"): (
        "hybrid",
        "Senior PM at PhonePe, 6-11 yrs experience. Drive strategy across payments, insurance, or wealth products.",
    ),
    ("highlevel", "sr. product manager – domains"): (
        "remote",
        "Fully remote. HighLevel is a remote-first SaaS CRM for agencies (1500+ team across 15 countries). Own the Domains product.",
    ),
    ("highlevel", "sr. product manager – workflows"): (
        "remote",
        "Fully remote. HighLevel Workflow Extended PM. Build automation capabilities for agency clients worldwide.",
    ),
    ("workindia", "product manager"): (
        "onsite",
        "WorkIndia is India's job platform for blue-collar workers. Prefers ex-founders or early-stage startup experience.",
    ),
    ("razorpay", "product manager"): (
        "hybrid",
        "1-4 yrs PM at Razorpay (India's #1 payments stack). Hybrid, Bangalore. Work on payments, banking, or developer tools.",
    ),
    ("razorpay", "senior product manager"): (
        "hybrid",
        "4-6 yrs PM at Razorpay. Hybrid Bangalore. Neobanking or payments infrastructure focus.",
    ),
    ("razorpay", "product designer i"): (
        "hybrid",
        "Product Designer I at Razorpay. Hybrid Bangalore. Design payments and fintech experiences used by 8M+ businesses.",
    ),
    ("meesho", "product manager"): (
        "hybrid",
        "PM II at Meesho (social commerce, 150M+ users). Hybrid Bangalore. Identify market problems, drive growth with cross-functional teams.",
    ),
    ("suprsend", "ui/ux designer"): (
        "remote",
        "Early-team UI/UX Designer at SuprSend (developer-first notification infra). Shape how developers experience a fast-growing dev tool.",
    ),
    ("societe generale", "product designer"): (
        "hybrid",
        "Product Designer for internal fintech tools at Societe Generale Bangalore. Enterprise financial services.",
    ),
    ("fintech startup (via qrata)", "business / data analyst"): (
        "onsite",
        "1-3 yrs analyst at high-growth edtech-fintech. Build payment/lending analytics. SQL, Python, Quicksight/Tableau required.",
    ),
}


def match_key(company: str, title: str):
    c = company.lower().strip()
    t = title.lower().strip()
    for (kc, kt), val in ENRICHMENT.items():
        if kc in c and kt in t:
            return val
    return None


cur.execute(
    "SELECT id, title, company FROM jobs WHERE employment_type IS NULL OR description_summary IS NULL"
)
jobs = cur.fetchall()
updated = 0

for job_id, title, company in jobs:
    result = match_key(company, title)
    if not result:
        print(f"  No enrichment for: {title} @ {company}")
        continue
    emp_type, summary = result
    cur.execute(
        "UPDATE jobs SET employment_type = %s, description_summary = %s, updated_at = %s WHERE id = %s",
        (emp_type, summary, datetime.now(timezone.utc), job_id),
    )
    updated += 1
    print(f"  Enriched [{emp_type}]: {title} @ {company}")

conn.commit()
cur.close()
conn.close()
print(f"\nDone. {updated}/{len(jobs)} jobs enriched.")
