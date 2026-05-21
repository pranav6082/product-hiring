"""
Loop 1 — Reliability checks (run every 2 hours).
6 health checks: R1–R6. Rule-based fixes where possible. Telegram alerts.
"""

import os
import re
import logging
import subprocess
import requests
from datetime import datetime, timezone, timedelta
from db import get_cur, get_state, save_state, mark_alerted, mark_fixed

logger = logging.getLogger(__name__)

BOARD_URL = os.environ.get("BOARD_URL", "https://board-pi-eight.vercel.app")
GH_TOKEN  = os.environ.get("GH_TOKEN", "")
GH_REPO   = os.environ.get("GH_REPO", "pg-pranav/claude-work")   # override in workflow
GH_API    = "https://api.github.com"


# ─── Telegram alert helper ───────────────────────────────────────────────────

def _telegram(msg: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat  = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        logger.warning("Telegram not configured — skipping alert")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")


def _alert(check_name: str, state: str, msg: str):
    """Send Telegram alert and record it."""
    icon = "🔴" if state == "critical" else "⚠️"
    full_msg = f"{icon} <b>Monitor [{check_name}] {state.upper()}</b>\n{msg}"
    _telegram(full_msg)
    mark_alerted(check_name)
    logger.warning(f"Alert sent: [{check_name}] {state} — {msg}")


# ─── R1: Pipeline ingestion ───────────────────────────────────────────────────

def check_r1_ingestion() -> tuple[str, dict]:
    cur = get_cur()
    cur.execute("""
        SELECT
          COUNT(*) FILTER (WHERE first_seen_at > NOW() - INTERVAL '6 hours')  AS added_6h,
          COUNT(*) FILTER (WHERE first_seen_at > NOW() - INTERVAL '24 hours') AS added_24h,
          MAX(first_seen_at) AS last_added_at
        FROM jobs
    """)
    row = cur.fetchone()
    added_6h  = row["added_6h"]  or 0
    added_24h = row["added_24h"] or 0
    last_at   = row["last_added_at"]

    if added_6h >= 1:
        return "ok", {"added_6h": added_6h, "added_24h": added_24h}
    if added_24h >= 1:
        return "warn", {"added_6h": 0, "added_24h": added_24h, "last_added_at": str(last_at)}
    return "critical", {"added_6h": 0, "added_24h": 0, "last_added_at": str(last_at)}


def fix_r1():
    """Trigger pipeline workflows."""
    logger.info("R1 fix: triggering pipeline-fast.yml")
    for wf in ("pipeline-fast.yml",):
        _trigger_workflow(wf)
    mark_fixed("R1")


# ─── R2: Enrichment activity ──────────────────────────────────────────────────

def check_r2_enrichment() -> tuple[str, dict]:
    cur = get_cur()
    cur.execute("""
        SELECT MAX(last_enriched_at) AS last_enriched_at FROM jobs
        WHERE last_enriched_at IS NOT NULL
    """)
    row = cur.fetchone()
    last_at = row["last_enriched_at"]
    if last_at is None:
        return "critical", {"last_enriched_at": None}

    # Ensure timezone-aware comparison
    now = datetime.now(timezone.utc)
    if last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=timezone.utc)

    age_h = (now - last_at).total_seconds() / 3600

    if age_h <= 2:
        return "ok", {"age_hours": round(age_h, 1)}
    if age_h <= 6:
        return "warn", {"age_hours": round(age_h, 1)}

    # > 6 hours — critical only if there's a pending backlog
    cur.execute("""
        SELECT COUNT(*) AS pending
        FROM jobs
        WHERE review_status = 'pending'
          AND COALESCE(norm_function, domain) IN ('pm', 'strategy')
    """)
    pending = cur.fetchone()["pending"] or 0
    if pending > 5:
        return "critical", {"age_hours": round(age_h, 1), "pending": pending}
    return "warn", {"age_hours": round(age_h, 1), "pending": pending}


def fix_r2():
    logger.info("R2 fix: triggering enrich.yml")
    _trigger_workflow("enrich.yml")
    mark_fixed("R2")


# ─── R3: Pending backlog ──────────────────────────────────────────────────────

