"""
Job enrichment pipeline.
For every dirty job record, fetches the actual job page via parallel-cli
and extracts: real title, real company, city, work mode, description summary.

Run:  python enrich.py
Runs automatically every 15 minutes via scheduled task.
"""

import os
import re
import json
import subprocess
import requests
import psycopg2
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

# Jobs per run. Daily cron uses the default; a manual run can clear a large
# backlog faster by setting ENRICH_BATCH (see enrich.yml workflow input).
BATCH = int(os.environ.get("ENRICH_BATCH", "30"))


# ─── Dirty detection ──────────────────────────────────────────────────────────

DIRTY_COMPANY_PATTERNS = re.compile(
    r'\d{3,}'                          # 3+ consecutive digits (slug IDs)
    r'|039'                            # HTML entity artifact
    r'|product manager|product designer'
    r'|data analyst|growth analyst', # Removed 'founding team' and 'founding member' as they are often part of company names, not just roles.
    re.IGNORECASE,
)


def is_dirty(row) -> bool:
    """
    Always-true for tier-1 (pending+unknown) candidates: those need India verification
    regardless of how clean the metadata looks. The DB-side WHERE clause already
    scoped the query — anything we get back is worth enriching.
    """
    job_id, company, norm_company, norm_function, employment_type, description_summary, confidence, job_url = row
    if not job_url:
        return False  # can't enrich without a URL
    return True


def fetch_dirty_jobs(limit: int, force: bool = False):
    """
    Per SPEC.md enrichment priority:
      1. pending+unknown PM/strategy jobs first (highest leverage)
      2. approved+confirmed PM/strategy jobs with stale enrichment (> 7 days)
      Within each tier: PM jobs before strategy jobs (PM is primary domain).
      Within each domain: senior roles first.

    A per-job cooldown (6h pending / 7d approved) normally stops a batch
    burning on recently-tried jobs. force=True drops the cooldown so the
    whole board is eligible — used to re-clean everything after an
    extraction change.
    """
    cd_pending  = "" if force else \
        "AND (last_enriched_at IS NULL OR last_enriched_at < NOW() - INTERVAL '6 hours')"
    cd_approved = "" if force else \
        "AND (last_enriched_at IS NULL OR last_enriched_at < NOW() - INTERVAL '7 days')"
    cur.execute(r"""
        SELECT id, company, norm_company, norm_function,
               employment_type, description_summary,
               normalization_confidence, job_url
        FROM jobs
        WHERE COALESCE(norm_function, domain) IN ('pm', 'strategy')
          AND job_url IS NOT NULL
          AND job_url NOT LIKE '%%linkedin.com/jobs%%'
          AND job_url NOT LIKE '%%indeed.com/viewjob%%'
          AND job_url NOT LIKE '%%jooble%%'
          AND (
            (review_status = 'pending' AND india_hiring = 'unknown' """ + cd_pending + r""")
            OR
            (review_status = 'approved' AND india_hiring = 'confirmed' """ + cd_approved + r""")
          )
        ORDER BY
          -- tier 1: pending+unknown first
          CASE
            WHEN review_status = 'pending' AND india_hiring = 'unknown' THEN 1
            ELSE 2
          END ASC,
          -- tier 2: PM before strategy (primary domain)
          CASE COALESCE(norm_function, domain)
            WHEN 'pm' THEN 1
            ELSE 2
          END ASC,
          -- tier 3: seniority (covers both PM and CoS/FO titles)
          CASE
            WHEN LOWER(COALESCE(norm_title, title)) SIMILAR TO
              '%%(cpo|vp |vice president|head of|chief product|entrepreneur in residence| eir |chief of staff)%%' THEN 1
            WHEN LOWER(COALESCE(norm_title, title)) SIMILAR TO '%%(director)%%'          THEN 2
            WHEN LOWER(COALESCE(norm_title, title)) SIMILAR TO '%%(staff |principal |group )%%' THEN 3
            WHEN LOWER(COALESCE(norm_title, title)) SIMILAR TO '%%(senior |lead |sr\. | sr )%%' THEN 4
            WHEN LOWER(COALESCE(norm_title, title)) SIMILAR TO '%%(associate |junior |jr\.)%%'  THEN 6
            ELSE 5
          END ASC,
          first_seen_at DESC
        LIMIT %s
    """, (limit * 3,))
    rows = cur.fetchall()
    return [r for r in rows if is_dirty(r)][:limit]


# ─── Parallel fetch + extract ─────────────────────────────────────────────────

