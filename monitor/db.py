"""
Shared DB connection and query helpers for the monitor.
All monitor modules import from here — one connection per process.
"""

import os
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

_conn = None


def get_conn():
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg2.connect(os.environ["DATABASE_URL"])
    return _conn


def get_cur():
    return get_conn().cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def commit():
    get_conn().commit()


def rollback():
    get_conn().rollback()


# ─── monitor_state helpers ────────────────────────────────────────────────────

def ensure_monitor_state_table():
    cur = get_cur()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS monitor_state (
          check_name        TEXT PRIMARY KEY,
          last_run_at       TIMESTAMPTZ,
          last_result       TEXT,
          consecutive_fails INT  DEFAULT 0,
          last_alerted_at   TIMESTAMPTZ,
          last_fixed_at     TIMESTAMPTZ,
          fix_attempts_24h  INT  DEFAULT 0,
          metadata          JSONB
        )
    """)
    commit()


def get_state(check_name: str) -> dict:
    cur = get_cur()
    cur.execute("SELECT * FROM monitor_state WHERE check_name = %s", (check_name,))
    row = cur.fetchone()
    if row is None:
        return {
            "check_name": check_name,
            "last_run_at": None,
            "last_result": None,
            "consecutive_fails": 0,
            "last_alerted_at": None,
            "last_fixed_at": None,
            "fix_attempts_24h": 0,
            "metadata": {},
        }
    return dict(row)


def save_state(check_name: str, result: str, metadata: dict = None):
    """Upsert check result. Increments consecutive_fails on non-ok, resets on ok."""
    cur = get_cur()
    existing = get_state(check_name)
    if result == "ok":
        consecutive_fails = 0
    else:
        consecutive_fails = (existing.get("consecutive_fails") or 0) + 1

    cur.execute("""
        INSERT INTO monitor_state
          (check_name, last_run_at, last_result, consecutive_fails, metadata)
        VALUES (%s, NOW(), %s, %s, %s)
        ON CONFLICT (check_name) DO UPDATE SET
          last_run_at       = NOW(),
          last_result       = EXCLUDED.last_result,
          consecutive_fails = EXCLUDED.consecutive_fails,
          metadata          = EXCLUDED.metadata
    """, (check_name, result, consecutive_fails, psycopg2.extras.Json(metadata or {})))
    commit()


def mark_alerted(check_name: str):
    cur = get_cur()
    cur.execute("""
        INSERT INTO monitor_state (check_name, last_alerted_at)
        VALUES (%s, NOW())
        ON CONFLICT (check_name) DO UPDATE SET last_alerted_at = NOW()
    """, (check_name,))
    commit()


def mark_fixed(check_name: str):
    cur = get_cur()
    cur.execute("""
        INSERT INTO monitor_state (check_name, last_fixed_at, fix_attempts_24h)
        VALUES (%s, NOW(), 1)
        ON CONFLICT (check_name) DO UPDATE SET
          last_fixed_at     = NOW(),
          fix_attempts_24h  = COALESCE(
            CASE WHEN monitor_state.last_fixed_at > NOW() - INTERVAL '24 hours'
                 THEN monitor_state.fix_attempts_24h + 1
                 ELSE 1
            END, 1
          )
    """, (check_name,))
    commit()
