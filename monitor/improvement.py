"""
Loop 2 — Quality + Improvement checks (run daily at 8am IST).
I1–I6: dead links, quality audit, target progress, India whitelist,
source performance, stuck pending jobs.

Also handles I4 auto-apply: updating pipeline.py INDIA_OFFICE_COMPANIES.
"""

import os
import re
import sys
import logging
import subprocess
from datetime import datetime, timezone
from db import get_cur, commit

logger = logging.getLogger(__name__)

# ─── I1: Dead link sweep ──────────────────────────────────────────────────────
# Delegated to deadlinks.py — called from monitor.py

# ─── I2: Quality audit (random 20 sample) ────────────────────────────────────

# Mirror the title classifier from pipeline.py (no import to avoid path issues)
TITLE_DOMAINS_PM = [
    "product manager", "head of product", "vp of product", "vp product",
    "chief product", "cpo", "group product manager", "group pm",
    "director of product", "director, product",
    "lead product manager", "principal product manager", "staff product manager",
    "associate product manager", "senior product manager", "sr product manager",
    "sr. product manager", "founding product manager", "founding pm",
]

TITLE_DOMAINS_STRATEGY = [
    "chief of staff",
    "founder's office", "founders office", "founder office",
    "entrepreneur in residence", " eir ",
    "head of special projects", "special projects lead", "special projects manager",
    "strategic initiatives",
]

ENGINEERING_TITLES = [
    "software development engineer", "software engineer", "full stack engineer",
    "backend engineer", "frontend engineer", "devops engineer", "data engineer",
    "site reliability", " sre ", "qa engineer", "test engineer", "machine learning engineer",
    "ml engineer", "ai engineer", "security engineer", "platform engineer",
    "infrastructure engineer", "cloud engineer", "solutions architect",
    "principal engineer", "staff engineer", "senior software", "lead engineer",
    "software developer", "java developer", "python developer",
    "blockchain", "smart contract",
]

NON_PRODUCT_ROLES = [
    "marketing manager", "growth marketing", "sales manager", "sales development",
    "account manager", "account executive", "customer success",
    "business development", "partner manager", "campaign manager",
    "project manager", "programme manager", "program manager",
    "hr ", "human resources", "talent acquisition", "recruiter",
    "finance manager", "controller", "compliance", "audit",
    "implementation lead", "implementation manager", "delivery manager",
    "technical writer", "executive assistant", "office manager",
    "strategy consultant", "management consultant",
    "vp strategy", "vp of strategy", "director of strategy",
    "chief strategy officer", "chief business officer", "head of strategy",
]

NOISE_TITLES = [
    "industrial", "manufacturing", "safety", "footwear", "gloves", "warehouse",
    "intern", "internship", "fresher", "graduate trainee",
    "seo", "social media", "content", "copywriter",
]

JUNK_COMPANY_NAMES = {
    "unknown", "usa", "us", "uk", "canada", "europe", "global",
    "remote", "india", "group", "lead", "senior", "junior", "nan",
    "wellfound.com", "iimjobs.com", "instahyre.com", "linkedin.com",
}

DIRTY_TITLE_RE = re.compile(
    r'\d{4}'                       # year suffix (e.g. "PM 2024")
    r'|&amp;|&lt;|&gt;|&quot;'    # HTML entities
    r'|[\[\]<>{}|\\]'             # URL/slug fragments
    r'|^\s*\w{1,3}\s*$',          # very short (1-3 char) titles
    re.IGNORECASE,
)


def _classify_title(title: str) -> str | None:
    """Returns 'pm', 'strategy', or None (rejected)."""
    t = title.lower()
    if any(b in t for b in ENGINEERING_TITLES): return None
    if any(b in t for b in NON_PRODUCT_ROLES):  return None
    if any(b in t for b in NOISE_TITLES):        return None
    for kw in TITLE_DOMAINS_PM:
        if kw in t: return "pm"
    for kw in TITLE_DOMAINS_STRATEGY:
        if kw in t: return "strategy"
    return None


