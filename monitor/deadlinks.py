"""
I1 — Dead link sweep.
Concurrent GET requests against all live (approved + pending) jobs.
Auto-rejects any job whose URL is dead, redirected off-platform, redirected to a
listing/index page (the job was removed), or has closed-phrase content.

Usage:
    from deadlinks import sweep_dead_links
    results = sweep_dead_links()                # live: rejects dead jobs
    results = sweep_dead_links(dry_run=True)    # reports only, writes nothing

    python deadlinks.py --dry-run               # CLI dry run
"""

import re
import sys
import time
import logging
import requests
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from db import get_cur, commit

logger = logging.getLogger(__name__)

CONCURRENCY = 10
TIMEOUT = 12  # seconds per request

# If a job page contains any of these phrases it's closed.
CLOSED_PHRASES = [
    "no longer accepting applications",
    "this position has been filled",
    "position closed",
    "job has expired",
    "this role is closed",
    "we are no longer hiring",
    "this opening is closed",
    "applications are closed",
    "role has been filled",
    "job is no longer available",
    "this job is no longer active",
    "position is no longer available",
    "this job posting has been closed",
    "this position is no longer available",
    "job is closed",
    "listing has expired",
]

# If the final URL after redirects doesn't contain one of these, it's a bad redirect.
JOB_DOMAINS = [
    "greenhouse.io",
    "lever.co",
    "wellfound.com",
    "iimjobs.com",
    "instahyre.com",
    "naukri.com",
    "linkedin.com/jobs",
    "smartrecruiters.com",
    "workday.com",
    "myworkdayjobs.com",
    "icims.com",
    "taleo.net",
    "jobvite.com",
    "ashbyhq.com",
    "rippling.com",
    "bamboohr.com",
    "recruitee.com",
    "dover.com",
    "jobs.lever.co",
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; HiringMonitor/1.0)",
    "Accept": "text/html,application/xhtml+xml,*/*",
}


def _platform(host: str) -> str:
    """Group host variants that belong to the same ATS platform."""
    host = (host or "").lower()
    if "greenhouse.io" in host:
        return "greenhouse"
    if "lever.co" in host:
        return "lever"
    return host


def _is_index_redirect(orig: str, final: str) -> bool:
    """
    True when a job-posting URL redirected to the company's listing / index
    page instead of the posting itself — i.e. the job was removed.

    The most common live failure: a Greenhouse job `/{company}/jobs/{id}`
    redirects to `/{company}`, or a Lever job `/{company}/{uuid}` redirects
    to `/{company}`. The final URL is still on the same platform, so the
    plain off-platform check misses it.
    """
    o, f = urlparse(orig), urlparse(final)
    if _platform(o.netloc) != _platform(f.netloc):
        return False  # cross-platform handled by the off-platform check
    op = o.path.rstrip("/")
    fp = f.path.rstrip("/")
    if not op or fp == op:
        return False
    if not fp:
        return True  # redirected to the bare platform root
    # Greenhouse: /{company}/jobs/{id} -> /{company}
    if "/jobs/" in op and "/jobs/" not in fp:
        return True
    # General: final path is a proper, shorter prefix of the original —
    # the job-identifying tail segment(s) were dropped.
    if op.startswith(fp + "/") and len(fp) < len(op):
        dropped = op[len(fp):].strip("/")
        if dropped and dropped != "apply":  # /{job}/apply -> /{job} is fine
            return True
    return False


def _looks_like_job_posting(url: str) -> bool:
    """
    True if the URL still points at a *specific* job posting rather than an
    index/careers-home page. Used to tell a live job that simply moved to the
    company's own site (keep) from a removed job that collapsed to an index.
    """
    u = url.lower()
    if re.search(r"(gh_jid|job_id|jobid|posting_id|requisition)=", u):
        return True
    if re.search(r"/\d{5,}", u) or re.search(r"\d{6,}", u):
        return True
    if re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-", u):  # uuid
        return True
    if re.search(r"/(jobs?|listing|posting|opening|vacancy|positions?|p)/[^/?#]{3,}", u):
        return True
    return False


