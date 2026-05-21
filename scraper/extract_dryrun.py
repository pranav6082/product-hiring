"""
Dry-run validator for llm_extract — writes NOTHING to the database.

Samples live jobs, fetches each JD, runs the LLM extractor, and prints a
before -> after comparison so the new extraction can be reviewed before it is
wired into the live enrichment path.

Run:  python extract_dryrun.py [--sample=20]
"""

import os
import sys
import psycopg2
import psycopg2.extras

from enrich import fetch_page
from llm_extract import extract_job_fields, GEMINI_KEY


def _sample(n: int):
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT j.id,
               COALESCE(j.norm_title, j.title)            AS title,
               COALESCE(j.norm_company, j.company)        AS company,
               COALESCE(j.norm_location_city, j.location) AS location,
               COALESCE(j.norm_function, j.domain)        AS domain,
               j.india_hiring,
               j.review_status,
               COALESCE(
                   j.job_url,
                   (SELECT signal_url FROM signals
                    WHERE job_id = j.id AND signal_url IS NOT NULL
                    ORDER BY scraped_at DESC LIMIT 1)
               ) AS url
        FROM jobs j
        WHERE COALESCE(j.norm_function, j.domain) IN ('pm', 'strategy')
          AND j.review_status IN ('approved', 'pending')
        ORDER BY random()
        LIMIT %s
    """, (n,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [r for r in rows if r["url"]]


def _show(label: str, old, new):
    old = (old or "").strip()
    new = (new or "").strip() if new else ""
    flag = ">>" if (new and new != old) else "  "
    print(f"   {flag} {label:9}: {old!r}")
    if new and new != old:
        print(f"      {'':9}  ->  {new!r}")


def main():
    n = 20
    for a in sys.argv[1:]:
        if a.startswith("--sample="):
            n = int(a.split("=", 1)[1])

    if not GEMINI_KEY:
        print("ERROR: GEMINI_API_KEY not set — cannot run extraction dry run.")
        sys.exit(1)

    rows = _sample(n)
    print(f"DRY RUN — llm_extract over {len(rows)} sampled jobs. Writes nothing.\n")

    stats = {"extracted": 0, "failed": 0, "title_chg": 0,
             "company_chg": 0, "domain_chg": 0, "not_pm": 0}

    for i, r in enumerate(rows, 1):
        print(f"{i}. job {str(r['id'])[:8]}  [{r['domain']}/{r['india_hiring']}]  {r['url'][:70]}")
        md = fetch_page(r["url"])
        if not md:
            print("   (could not fetch JD page — skipped)\n")
            stats["failed"] += 1
            continue
        ext = extract_job_fields(md, {
            "raw_title": r["title"],
            "raw_company": r["company"],
            "raw_location": r["location"],
        })
        if not ext:
            print("   (LLM extraction failed — would fall back to regex)\n")
            stats["failed"] += 1
            continue

        stats["extracted"] += 1
        _show("title", r["title"], ext["title"])
        _show("company", r["company"], ext["company"])
        _show("domain", r["domain"], ext["domain"])
        _show("india", r["india_hiring"], ext["india_status"])
        print(f"      summary  : {(ext['summary'] or '')[:150]!r}")
        print(f"      is_pm={ext['is_pm_role']}  confidence={ext['confidence']:.2f}\n")

        if (ext["title"] or "").strip() != (r["title"] or "").strip():
            stats["title_chg"] += 1
        if ext["company"] and ext["company"].strip() != (r["company"] or "").strip():
            stats["company_chg"] += 1
        if ext["domain"] and ext["domain"] != r["domain"]:
            stats["domain_chg"] += 1
        if not ext["is_pm_role"] and r["domain"] == "pm":
            stats["not_pm"] += 1

    print("=" * 60)
    print("SUMMARY")
    print(f"  sampled               : {len(rows)}")
    print(f"  extracted ok          : {stats['extracted']}")
    print(f"  fetch / LLM failed    : {stats['failed']}")
    print(f"  title would change    : {stats['title_chg']}")
    print(f"  company would change  : {stats['company_chg']}")
    print(f"  domain would flip     : {stats['domain_chg']}")
    print(f"  pm-domain rows that are NOT a real PM role: {stats['not_pm']}")
    print("\nNothing was written. Review above, then wire llm_extract into enrich.py.")


if __name__ == "__main__":
    main()
