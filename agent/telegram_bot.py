"""
Telegram briefing agent.
- Sends unbriefed, validated signals to Pranav every morning
- Accepts /jobs, /stats commands

Run: python telegram_bot.py --send   (send pending briefings)
     python telegram_bot.py --listen  (listen for commands)
"""

import os
import sys
import json
import argparse
import psycopg2
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

BOARD_URL = "https://board-pi-eight.vercel.app"


def telegram_post(method: str, data: dict) -> dict:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def send_message(text: str, parse_mode: str = "Markdown"):
    telegram_post("sendMessage", {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    })


def format_signal(row) -> str:
    title, company, location, domain, posted_by, profile_url, signal_url, urgency = row
    urgency_tag = " 🔥" if urgency else ""
    known_tag = ""

    lines = [f"*{title}* at {company}{urgency_tag}"]
    lines.append(f"📍 {location}  •  {domain.upper()}")
    if profile_url:
        lines.append(f"👤 [{posted_by}]({profile_url})")
    else:
        lines.append(f"👤 {posted_by}")
    if signal_url:
        lines.append(f"[View post]({signal_url})")
    return "\n".join(lines)


def send_briefing():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    cur.execute("""
        SELECT
            j.title, j.company, j.location, j.domain,
            p.name, p.linkedin_url,
            sig.signal_url, sig.urgency_signals,
            sig.id
        FROM signals sig
        JOIN jobs j ON j.id = sig.job_id
        LEFT JOIN people p ON p.id = sig.person_id
        WHERE sig.validated = true AND sig.briefed = false
        ORDER BY sig.scraped_at DESC
        LIMIT 20
    """)
    rows = cur.fetchall()

    if not rows:
        print("No new signals to brief.")
        conn.close()
        return

    # Header
    now = datetime.now(timezone.utc).strftime("%a %d %b")
    send_message(f"*Hiring Signals — {now}*\n{len(rows)} new signal{'s' if len(rows) != 1 else ''}\n{BOARD_URL}")

    # Send each signal
    signal_ids = []
    for row in rows:
        *signal_data, signal_id = row
        try:
            send_message(format_signal(signal_data))
            signal_ids.append(signal_id)
        except Exception as e:
            print(f"Failed to send signal {signal_id}: {e}")

    # Mark as briefed
    if signal_ids:
        cur.execute(
            "UPDATE signals SET briefed = true, briefed_at = now() WHERE id = ANY(%s)",
            (signal_ids,),
        )
        conn.commit()
        print(f"Sent {len(signal_ids)} signals to Telegram.")

    cur.close()
    conn.close()


def get_stats() -> str:
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM jobs WHERE is_active = true")
    total_jobs = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM signals WHERE scraped_at > now() - interval '24 hours'")
    last_24h = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM people WHERE known_to_pranav = true")
    known = cur.fetchone()[0]
    cur.close()
    conn.close()
    return f"*Stats*\n{total_jobs} active jobs\n{last_24h} signals in last 24h\n{known} known contacts\n[Open board]({BOARD_URL})"


def listen():
    print("Listening for Telegram commands...")
    last_update_id = None

    while True:
        try:
            params = {"timeout": 30, "allowed_updates": ["message"]}
            if last_update_id:
                params["offset"] = last_update_id + 1

            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?{urllib.parse.urlencode(params)}"
            with urllib.request.urlopen(url, timeout=35) as resp:
                data = json.loads(resp.read())

            for update in data.get("result", []):
                last_update_id = update["update_id"]
                msg = update.get("message", {})
                text = msg.get("text", "")
                chat_id = str(msg.get("chat", {}).get("id", ""))

                if chat_id != CHAT_ID:
                    continue

                if text == "/jobs":
                    send_briefing()
                elif text == "/stats":
                    send_message(get_stats())
                elif text == "/help":
                    send_message("/jobs — send latest signals\n/stats — show summary\n/help — this message")

        except Exception as e:
            print(f"Error: {e}")
            import time; time.sleep(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--send", action="store_true", help="Send pending briefings")
    parser.add_argument("--listen", action="store_true", help="Listen for commands")
    parser.add_argument("--stats", action="store_true", help="Print stats")
    args = parser.parse_args()

    if args.send:
        send_briefing()
    elif args.listen:
        listen()
    elif args.stats:
        print(get_stats())
    else:
        send_briefing()