def run_i2_quality_audit(sample_size: int = 20) -> dict:
    """
    Sample approved jobs, run each through 5 SPEC criteria.
    Auto-sends failures back to pending.
    """
    cur = get_cur()
    cur.execute("""
        SELECT id,
               COALESCE(norm_title, title)   AS title,
               COALESCE(norm_company, company) AS company,
               job_url,
               india_hiring
        FROM jobs
        WHERE review_status = 'approved'
          AND COALESCE(norm_function, domain) IN ('pm', 'strategy')
        ORDER BY RANDOM()
        LIMIT %s
    """, (sample_size,))
    rows = cur.fetchall()

    passed = 0
    failures = []
    demoted_ids = []

    for row in rows:
        job_id  = str(row["id"])
        title   = row["title"] or ""
        company = row["company"] or ""
        url     = row["job_url"] or ""

        fail_reasons = []

        # Criterion 1: Title is a real PM/FO title
        if _classify_title(title) is None:
            fail_reasons.append(f"Title not classified as PM/FO: '{title}'")

        # Criterion 2: Title is clean
        if DIRTY_TITLE_RE.search(title):
            fail_reasons.append(f"Dirty title pattern: '{title}'")

        # Criterion 3: Company name is correct
        co_lower = company.lower().strip()
        if co_lower in JUNK_COMPANY_NAMES or len(co_lower) < 2:
            fail_reasons.append(f"Bad company name: '{company}'")
        elif re.search(r'^\d+$', company.strip()):
            fail_reasons.append(f"Company looks like a number: '{company}'")

        # Criterion 4: India-eligible (board query should already gate this,
        # but flag if something slipped through)
        if row["india_hiring"] != "confirmed":
            fail_reasons.append(f"india_hiring='{row['india_hiring']}' not confirmed")

        # Criterion 5: Link — we rely on I1 dead link sweep for this.
        # We don't re-check here to avoid double HTTP traffic.

        if fail_reasons:
            failures.append({
                "job_id": job_id,
                "title": title,
                "company": company,
                "reasons": fail_reasons,
            })
            demoted_ids.append(job_id)
        else:
            passed += 1

    # Send failures back to pending for re-enrichment
    if demoted_ids:
        cur.execute("""
            UPDATE jobs
            SET review_status = 'pending',
                needs_review   = true,
                updated_at     = NOW()
            WHERE id = ANY(%s::uuid[])
        """, (demoted_ids,))
        commit()
        logger.info(f"Quality audit: demoted {len(demoted_ids)} jobs to pending")

    return {
        "sampled": len(rows),
        "passed": passed,
        "failed": len(failures),
        "failures": failures,
    }


# ─── I3: Progress toward spec targets ────────────────────────────────────────

def run_i3_target_progress() -> dict:
    cur = get_cur()

    # Current approved counts
    cur.execute("""
        SELECT
          COALESCE(norm_function, domain) AS domain,
          COUNT(*) AS approved_count
        FROM jobs
        WHERE review_status = 'approved'
          AND india_hiring = 'confirmed'
          AND COALESCE(norm_function, domain) IN ('pm', 'strategy')
        GROUP BY 1
    """)
    counts = {row["domain"]: row["approved_count"] for row in cur.fetchall()}

    pm_count = counts.get("pm", 0)
    fo_count = counts.get("strategy", 0)

    # Weekly intake and approval rates per domain
    cur.execute("""
        SELECT
          COALESCE(norm_function, domain) AS domain,
          COUNT(*) FILTER (WHERE first_seen_at > NOW() - INTERVAL '7 days') AS added_7d,
          COUNT(*) FILTER (WHERE review_status = 'approved'
            AND first_seen_at > NOW() - INTERVAL '7 days') AS approved_7d,
          COUNT(*) FILTER (WHERE review_status = 'rejected'
            AND first_seen_at > NOW() - INTERVAL '7 days') AS rejected_7d,
          COUNT(*) FILTER (WHERE review_status = 'pending'
            AND first_seen_at > NOW() - INTERVAL '7 days') AS pending_7d
        FROM jobs
        WHERE COALESCE(norm_function, domain) IN ('pm', 'strategy')
          AND first_seen_at > NOW() - INTERVAL '7 days'
        GROUP BY 1
    """)
    weekly = {row["domain"]: dict(row) for row in cur.fetchall()}

    def gap_analysis(domain, current, target, wdata):
        added    = wdata.get("added_7d", 0)
        approved = wdata.get("approved_7d", 0)
        rejected = wdata.get("rejected_7d", 0)
        conv_rate = round(approved / max(added, 1) * 100)
        rej_rate  = round(rejected / max(added, 1) * 100)

        issues = []
        if added < 5:
            issues.append("low_intake")
        if conv_rate < 15 and added > 5:
            issues.append("low_conversion")
        if rej_rate > 70:
            issues.append("high_rejection")

        return {
            "current": current, "target": target,
            "pct": round(current / target * 100),
            "added_7d": added, "approved_7d": approved,
            "rejected_7d": rejected, "conv_rate_pct": conv_rate,
            "issues": issues,
        }

    return {
        "pm": gap_analysis("pm", pm_count, 50, weekly.get("pm", {})),
        "strategy": gap_analysis("strategy", fo_count, 30, weekly.get("strategy", {})),
    }