def check_r3_backlog() -> tuple[str, dict]:
    cur = get_cur()
    cur.execute("""
        SELECT
          COUNT(*) FILTER (WHERE review_status = 'pending') AS pending,
          COUNT(*) FILTER (WHERE review_status = 'pending'
            AND first_seen_at < NOW() - INTERVAL '12 hours') AS pending_old
        FROM jobs
        WHERE COALESCE(norm_function, domain) IN ('pm', 'strategy')
    """)
    row = cur.fetchone()
    pending     = row["pending"]     or 0
    pending_old = row["pending_old"] or 0

    if pending_old >= 25:
        return "critical", {"pending": pending, "pending_old": pending_old}
    if pending >= 30 or pending_old >= 10:
        return "warn", {"pending": pending, "pending_old": pending_old}
    return "ok", {"pending": pending, "pending_old": pending_old}


def fix_r3():
    logger.info("R3 fix: triggering enrich.yml")
    _trigger_workflow("enrich.yml")
    mark_fixed("R3")


# ─── R4: Rejection rate spike ─────────────────────────────────────────────────

def check_r4_rejection_rate() -> tuple[str, dict]:
    cur = get_cur()
    cur.execute("""
        SELECT
          COUNT(*) FILTER (WHERE review_status = 'rejected'
            AND first_seen_at > NOW() - INTERVAL '24 hours') AS rejected_24h,
          COUNT(*) FILTER (WHERE first_seen_at > NOW() - INTERVAL '24 hours') AS total_24h
        FROM jobs
        WHERE COALESCE(norm_function, domain) IN ('pm', 'strategy')
    """)
    row = cur.fetchone()
    rejected = row["rejected_24h"] or 0
    total    = row["total_24h"]    or 0

    if total < 5:
        return "ok", {"total_24h": total, "rejected_24h": rejected, "ratio": None}

    ratio = rejected / total
    meta  = {"total_24h": total, "rejected_24h": rejected, "ratio": round(ratio, 2)}
    if ratio >= 0.85:
        return "critical", meta
    if ratio >= 0.60:
        return "warn", meta
    return "ok", meta


# ─── R5: Board availability ───────────────────────────────────────────────────

def check_r5_board() -> tuple[str, dict]:
    try:
        resp = requests.get(BOARD_URL, timeout=15)
        if resp.status_code != 200:
            return "critical", {"http_code": resp.status_code}
        # Check if the page has any job content (look for job row indicators)
        body = resp.text.lower()
        has_content = any(k in body for k in [
            "product manager", "chief of staff", "founder", "job-row",
            "data-job", "role", "apply",
        ])
        if not has_content:
            return "warn", {"http_code": 200, "reason": "Board returned 200 but looks empty"}
        return "ok", {"http_code": 200}
    except requests.exceptions.Timeout:
        return "critical", {"reason": "Board request timed out"}
    except Exception as e:
        return "critical", {"reason": str(e)[:120]}


# ─── R6: GitHub Actions workflow health ───────────────────────────────────────

def check_r6_github_actions() -> tuple[str, dict]:
    if not GH_TOKEN:
        return "warn", {"reason": "GH_TOKEN not set — cannot check workflow health"}

    headers = {
        "Authorization": f"token {GH_TOKEN}",
        "Accept": "application/vnd.github+json",
    }

    try:
        resp = requests.get(
            f"{GH_API}/repos/{GH_REPO}/actions/runs",
            params={"per_page": 30, "branch": "main"},
            headers=headers, timeout=15,
        )
        if resp.status_code != 200:
            return "warn", {"reason": f"GH API returned {resp.status_code}"}

        runs = resp.json().get("workflow_runs", [])
    except Exception as e:
        return "warn", {"reason": f"GH API error: {str(e)[:80]}"}

    # Group by workflow name, get consecutive failures from head
    workflows: dict[str, list] = {}
    for run in runs:
        wf = run.get("name", "unknown")
        workflows.setdefault(wf, []).append(run)

    issues = []
    worst = "ok"

    for wf_name, wf_runs in workflows.items():
        # Only monitor our pipeline/enrich/monitor workflows
        relevant = any(kw in wf_name.lower() for kw in
                       ["pipeline", "enrich", "monitor"])
        if not relevant:
            continue

        consecutive_fails = 0
        for run in wf_runs:  # already sorted newest-first from API
            conclusion = run.get("conclusion")
            status     = run.get("status")

            # Still running is ok
            if status == "in_progress":
                break

            # Queued/cancelled with no steps = runner starvation
            if status in ("queued", "waiting") or (
                conclusion == "cancelled" and run.get("run_attempt", 1) == 1
            ):
                consecutive_fails += 1
            elif conclusion not in ("success",):
                consecutive_fails += 1
            else:
                break

        if consecutive_fails >= 3:
            worst = "critical"
            issues.append({"workflow": wf_name, "consecutive_fails": consecutive_fails})
        elif consecutive_fails >= 1:
            if worst == "ok":
                worst = "warn"
            issues.append({"workflow": wf_name, "consecutive_fails": consecutive_fails})

    return worst, {"issues": issues}