def fetch_page(url: str) -> str | None:
    """
    Returns clean markdown from a job page URL.
    parallel-cli fetch stdout looks like:
        Extracting content from 1 URL(s)...
        Extracted 1 page(s)
        {Page Title}
        {url}
        Excerpts:
          {indented markdown content}
    We grab the page title line + dedented content as the usable text.
    """
    try:
        result = subprocess.run(
            ["parallel-cli", "fetch", "--full-content", url],
            capture_output=True, text=True, timeout=45,
        )
        if result.returncode != 0 or len(result.stdout) < 200:
            return None

        raw = result.stdout

        # Prefer the "Full content:" section — it has the complete JD text.
        # Fall back to "Excerpts:" if full content wasn't returned.
        full_marker = "Full content:\n"
        excerpt_marker = "Excerpts:\n"

        if full_marker in raw:
            body = raw[raw.index(full_marker) + len(full_marker):]
        elif excerpt_marker in raw:
            # dedent the 2-space indent on excerpt lines
            excerpt_raw = raw[raw.index(excerpt_marker) + len(excerpt_marker):]
            body = "\n".join(
                line[2:] if line.startswith("  ") else line
                for line in excerpt_raw.splitlines()
            )
        else:
            body = raw

        # Extract page title from the header block (line before the URL echo)
        page_title = None
        for line in raw.splitlines():
            if line.startswith("Extracting") or line.startswith("Extracted") or line.startswith("http"):
                continue
            if line.strip() == "Excerpts:" or line.strip() == "Full content:":
                break
            if line.strip():
                page_title = line.strip()

        # Prepend page title so company extraction can use it
        if page_title:
            body = page_title + "\n" + body

        return body[:12000] if len(body) > 200 else None
    except Exception:
        return None


def _clean(s: str) -> str:
    """Strip markdown formatting and normalise whitespace."""
    s = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', s)   # [text](url) → text
    s = re.sub(r'[*_`#]+', '', s)
    return re.sub(r'\s+', ' ', s).strip()


def extract_title(text: str, existing_title: str) -> str | None:
    """
    Markdown structures seen in the wild:
      Greenhouse : "# Product Manager\nBengaluru, India\nApply"
      Lever      : "## Staff Product Manager - App Marketplace\nIndia\nProduct / Employee"
      iimjobs    : "# Chief of Staff\nMumbai"
    Note: DIRTY_COMPANY_PATTERNS must NOT be applied here — titles legitimately
    contain "product manager", "chief of staff" etc.
    """
    for pat in [r'^##\s+(.+)$', r'^#\s+(.+)$']:
        for m in re.finditer(pat, text, re.MULTILINE):
            candidate = _clean(m.group(1))
            # Skip nav/footer headings — just length + no digits-only content
            if 4 < len(candidate) < 90 and not re.search(r'^\d+$', candidate):
                return candidate
    return None


def extract_company(text: str, existing_company: str) -> str | None:
    """
    Patterns in order of reliability:
      1. Lever page title line:  "HighLevel - Staff Product Manager - App Marketplace"
         → company is everything before the first " - "
      2. Greenhouse page title:  "Job Application for X at **Groww**"
      3. About section:          "**About Groww:**" or "**About us**  \nGroww is..."
    """
    lines = text.splitlines()

    # Pattern 1 — Lever: first line is "{Company} - {Title}"
    # Guard: Greenhouse uses "Job Application for X at Company" — this is a valid company extraction pattern
    # Also, ensure the company part is not a common job title itself.
    if lines and not lines[0].lower().startswith("job application for"): 
        m = re.match(r'^([^\-]+?)\s*\-\s*(.+)$', lines[0])
        if m:
            candidate = _clean(m.group(1))
            if 2 < len(candidate) < 40 and not DIRTY_COMPANY_PATTERNS.search(candidate.lower()):
                return candidate
        first = _clean(lines[0])
        if ' - ' in first and not first.lower().startswith("job application"):
            candidate = first.split(' - ')[0].strip()
            if 2 < len(candidate) < 50 and not DIRTY_COMPANY_PATTERNS.search(candidate):
                return candidate

    # Pattern 2 — Greenhouse: "Job Application for X at Company"
    m = re.search(r'Job Application for .+? at ([A-Z][A-Za-z0-9][A-Za-z0-9 &\.\-]{1,40})',
                  text, re.IGNORECASE)
    if m:
        candidate = m.group(1).strip()
        if not DIRTY_COMPANY_PATTERNS.search(candidate):
            return candidate

    # Pattern 3 — "**About CompanyName:**" or "**About CompanyName**"
    m = re.search(r'\*\*About\s+([A-Z][A-Za-z0-9][A-Za-z0-9 &\.\-]{1,40})[:\*]',
                  text)
    if m:
        candidate = m.group(1).strip()
        if not DIRTY_COMPANY_PATTERNS.search(candidate):
            return candidate

    # Pattern 4 — iimjobs: "CompanyName - Role Title - iimjobs.com"
    m = re.search(r'^([A-Z][A-Za-z0-9 &\.\-]{2,40})\s*[-–]\s*.+iimjobs',
                  text, re.IGNORECASE | re.MULTILINE)
    if m:
        candidate = m.group(1).strip()
        if not DIRTY_COMPANY_PATTERNS.search(candidate):
            return candidate

    return None