# ─── I4: India whitelist gaps ─────────────────────────────────────────────────

def run_i4_india_whitelist() -> dict:
    """
    Find companies enrichment-confirmed as India-eligible but not in pipeline.py whitelist.
    Auto-adds them.
    """
    cur = get_cur()
    cur.execute("""
        SELECT LOWER(COALESCE(norm_company, company)) AS co, COUNT(*) AS confirmations
        FROM jobs
        WHERE india_hiring = 'confirmed'
          AND last_enriched_at IS NOT NULL
          AND review_status = 'approved'
        GROUP BY 1
        HAVING COUNT(*) >= 2
        ORDER BY confirmations DESC
    """)
    db_companies = {row["co"]: row["confirmations"] for row in cur.fetchall()}

    # Read current whitelist from pipeline.py
    pipeline_path = os.path.join(
        os.path.dirname(__file__), "..", "scraper", "pipeline.py"
    )
    pipeline_path = os.path.abspath(pipeline_path)

    try:
        with open(pipeline_path, "r") as f:
            content = f.read()
    except FileNotFoundError:
        logger.warning(f"pipeline.py not found at {pipeline_path}")
        return {"added": [], "error": "pipeline.py not found"}

    # Extract existing whitelist entries
    existing_lower = set()
    m = re.search(
        r'INDIA_OFFICE_COMPANIES\s*=\s*\{([^}]+)\}', content, re.DOTALL
    )
    if m:
        for item in re.findall(r'"([^"]+)"', m.group(1)):
            existing_lower.add(item.lower())

    # Words that indicate a job title / search term snuck in as company name
    JOB_TITLE_WORDS = {
        "product", "manager", "engineer", "designer", "analyst", "developer",
        "lead", "director", "vp", "head", "chief", "principal", "senior", "junior",
        "saas", "b2b", "b2c", "remote", "hiring", "jobs", "role", "position",
    }

    # Find gaps
    gaps = []
    for co, count in db_companies.items():
        if not co or len(co) < 2:
            continue
        if co in existing_lower or co in JUNK_COMPANY_NAMES:
            continue
        if re.search(r'^\d+$', co):
            continue
        # Skip if looks like a job title (contains 2+ title words)
        co_words = set(co.split())
        if len(co_words & JOB_TITLE_WORDS) >= 2:
            continue
        # Skip if too many words (real company names rarely > 5 words)
        if len(co_words) > 5:
            continue
        gaps.append((co, count))

    if not gaps:
        return {"added": [], "skipped": []}

    # Auto-add to pipeline.py
    added = []
    skipped = []
    new_entries = ""
    for co, count in gaps[:10]:  # max 10 per run to be safe
        # Sanitize — only add if it looks like a real company name
        if len(co) < 3 or len(co) > 60:
            skipped.append(co)
            continue
        if re.search(r'[<>&"\'\\]', co):
            skipped.append(co)
            continue
        new_entries += f'\n    "{co}",  # auto-added by monitor (confirmed {count}x)'
        added.append(co)

    if added:
        # Insert before the closing } of INDIA_OFFICE_COMPANIES
        new_content = content.replace(
            "    # VC funds with India offices",
            new_entries + "\n    # VC funds with India offices",
            1,
        )
        if new_content == content:
            # Fallback: find the closing } of the set and insert before it
            new_content = re.sub(
                r'(INDIA_OFFICE_COMPANIES\s*=\s*\{[^}]+)',
                r'\1' + new_entries + "\n",
                content, count=1, flags=re.DOTALL,
            )

        if new_content != content:
            with open(pipeline_path, "w") as f:
                f.write(new_content)
            logger.info(f"I4: Added {len(added)} companies to INDIA_OFFICE_COMPANIES: {added}")

            # Commit and push
            _git_commit_and_push(
                pipeline_path,
                f"monitor: auto-add {len(added)} companies to India whitelist\n\n"
                + "\n".join(f"  + {co}" for co in added),
            )
        else:
            logger.warning("I4: Could not find insertion point in pipeline.py")
            added = []

    return {"added": added, "skipped": skipped, "gaps_found": len(gaps)}


