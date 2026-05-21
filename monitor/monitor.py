"""
Monitor entry point.

Usage:
  python monitor.py --loop reliability    # Loop 1: 6 health checks
  python monitor.py --loop improvement    # Loop 2: quality + daily digest
  python monitor.py --loop both           # Run both (for testing)

Environment:
  DATABASE_URL         Neon connection string
  TELEGRAM_BOT_TOKEN   Telegram bot token
  TELEGRAM_CHAT_ID     Telegram chat/user ID
  GH_TOKEN             GitHub PAT (repo + actions:read + actions:write)
  GH_REPO              e.g. "pg-pranav/claude-work"
  BOARD_URL            https://board-pi-eight.vercel.app
"""

import os
import sys
import logging
import argparse
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


def run_reliability():
    from db import ensure_monitor_state_table
    from reliability import run_reliability_loop

    logger.info("=== Loop 1: Reliability ===")
    ensure_monitor_state_table()
    summary = run_reliability_loop()

    ok_count   = sum(1 for v in summary.values() if v.get("result") == "ok")
    warn_count = sum(1 for v in summary.values() if v.get("result") == "warn")
    crit_count = sum(1 for v in summary.values() if v.get("result") == "critical")

    logger.info(f"Reliability: {ok_count} ok, {warn_count} warn, {crit_count} critical")
    return summary


def run_improvement():
    from db import ensure_monitor_state_table
    from deadlinks import sweep_dead_links
    from improvement import run_improvement_checks
    from digest import build_digest, send_digest
    from ai_advisor import run_ai_advisor

    logger.info("=== Loop 2: Quality + Improvement ===")
    ensure_monitor_state_table()

    logger.info("I1: Dead link sweep...")
    i1 = sweep_dead_links(days_old=0)
    logger.info(f"  → checked={i1['checked']}, rejected={i1['rejected']}")

    results = run_improvement_checks()
    i2 = results["i2"]
    i3 = results["i3"]
    i4 = results["i4"]
    i5 = results["i5"]
    i6 = results["i6"]

    # AI advisor — Gemini analyses metrics and applies safe improvements.
    # A manual digest-only run can skip it (SKIP_AI_ADVISOR=1) so the
    # autonomous code editor does not run while data is mid-cleanup.
    if os.environ.get("SKIP_AI_ADVISOR") == "1":
        logger.info("AI advisor: skipped (SKIP_AI_ADVISOR=1)")
        ai = {"error": "skipped for this run", "analysis": "",
              "applied": {}, "code_changes_results": [], "report_only": []}
    else:
        logger.info("AI advisor: analysing metrics and applying improvements...")
        ai = run_ai_advisor({"i1": i1, "i2": i2, "i3": i3, "i4": i4, "i5": i5, "i6": i6})
    logger.info(f"  → analysis: {ai.get('analysis', '')[:120]}")
    logger.info(f"  → applied: {ai.get('applied', {})}")

    logger.info("Building and sending daily digest...")
    msg = build_digest(i1, i2, i3, i4, i5, i6, ai_result=ai)
    send_digest(msg)
    logger.info("Digest sent ✓")

    return {"i1": i1, **results, "ai": ai}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", choices=["reliability", "improvement", "both"],
                        default="reliability")
    args = parser.parse_args()

    if args.loop == "reliability":
        run_reliability()
    elif args.loop == "improvement":
        run_improvement()
    elif args.loop == "both":
        rel = run_reliability()
        imp = run_improvement()
    else:
        logger.error(f"Unknown loop: {args.loop}")
        sys.exit(1)


if __name__ == "__main__":
    main()
