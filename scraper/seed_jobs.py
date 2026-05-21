"""
Seed the DB with real jobs found via web search.
Run: python seed_jobs.py
"""
import os, uuid, psycopg2
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

# Ensure web_search source exists
cur.execute("SELECT id FROM sources WHERE name = 'web_search'")
row = cur.fetchone()
if row:
    source_id = row[0]
else:
    source_id = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO sources (id, name, is_active) VALUES (%s, %s, true)",
        (source_id, "web_search"),
    )

JOBS = [
    # PM
    dict(title="Senior Product Manager", company="Locus", location="Bangalore",
         domain="pm", job_url="https://in.linkedin.com/jobs/view/senior-product-manager-at-locus-4397666104",
         raw="Locus is hiring a Senior Product Manager in Bengaluru. 6-10 yrs experience in SaaS/logistics platforms. Will own product vision for Locus Suite, mentor junior PMs, work with global enterprise clients like Unilever and Nestlé."),
    dict(title="Product Manager – Growth", company="PhonePe", location="Bangalore",
         domain="pm", job_url="https://job-boards.greenhouse.io/phonepe/jobs/7690461003",
         raw="PhonePe hiring Product Manager for growth-focused problems. Own and drive vision and roadmap for products driving customer growth for PhonePe and merchant partners."),
    dict(title="Senior Product Manager", company="PhonePe", location="Bangalore",
         domain="pm", job_url="https://www.instahyre.com/job-116495-senior-product-manager-at-phonepe-bangalore/",
         raw="PhonePe hiring Senior Product Manager in Bangalore. 6-11 years experience in General Management, Strategy, Product Management."),
    dict(title="Sr. Product Manager – Domains", company="HighLevel", location="Remote / India",
         domain="pm", job_url="https://in.linkedin.com/jobs/view/sr-product-manager-domains-at-highlevel-4401084906",
         raw="HighLevel hiring Sr. Product Manager for Domains team in India. HighLevel is a SaaS CRM platform for agencies."),
    dict(title="Sr. Product Manager – Workflows", company="HighLevel", location="Remote / India",
         domain="pm", job_url="https://in.linkedin.com/jobs/view/sr-product-manager-workflow-extended-at-highlevel-4398243609",
         raw="HighLevel hiring Sr. Product Manager for Workflow Extended in India."),
    dict(title="Product Manager", company="WorkIndia", location="Bangalore",
         domain="pm", job_url="https://in.linkedin.com/jobs/view/product-manager-at-workindia-3357242494",
         raw="WorkIndia hiring Product Manager in Bengaluru. Prefers candidates who have run their own startup or were co-founders."),
    dict(title="Product Manager", company="Razorpay", location="Bangalore",
         domain="pm", job_url="https://www.instahyre.com/job-148007-product-manager-at-razorpay-bangalore/",
         raw="Razorpay hiring Product Manager in Bangalore. 1-4 years of experience in Product Management."),
    dict(title="Senior Product Manager", company="Razorpay", location="Bangalore",
         domain="pm", job_url="https://www.instahyre.com/job-202693-senior-product-manager-at-razorpay-bangalore/",
         raw="Razorpay hiring Senior Product Manager in Bangalore. 4-6 years of experience."),
    dict(title="Product Manager", company="Meesho", location="Bangalore",
         domain="pm", job_url="https://www.uplers.com/company/meesho-4526",
         raw="Meesho actively hiring Product Managers as of April 2026. 29 open positions across the company."),

    # Design
    dict(title="Product Designer I", company="Razorpay", location="Bangalore",
         domain="design", job_url="https://builtin.com/job/product-designer-i/6345265",
         raw="Razorpay hiring Product Designer I in Bangalore. Full-time onsite role at India's leading payments company."),
    dict(title="UI/UX Designer", company="SuprSend", location="Bangalore",
         domain="design", job_url="https://wellfound.com/role/l/ui-ux-designer/bangalore",
         raw="SuprSend hiring UI/UX Designer to join early team. SuprSend is a developer-first notification infrastructure platform. Shape how developers experience the product."),
    dict(title="Product Designer – UI/UX", company="Societe Generale", location="Bangalore",
         domain="design", job_url="https://careers.societegenerale.com/en/job-offers/product-designer-ui-ux-2600023B-en",
         raw="Societe Generale hiring Product Designer UI/UX in Bangalore, India. Full-time role at global financial services group."),

    # Data
    dict(title="Business / Data Analyst", company="Fintech Startup (via Qrata)", location="Bangalore",
         domain="data", job_url="https://cutshort.io/job/Bussiness-Data-Analyst-Bengaluru-Bangalore-Qrata-AIGcbKf2",
         raw="Mission-oriented high-growth fintech startup building payment and lending products hiring Business/Data Analyst in Bangalore. Requires Excel, SQL, Python familiarity, BI tools (Quicksight/Tableau). 1-3 years experience in analytics or data operations in fintech."),
    dict(title="Data Analyst", company="Locus", location="Bangalore",
         domain="data", job_url="https://locus.freshteam.com/jobs",
         raw="Locus hiring Data Analyst in Bangalore. Work on logistics intelligence and delivery optimization data."),
]

now = datetime.now(timezone.utc)
saved = 0

for j in JOBS:
    # Check if job already exists
    cur.execute("SELECT id FROM jobs WHERE company = %s AND title = %s", (j["company"], j["title"]))
    row = cur.fetchone()
    if row:
        job_id = row[0]
        print(f"  Exists: {j['title']} at {j['company']}")
    else:
        job_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO jobs (id, title, company, location, domain, job_url) VALUES (%s,%s,%s,%s,%s,%s)",
            (job_id, j["title"], j["company"], j["location"], j["domain"], j["job_url"]),
        )

    # Check if signal already exists for this job+source
    cur.execute("SELECT id FROM signals WHERE job_id = %s AND source_id = %s", (job_id, source_id))
    if cur.fetchone():
        continue

    signal_id = str(uuid.uuid4())
    cur.execute(
        """INSERT INTO signals
           (id, job_id, source_id, signal_url, raw_text, urgency_signals, post_date, validated, briefed)
           VALUES (%s,%s,%s,%s,%s,%s,%s,true,false)""",
        (signal_id, job_id, source_id, j["job_url"], j["raw"], [], now),
    )
    saved += 1
    print(f"  Saved: {j['title']} at {j['company']} ({j['location']}) [{j['domain']}]")

conn.commit()
cur.close()
conn.close()
print(f"\nDone. {saved} new signals seeded.")