def _check_url(job_id: str, url: str) -> dict:
    """
    Returns:
        {"job_id": ..., "url": ...,
         "status": "ok"|"dead"|"closed"|"redirect"|"blocked",
         "http_code": ..., "reason": ...}

    Only "dead"/"closed"/"redirect" cause a job to be rejected. "blocked"
    (403/429/5xx/timeout — the checker was blocked, not the job) never does.
    """
    try:
        resp = requests.get(
            url, headers=HEADERS, timeout=TIMEOUT,
            allow_redirects=True,
            stream=True,   # don't download the whole body yet
        )
        final_url = resp.url
        http_code = resp.status_code

        # 1. HTTP status
        if http_code in (404, 410):
            return {"job_id": job_id, "url": url, "status": "dead",
                    "http_code": http_code, "reason": f"HTTP {http_code}"}

        # 401/403/429 = the checker was blocked or rate-limited (common from a
        # CI runner IP), 5xx = a transient server error. None means the job is
        # gone — mark inconclusive and never reject on them.
        if http_code in (401, 403, 429) or http_code >= 500:
            return {"job_id": job_id, "url": url, "status": "blocked",
                    "http_code": http_code, "reason": f"HTTP {http_code} — inconclusive"}

        if http_code >= 400:
            return {"job_id": job_id, "url": url, "status": "dead",
                    "http_code": http_code, "reason": f"HTTP {http_code}"}

        # 2. Redirect handling
        if final_url != url:
            # 2a. redirected to the company's listing/index page → job removed
            if _is_index_redirect(url, final_url):
                return {"job_id": job_id, "url": url, "status": "redirect",
                        "http_code": http_code,
                        "reason": f"Job removed — redirected to listing page ({final_url[:70]})"}
            # 2b. redirected off the job platform — dead only if it did NOT
            #     land on another specific job posting (many companies host on
            #     Greenhouse but redirect to their own careers page for a live job).
            on_platform = any(d in final_url for d in JOB_DOMAINS)
            if not on_platform and not _looks_like_job_posting(final_url):
                return {"job_id": job_id, "url": url, "status": "redirect",
                        "http_code": http_code,
                        "reason": f"Redirected off-platform to a non-job page → {final_url[:70]}"}

        # 3. Content check for closed phrases (read first 8KB only)
        content = ""
        try:
            content = next(resp.iter_content(chunk_size=8192, decode_unicode=True), b"")
            if isinstance(content, bytes):
                content = content.decode("utf-8", errors="ignore")
            content = content.lower()
        except Exception:
            pass

        for phrase in CLOSED_PHRASES:
            if phrase in content:
                return {"job_id": job_id, "url": url, "status": "closed",
                        "http_code": http_code, "reason": f"Closed phrase: '{phrase}'"}

        return {"job_id": job_id, "url": url, "status": "ok",
                "http_code": http_code, "reason": None}

    except requests.exceptions.Timeout:
        # Slow site or transient — inconclusive, do not reject.
        return {"job_id": job_id, "url": url, "status": "blocked",
                "http_code": None, "reason": "Timeout — inconclusive"}
    except requests.exceptions.ConnectionError as e:
        return {"job_id": job_id, "url": url, "status": "blocked",
                "http_code": None, "reason": f"Connection error — inconclusive: {str(e)[:50]}"}
    except Exception as e:
        return {"job_id": job_id, "url": url, "status": "blocked",
                "http_code": None, "reason": f"Error — inconclusive: {str(e)[:50]}"}