def extract_location(text: str) -> str | None:
    """
    In Greenhouse/Lever markdown the city appears on the line immediately
    after the H1/H2 title heading.
    """
    cities = {
        "bengaluru": "Bengaluru", "bangalore": "Bengaluru",
        "mumbai": "Mumbai", "delhi": "Delhi",
        "gurgaon": "Gurugram", "gurugram": "Gurugram",
        "hyderabad": "Hyderabad", "pune": "Pune",
        "chennai": "Chennai", "noida": "Noida", "kolkata": "Kolkata",
    }

    # Try line after H1/H2 first — most reliable
    heading_re = re.compile(r'^#{1,2}\s+.+$', re.MULTILINE)
    for m in heading_re.finditer(text):
        rest = text[m.end():].lstrip('\n')
        first_line = rest.split('\n')[0].lower().strip()
        for key, canonical in cities.items():
            if key in first_line:
                return canonical

    # Fallback — scan full text
    t = text.lower()
    for key, canonical in cities.items():
        if key in t:
            return canonical

    if "remote" in t and "india" in t:
        return "Remote / India"
    return None


def extract_work_mode(text: str) -> str | None:
    """
    Lever explicitly prints "Remote" or "Hybrid" right under the title.
    Greenhouse buries it in the description.
    """
    t = text.lower()

    # Lever puts work mode as a standalone word in the metadata block
    # e.g. "India\nProduct – Product Management /\nEmployee India /\nRemote"
    lever_meta = re.search(
        r'(?:employee|full.?time|part.?time)[^\n]*\n\s*(remote|hybrid|on.?site)',
        t
    )
    if lever_meta:
        mode = lever_meta.group(1).replace('-', '').replace(' ', '')
        return {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite"}.get(mode)

    onsite = ["5 days a week", "5 days/week", "in-office", "fully onsite",
              "work from office", "wfo", "mandatory office"]
    if any(s in t for s in onsite):
        return "onsite"

    remote_strong = ["fully remote", "100% remote", "work from home", "work from anywhere",
                     "remote-first", "remote first", "remote only", "fully distributed"]
    if any(s in t for s in remote_strong):
        return "remote"

    if "hybrid" in t:
        return "hybrid"

    if "remote" in t:
        return "remote"

    return None


def extract_summary(text: str) -> str | None:
    """2-sentence summary from the first substantive paragraph after the job heading."""
    markers = [
        "about the role", "the role", "what you'll do", "responsibilities",
        "job description", "overview", "about us", "about the company",
        "what we're looking", "position overview",
    ]
    t_lower = text.lower()
    for marker in markers:
        idx = t_lower.find(marker)
        if idx == -1:
            continue
        snippet = text[idx + len(marker):idx + len(marker) + 600].strip()
        snippet = _clean(snippet)
        sentences = re.split(r'(?<=[.!?])\s+', snippet)
        # Skip very short or bullet-looking sentences
        good = [s for s in sentences if len(s) > 30 and not s.startswith('-')]
        summary = ' '.join(good[:2]).strip()
        if len(summary) > 50:
            return summary[:300]

    # Fallback: first long paragraph that looks like prose (not a URL, not nav)
    for para in text.split('\n\n'):
        clean = _clean(para)
        if len(clean) < 80:
            continue
        if clean.startswith('[') or clean.startswith('#'):
            continue
        if re.search(r'https?://', clean):
            continue  # skip paragraphs that are just a URL
        if re.match(r'^[\d\.\>]+\s', clean):
            continue  # skip breadcrumb nav like "1. Home 2. > 3. Consulting"
        if clean.count('. ') < 1 and clean.count(', ') < 1:
            continue  # skip single-word or list-style lines
        return clean[:300]
    return None


# ─── Field extraction: LLM-first, regex fallback ─────────────────────────────

def extract_fields_for_job(job_id, markdown, company, norm_company, norm_function,
                           employment_type, description_summary, cur) -> dict:
    """
    Build the column updates for one job from its JD markdown.

    Tries the LLM extractor (llm_extract) first; falls back to the regex
    extractors when the model is unavailable, errors, or returns low
    confidence — so there is never a regression versus the old behaviour.
    """
    updates: dict = {}

    # ── LLM extraction ──
    ext = None
    try:
        from llm_extract import extract_job_fields
        ext = extract_job_fields(markdown, {"raw_company": norm_company or company})
    except Exception as e:
        print(f"  ! llm_extract error: {e}")

    if ext and ext.get("confidence", 0) >= 0.55:
        if ext["title"]:
            updates["norm_title"] = ext["title"]
        if ext["company"]:
            updates["company"] = ext["company"]
            updates["norm_company"] = ext["company"]
        if ext["location"]:
            updates["norm_location_city"] = ext["location"]
            updates["location"] = ext["location"]
        if ext["work_mode"]:
            updates["employment_type"] = ext["work_mode"]
            updates["norm_remote_type"] = ext["work_mode"]
        if ext["domain"]:
            updates["norm_function"] = ext["domain"]
        if ext["summary"]:
            updates["description_summary"] = ext["summary"]
        india = ext["india_status"]
        updates["india_hiring"] = india
        if india == "confirmed" and ext["confidence"] >= 0.8:
            updates["review_status"] = "approved"
            updates["needs_review"] = False
        elif india == "rejected":
            updates["review_status"] = "rejected"
            updates["needs_review"] = False
        else:
            # unknown, or confirmed-but-not-confident → keep in the review queue
            updates["review_status"] = "pending"
            updates["needs_review"] = True
        print(f"  ⓘ llm-extract: conf={ext['confidence']:.2f} india={india}")
        return updates

    # ── Regex fallback (LLM unavailable / errored / low confidence) ──
    try:
        from pipeline import classify_india_hiring
        cur.execute(
            "SELECT india_hiring, location, COALESCE(norm_company, company) "
            "FROM jobs WHERE id = %s", (job_id,)
        )
        cur_status, cur_location, cur_company = cur.fetchone() or (None, "", "")
        if cur_status in (None, "unknown"):
            new_status = classify_india_hiring(markdown, cur_location or "", cur_company or "")
            if new_status == "confirmed":
                updates["india_hiring"] = "confirmed"
                updates["review_status"] = "approved"
                updates["needs_review"] = False
            elif new_status == "rejected":
                updates["india_hiring"] = "rejected"
                updates["review_status"] = "rejected"
                updates["needs_review"] = False
    except Exception as e:
        print(f"  ! india check failed: {e}")

    if employment_type is None:
        mode = extract_work_mode(markdown)
        if mode:
            updates["employment_type"] = mode
            updates["norm_remote_type"] = mode

    if description_summary is None:
        summary = extract_summary(markdown)
        if summary:
            updates["description_summary"] = summary

    if DIRTY_COMPANY_PATTERNS.search(norm_company or "") or \
       (norm_company or "").lower() == (norm_function or "").lower():
        clean_company = extract_company(markdown, company)
        if clean_company:
            updates["company"] = clean_company
            updates["norm_company"] = clean_company

    cur.execute("SELECT norm_location_city FROM jobs WHERE id = %s", (job_id,))
    current_city = (cur.fetchone() or [None])[0]
    if not current_city or current_city in ("India", "Unknown", "Remote"):
        loc = extract_location(markdown)
        if loc:
            updates["norm_location_city"] = loc
            updates["location"] = loc

    return updates


# ─── Update DB ────────────────────────────────────────────────────────────────

def update_job(job_id: str, updates: dict):
    if not updates:
        return
    updates["last_enriched_at"] = datetime.now(timezone.utc)
    updates["updated_at"] = datetime.now(timezone.utc)
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [job_id]
    cur.execute(f"UPDATE jobs SET {set_clause} WHERE id = %s", values)


# ─── Main ─────────────────────────────────────────────────────────────────────

def run():
    force = os.environ.get("ENRICH_FORCE") == "1"
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    print(f"Enrichment run — {stamp}{'  [FORCE — cooldown ignored]' if force else ''}")
    jobs = fetch_dirty_jobs(BATCH, force=force)
    print(f"  {len(jobs)} dirty jobs to enrich")

    enriched = 0
    for row in jobs:
        job_id, company, norm_company, norm_function, employment_type, description_summary, confidence, job_url = row
        try:
            # Dead-link check first — fast HEAD request
            try:
                head = requests.head(job_url, timeout=8, allow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0"})
                if head.status_code in (404, 410):
                    print(f"  ✗ dead link ({head.status_code}): {company[:30]} — {job_url[:60]}")
                    cur.execute(
                        "UPDATE jobs SET review_status='rejected', last_enriched_at=%s WHERE id=%s",
                        (datetime.now(timezone.utc), job_id)
                    )
                    conn.commit()
                    continue
            except Exception:
                pass  # network error — skip check, try fetch anyway

            markdown = fetch_page(job_url)
            if not markdown:
                # Check how many times we've failed on this job (approximate by age).
                # If it's been pending > 72h with enrichment attempted, give up.
                cur.execute(
                    "SELECT first_seen_at, last_enriched_at FROM jobs WHERE id = %s",
                    (job_id,)
                )
                age_row = cur.fetchone()
                age_h = 0
                if age_row and age_row[0]:
                    first_seen = age_row[0]
                    if first_seen.tzinfo is None:
                        first_seen = first_seen.replace(tzinfo=timezone.utc)
                    age_h = (datetime.now(timezone.utc) - first_seen).total_seconds() / 3600
                if age_h > 72 and age_row and age_row[1]:  # old AND already tried before
                    print(f"  ✗ unscrapeable (>72h): {company[:30]} — {job_url[:60]}")
                    cur.execute(
                        "UPDATE jobs SET review_status='rejected', "
                        "review_notes='auto-rejected: unscrapeable after 72h', "
                        "last_enriched_at=%s WHERE id=%s",
                        (datetime.now(timezone.utc), job_id)
                    )
                else:
                    print(f"  ⚠ could not fetch JD page: {company[:28]} — {job_url[:50]}")
                    cur.execute("UPDATE jobs SET last_enriched_at = %s WHERE id = %s",
                                (datetime.now(timezone.utc), job_id))
                conn.commit()
                continue

            updates = {}

            # Startup-context guard for "strategic initiatives" titles (SPEC.md v0.4).
            # Strategic Initiatives at an enterprise is out of scope; only startup context passes.
            cur.execute("SELECT COALESCE(norm_function, domain), COALESCE(norm_title, title) FROM jobs WHERE id = %s", (job_id,))
            _domain_row = cur.fetchone() or (None, "")
            _job_domain, _job_title = _domain_row
            if _job_domain == "strategy" and "strategic initiatives" in (_job_title or "").lower():
                startup_signals = ["series a", "series b", "series c", "seed", "startup",
                                   "early stage", "growth stage", "founder"]
                if not any(s in markdown.lower() for s in startup_signals):
                    print(f"  ✗ non-startup strategic initiatives: {company[:30]}")
                    cur.execute(
                        "UPDATE jobs SET review_status='rejected', last_enriched_at=%s WHERE id=%s",
                        (datetime.now(timezone.utc), job_id)
                    )
                    conn.commit()
                    continue

            # "No longer accepting applications" / closed-job detection (per spec rule 5).
            # If the JD says the position is closed, mark as rejected immediately.
            closed_phrases = [
                "no longer accepting applications", "no longer accepting",
                "this position has been filled", "position has been filled",
                "position closed", "job has expired", "this role is closed",
                "we are no longer hiring", "this opening is closed",
                "applications are closed", "role has been filled",
            ]
            md_lower = markdown.lower()
            if any(p in md_lower for p in closed_phrases):
                print(f"  ✗ closed: {company[:30]} — JD says position is closed")
                cur.execute(
                    "UPDATE jobs SET review_status='rejected', india_hiring='rejected', "
                    "last_enriched_at=%s WHERE id=%s",
                    (datetime.now(timezone.utc), job_id)
                )
                conn.commit()
                continue

            # Structured field extraction — LLM-first, regex fallback.
            updates.update(extract_fields_for_job(
                job_id, markdown, company, norm_company, norm_function,
                employment_type, description_summary, cur,
            ))

            if updates:
                updates["normalization_confidence"] = 0.92
                update_job(job_id, updates)
                enriched += 1
                new_company = updates.get("company", company)
                new_mode = updates.get("employment_type", employment_type or "?")
                print(f"  ✓ {new_company[:30]} — {new_mode} — {list(updates.keys())}")
            else:
                # Nothing improved but mark attempted
                cur.execute("UPDATE jobs SET last_enriched_at = %s WHERE id = %s",
                            (datetime.now(timezone.utc), job_id))

            # Commit each job as it finishes so a large batch is crash-safe.
            conn.commit()

        except Exception as e:
            print(f"  ✗ {company[:30]}: {e}")

    conn.commit()
    cur.close()
    conn.close()
    print(f"\nDone. {enriched}/{len(jobs)} records improved.")


if __name__ == "__main__":
    run()