def _git_commit_and_push(filepath: str, message: str):
    """Commit a single file change and push to main."""
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    try:
        subprocess.run(["git", "-C", repo_root, "add", filepath], check=True)
        subprocess.run(
            ["git", "-C", repo_root, "commit", "-m", message],
            check=True, env={**os.environ, "GIT_AUTHOR_NAME": "monitor-bot",
                              "GIT_AUTHOR_EMAIL": "monitor@pipeline",
                              "GIT_COMMITTER_NAME": "monitor-bot",
                              "GIT_COMMITTER_EMAIL": "monitor@pipeline"},
        )
        subprocess.run(["git", "-C", repo_root, "push", "origin", "HEAD:main"], check=True)
        logger.info("Git push succeeded")
    except subprocess.CalledProcessError as e:
        logger.warning(f"Git operation failed: {e}")


# ─── I5: Source performance ───────────────────────────────────────────────────

def run_i5_source_performance() -> dict:
    cur = get_cur()
    # Check if signals and sources tables exist (may not if schema hasn't been applied)
    cur.execute("""
        SELECT EXISTS (
            SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'signals'
        ) AS has_signals
    """)
    if not cur.fetchone()["has_signals"]:
        # Fallback: approximate by source in job_url patterns
        return _i5_fallback_by_url()

    cur.execute("""
        SELECT
          s.name AS source,
          COUNT(DISTINCT j.id) FILTER (WHERE j.review_status = 'approved') AS approved,
          COUNT(DISTINCT j.id) AS total,
          ROUND(
            COUNT(DISTINCT j.id) FILTER (WHERE j.review_status = 'approved')::numeric
            / NULLIF(COUNT(DISTINCT j.id), 0) * 100
          ) AS approval_pct
        FROM signals sig
        JOIN sources s  ON s.id  = sig.source_id
        JOIN jobs    j  ON j.id  = sig.job_id
        WHERE j.first_seen_at > NOW() - INTERVAL '7 days'
          AND COALESCE(j.norm_function, j.domain) IN ('pm', 'strategy')
        GROUP BY s.name
        ORDER BY approval_pct DESC NULLS LAST
    """)
    rows = cur.fetchall()
    sources = []
    for row in rows:
        pct = row["approval_pct"] or 0
        flag = "ok" if pct >= 50 else ("warn" if pct >= 20 else "bad")
        sources.append({"source": row["source"], "approved": row["approved"],
                        "total": row["total"], "approval_pct": pct, "flag": flag})
    return {"sources": sources}


def _i5_fallback_by_url() -> dict:
    """Approximate source from job_url domain."""
    cur = get_cur()
    cur.execute("""
        SELECT job_url, review_status
        FROM jobs
        WHERE first_seen_at > NOW() - INTERVAL '7 days'
          AND COALESCE(norm_function, domain) IN ('pm', 'strategy')
          AND job_url IS NOT NULL
    """)
    rows = cur.fetchall()

    URL_SOURCE_MAP = [
        ("greenhouse.io",  "greenhouse"),
        ("lever.co",       "lever"),
        ("naukri.com",     "naukri"),
        ("iimjobs.com",    "iimjobs"),
        ("instahyre.com",  "instahyre"),
        ("wellfound.com",  "wellfound"),
        ("linkedin.com",   "linkedin"),
        ("indeed.com",     "indeed"),
    ]

    buckets: dict[str, dict] = {}
    for row in rows:
        url = row["job_url"] or ""
        source = "other"
        for domain, name in URL_SOURCE_MAP:
            if domain in url:
                source = name
                break
        if source not in buckets:
            buckets[source] = {"approved": 0, "total": 0}
        buckets[source]["total"] += 1
        if row["review_status"] == "approved":
            buckets[source]["approved"] += 1

    sources = []
    for name, counts in sorted(buckets.items(), key=lambda x: -x[1]["approved"]):
        pct = round(counts["approved"] / max(counts["total"], 1) * 100)
        flag = "ok" if pct >= 50 else ("warn" if pct >= 20 else "bad")
        sources.append({"source": name, "approved": counts["approved"],
                        "total": counts["total"], "approval_pct": pct, "flag": flag})
    return {"sources": sources}