def sweep_dead_links(days_old: int = 0, dry_run: bool = False) -> dict:
    """
    Check every live (approved + pending) PM / Founder's-Office job.
    Auto-rejects jobs whose URLs are dead / closed / redirected to a listing page.

    Args:
        days_old: only check jobs first seen more than this many days ago
                  (0 = check everything, including jobs added today).
        dry_run:  when True, report what would be rejected but write nothing.

    The URL checked is the job's own `job_url`, falling back to the most
    recent `signal_url` — i.e. the link a candidate actually clicks on the board.

    Returns:
        {"checked", "ok", "dead", "closed", "redirect", "rejected",
         "dry_run", "details": [...]}
    """
    cur = get_cur()

    cur.execute("""
        SELECT j.id, COALESCE(j.norm_title, j.title) AS title,
               COALESCE(j.norm_company, j.company) AS company,
               COALESCE(
                   j.job_url,
                   (SELECT signal_url FROM signals
                    WHERE job_id = j.id AND signal_url IS NOT NULL
                    ORDER BY scraped_at DESC LIMIT 1)
               ) AS job_url
        FROM jobs j
        WHERE j.review_status IN ('approved', 'pending')
          AND COALESCE(j.norm_function, j.domain) IN ('pm', 'strategy')
          AND j.first_seen_at < NOW() - INTERVAL '%s days'
        ORDER BY j.last_enriched_at ASC NULLS FIRST
    """ % int(days_old))
    rows = [r for r in cur.fetchall() if r["job_url"]]

    if not rows:
        return {"checked": 0, "ok": 0, "dead": 0, "closed": 0,
                "redirect": 0, "rejected": 0, "dry_run": dry_run, "details": []}

    logger.info(
        f"Dead link sweep ({'DRY RUN' if dry_run else 'live'}): "
        f"checking {len(rows)} live jobs (>{days_old} days old)"
    )

    results = {"ok": 0, "dead": 0, "closed": 0, "redirect": 0, "blocked": 0}
    rejected_details = []
    rejected_ids = []

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futures = {
            pool.submit(_check_url, str(row["id"]), row["job_url"]): row
            for row in rows
        }
        for future in as_completed(futures):
            row = futures[future]
            try:
                result = future.result()
            except Exception as e:
                logger.warning(f"Future error for {row['job_url']}: {e}")
                result = {"status": "blocked", "reason": str(e), "http_code": None}

            status = result["status"]
            results[status] = results.get(status, 0) + 1

            # Only definitive signals reject. "blocked"/"ok" never do.
            if status in ("dead", "closed", "redirect"):
                rejected_ids.append(str(row["id"]))
                rejected_details.append({
                    "job_id": str(row["id"]),
                    "title": row["title"],
                    "company": row["company"],
                    "url": row["job_url"],
                    "status": status,
                    "reason": result["reason"],
                })
                logger.info(
                    f"  [{status.upper()}] {row['title']} @ {row['company']} "
                    f"— {result['reason']}"
                )

    # Bulk reject all dead/closed/redirect jobs
    if rejected_ids and not dry_run:
        cur.execute("""
            UPDATE jobs
            SET review_status = 'rejected',
                review_notes  = CONCAT(COALESCE(review_notes, ''), ' [auto-rejected: dead link]'),
                updated_at    = NOW()
            WHERE id = ANY(%s::uuid[])
        """, (rejected_ids,))
        commit()
        logger.info(f"Auto-rejected {len(rejected_ids)} jobs with dead/closed/redirect links")
    elif rejected_ids and dry_run:
        logger.info(f"[DRY RUN] would auto-reject {len(rejected_ids)} jobs with dead/closed/redirect links")

    return {
        "checked": len(rows),
        "ok": results.get("ok", 0),
        "dead": results.get("dead", 0),
        "closed": results.get("closed", 0),
        "redirect": results.get("redirect", 0),
        "blocked": results.get("blocked", 0),
        "rejected": 0 if dry_run else len(rejected_ids),
        "would_reject": len(rejected_ids) if dry_run else 0,
        "dry_run": dry_run,
        "details": rejected_details,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    dry = "--dry-run" in sys.argv
    days = 0
    for arg in sys.argv[1:]:
        if arg.startswith("--days-old="):
            days = int(arg.split("=", 1)[1])
    res = sweep_dead_links(days_old=days, dry_run=dry)
    print()
    print(f"{'DRY RUN — ' if dry else ''}Dead-link sweep complete")
    print(f"  checked : {res['checked']}")
    print(f"  ok      : {res['ok']}")
    print(f"  dead    : {res['dead']}")
    print(f"  closed  : {res['closed']}")
    print(f"  redirect: {res['redirect']}")
    print(f"  blocked : {res.get('blocked', 0)}  (inconclusive — not rejected)")
    print(f"  {'would reject' if dry else 'rejected'}: "
          f"{res.get('would_reject') if dry else res['rejected']}")
    if res["details"]:
        print()
        print("  Affected jobs:")
        for d in res["details"]:
            print(f"    [{d['status'].upper()}] {d['title']} @ {d['company']}")
            print(f"        {d['reason']}")