def fix_r6(metadata: dict):
    """
    If workflows are cancelled/queued (minutes exhausted), bump cron intervals.
    This is heuristic — only act if pattern matches runner starvation.
    """
    issues = metadata.get("issues", [])
    # Detect runner starvation: all failing workflows have "cancelled" runs
    logger.info(f"R6 issues: {issues} — no automated cron-editing; alerting only")
    # We don't auto-edit workflow YAMLs here (too risky without more context).
    # The Telegram alert already tells Pranav what to do.
    mark_fixed("R6")


# ─── Workflow trigger helper ──────────────────────────────────────────────────

def _trigger_workflow(workflow_file: str):
    if not GH_TOKEN:
        logger.warning(f"GH_TOKEN not set — cannot trigger {workflow_file}")
        return
    headers = {
        "Authorization": f"token {GH_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    try:
        resp = requests.post(
            f"{GH_API}/repos/{GH_REPO}/actions/workflows/{workflow_file}/dispatches",
            headers=headers,
            json={"ref": "main"},
            timeout=15,
        )
        if resp.status_code == 204:
            logger.info(f"Triggered {workflow_file} ✓")
        else:
            logger.warning(f"Failed to trigger {workflow_file}: {resp.status_code} {resp.text[:80]}")
    except Exception as e:
        logger.warning(f"Error triggering {workflow_file}: {e}")


# ─── Main reliability loop ────────────────────────────────────────────────────

CHECKS = [
    ("R1", "Pipeline ingestion",  check_r1_ingestion, fix_r1),
    ("R2", "Enrichment activity", check_r2_enrichment, fix_r2),
    ("R3", "Pending backlog",     check_r3_backlog, fix_r3),
    ("R4", "Rejection rate",      check_r4_rejection_rate, None),
    ("R5", "Board availability",  check_r5_board, None),
    ("R6", "GH Actions health",   check_r6_github_actions, fix_r6),
]


def _board_snapshot() -> dict:
    """Quick DB snapshot for the progress pulse."""
    try:
        cur = get_cur()
        cur.execute("""
            SELECT
              COALESCE(norm_function, domain) AS domain,
              COUNT(*) FILTER (WHERE review_status = 'approved'
                AND india_hiring = 'confirmed') AS approved,
              COUNT(*) FILTER (WHERE review_status = 'pending')  AS pending,
              COUNT(*) FILTER (WHERE first_seen_at > NOW() - INTERVAL '24 hours') AS added_24h,
              COUNT(*) FILTER (WHERE review_status = 'approved'
                AND first_seen_at > NOW() - INTERVAL '24 hours') AS approved_24h
            FROM jobs
            WHERE COALESCE(norm_function, domain) IN ('pm', 'strategy')
            GROUP BY 1
        """)
        rows = {r["domain"]: dict(r) for r in cur.fetchall()}
        pm = rows.get("pm", {})
        fo = rows.get("strategy", {})
        return {
            "pm_approved":  pm.get("approved", 0),
            "fo_approved":  fo.get("approved", 0),
            "pm_pending":   pm.get("pending", 0),
            "fo_pending":   fo.get("pending", 0),
            "pm_added_24h": pm.get("added_24h", 0),
            "fo_added_24h": fo.get("added_24h", 0),
            "pm_approved_24h": pm.get("approved_24h", 0),
            "fo_approved_24h": fo.get("approved_24h", 0),
        }
    except Exception as e:
        return {"error": str(e)}


def _send_progress_pulse(summary: dict, snapshot: dict):
    """
    Send a short Telegram update after every reliability run.
    Always sent — not just on failure. This gives Pranav a live feed
    of pipeline activity without waiting for the 8am digest.
    """
    from datetime import datetime, timezone
    now_ist = datetime.now(timezone.utc).strftime("%H:%M UTC")  # close enough

    pm  = snapshot.get("pm_approved", 0)
    fo  = snapshot.get("fo_approved", 0)
    pm_p = snapshot.get("pm_pending", 0)
    fo_p = snapshot.get("fo_pending", 0)
    added = snapshot.get("pm_added_24h", 0) + snapshot.get("fo_added_24h", 0)
    new_approved = snapshot.get("pm_approved_24h", 0) + snapshot.get("fo_approved_24h", 0)

    def _bar(n, t, w=8):
        pct = min(int(n / max(t, 1) * w), w)
        return "█" * pct + "░" * (w - pct)

    system_lines = []
    for check_id, info in summary.items():
        r = info.get("result", "?")
        if r == "ok":
            system_lines.append(f"  {check_id} ✅")
        elif r == "warn":
            m = info.get("meta", {})
            system_lines.append(f"  {check_id} ⚠️  {_short_meta(m)}")
        elif r == "critical":
            m = info.get("meta", {})
            system_lines.append(f"  {check_id} 🔴 {_short_meta(m)}")
        elif r == "error":
            system_lines.append(f"  {check_id} ❓ error")

    msg = (
        f"⚡ <b>Pipeline pulse — {now_ist}</b>\n"
        f"\n"
        f"PM  {pm}/50 {_bar(pm,50)} +{new_approved - snapshot.get('fo_approved_24h',0)}↑ today\n"
        f"FO  {fo}/30 {_bar(fo,30)}\n"
        f"Pending: {pm_p+fo_p}  |  Added 24h: {added}\n"
        f"\n"
        + "\n".join(system_lines) +
        f"\n{BOARD_URL}"
    )
    _telegram(msg)


def _short_meta(meta: dict) -> str:
    parts = []
    for k, v in (meta or {}).items():
        if k == "issues":
            for iss in (v or []):
                if isinstance(iss, dict):
                    parts.append(f"{iss.get('workflow','')[:20]} x{iss.get('consecutive_fails','')}")
                else:
                    parts.append(str(iss)[:30])
        elif v not in (None, [], {}):
            parts.append(f"{k}={v}")
    return " ".join(parts[:3])


def run_reliability_loop():
    """Run all 6 health checks. Alert + fix as needed. Returns summary dict."""
    summary = {}

    for check_id, check_name, check_fn, fix_fn in CHECKS:
        try:
            result, meta = check_fn()
            save_state(check_id, result, meta)
            summary[check_id] = {"result": result, "meta": meta}
            logger.info(f"  [{check_id}] {check_name}: {result.upper()} {meta}")

            prev = get_state(check_id)
            consecutive = prev.get("consecutive_fails", 0)
            last_alerted = prev.get("last_alerted_at")

            # Alert cooldown: don't re-alert the same check more than once per 4h
            alert_cooldown = timedelta(hours=4)
            now = datetime.now(timezone.utc)
            should_alert = False
            if last_alerted is None:
                should_alert = True
            else:
                if last_alerted.tzinfo is None:
                    last_alerted = last_alerted.replace(tzinfo=timezone.utc)
                should_alert = (now - last_alerted) > alert_cooldown

            if result == "critical":
                if should_alert:
                    _alert(check_id, "critical",
                           f"{check_name}\n{_format_meta(meta)}")
                # Apply known fix on first occurrence
                if consecutive <= 1 and fix_fn is not None:
                    try:
                        if check_id == "R6":
                            fix_fn(meta)
                        else:
                            fix_fn()
                    except Exception as e:
                        logger.warning(f"Fix for {check_id} failed: {e}")

            elif result == "warn" and consecutive >= 2 and should_alert:
                _alert(check_id, "warn",
                       f"{check_name} has been {result} for {consecutive} consecutive checks\n{_format_meta(meta)}")

        except Exception as e:
            logger.error(f"Check {check_id} threw exception: {e}", exc_info=True)
            summary[check_id] = {"result": "error", "meta": {"error": str(e)}}

    # Always send a progress pulse to Telegram (not just on failure)
    try:
        snapshot = _board_snapshot()
        _send_progress_pulse(summary, snapshot)
    except Exception as e:
        logger.warning(f"Progress pulse failed: {e}")

    return summary


def _format_meta(meta: dict) -> str:
    lines = []
    for k, v in (meta or {}).items():
        lines.append(f"  {k}: {v}")
    return "\n".join(lines)