# ─── I6: Stuck pending jobs ───────────────────────────────────────────────────

UNSCRAPEABLE_DOMAINS = ["linkedin.com/jobs", "indeed.com/viewjob", "jooble"]


def run_i6_stuck_pending() -> dict:
    cur = get_cur()
    cur.execute("""
        SELECT id, title, company, job_url, first_seen_at
        FROM jobs
        WHERE review_status = 'pending'
          AND india_hiring = 'unknown'
          AND (last_enriched_at IS NULL
               OR last_enriched_at < first_seen_at + INTERVAL '1 hour')
          AND first_seen_at < NOW() - INTERVAL '48 hours'
          AND COALESCE(norm_function, domain) IN ('pm', 'strategy')
        ORDER BY first_seen_at ASC
        LIMIT 50
    """)
    rows = cur.fetchall()

    unscrapeable_ids = []
    requeue_ids = []
    details = []

    for row in rows:
        url = row["job_url"] or ""
        is_unscrapeable = any(d in url for d in UNSCRAPEABLE_DOMAINS)

        if is_unscrapeable:
            unscrapeable_ids.append(str(row["id"]))
            details.append({"id": str(row["id"]), "title": row["title"],
                            "action": "auto-rejected (unscrapeable URL)"})
        else:
            requeue_ids.append(str(row["id"]))
            details.append({"id": str(row["id"]), "title": row["title"],
                            "action": "re-queued for enrichment"})

    # Auto-reject unscrapeable
    if unscrapeable_ids:
        cur.execute("""
            UPDATE jobs
            SET review_status = 'rejected',
                review_notes  = CONCAT(COALESCE(review_notes,''), ' [auto-rejected: unscrapeable URL]'),
                updated_at    = NOW()
            WHERE id = ANY(%s::uuid[])
        """, (unscrapeable_ids,))
        commit()

    # Re-queue valid stuck jobs (reset so enrichment picks them up again)
    if requeue_ids:
        cur.execute("""
            UPDATE jobs
            SET last_enriched_at = NULL,
                updated_at       = NOW()
            WHERE id = ANY(%s::uuid[])
        """, (requeue_ids,))
        commit()

    return {
        "total_stuck": len(rows),
        "auto_rejected": len(unscrapeable_ids),
        "requeued": len(requeue_ids),
        "details": details,
    }


# ─── Run all improvement checks ──────────────────────────────────────────────

def run_improvement_checks() -> dict:
    """Run I2–I6 (I1 deadlinks is run separately). Returns all results."""
    results = {}

    logger.info("I2: Quality audit...")
    results["i2"] = run_i2_quality_audit()
    logger.info(f"  → {results['i2']['passed']}/{results['i2']['sampled']} passed")

    logger.info("I3: Target progress...")
    results["i3"] = run_i3_target_progress()
    logger.info(f"  → PM {results['i3']['pm']['current']}/50, FO {results['i3']['strategy']['current']}/30")

    logger.info("I4: India whitelist gaps...")
    results["i4"] = run_i4_india_whitelist()
    logger.info(f"  → Added: {results['i4']['added']}")

    logger.info("I5: Source performance...")
    results["i5"] = run_i5_source_performance()

    logger.info("I6: Stuck pending jobs...")
    results["i6"] = run_i6_stuck_pending()
    logger.info(f"  → Rejected: {results['i6']['auto_rejected']}, re-queued: {results['i6']['requeued']}")

    return results
