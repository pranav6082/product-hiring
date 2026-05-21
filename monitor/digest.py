"""
Daily Telegram digest formatter.
Builds the 8am IST morning report from all improvement check results.
"""

import os
import logging
import requests
from datetime import date

logger = logging.getLogger(__name__)

BOARD_URL = os.environ.get("BOARD_URL", "https://board-pi-eight.vercel.app")


def _bar(pct: int, width: int = 10) -> str:
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


def _delta_str(n: int) -> str:
    if n > 0: return f"+{n} this week"
    if n < 0: return f"{n} this week"
    return "no change this week"


def build_digest(i1: dict, i2: dict, i3: dict, i4: dict, i5: dict, i6: dict,
                 reliability: dict = None, ai_result: dict = None) -> str:
    today = date.today().strftime("%a %d %b %Y")
    lines = [f"📊 <b>Pipeline Daily — {today}</b>\n"]

    # ─── BOARD ───────────────────────────────────────────────────────────────
    pm = i3.get("pm", {})
    fo = i3.get("strategy", {})
    pm_n   = pm.get("current", 0)
    fo_n   = fo.get("current", 0)
    pm_pct = min(pm.get("pct", 0), 100)
    fo_pct = min(fo.get("pct", 0), 100)
    pm_wk  = pm.get("approved_7d", 0)
    fo_wk  = fo.get("approved_7d", 0)

    lines.append("<b>BOARD</b>")
    lines.append(f"PM:  {pm_n}/50  {_bar(pm_pct)}  {_delta_str(pm_wk)}")
    lines.append(f"FO:  {fo_n}/30  {_bar(fo_pct)}  {_delta_str(fo_wk)}\n")

    # ─── DEAD LINKS ──────────────────────────────────────────────────────────
    lines.append("<b>DEAD LINKS</b>")
    if i1:
        dead     = i1.get("dead", 0)
        closed   = i1.get("closed", 0)
        redirect = i1.get("redirect", 0)
        total_killed = dead + closed + redirect
        if total_killed > 0:
            lines.append(f"{total_killed} killed  (404: {dead}, closed: {closed}, redirect: {redirect})")
        else:
            lines.append(f"0 killed — all {i1.get('checked', 0)} checked links alive")
    else:
        lines.append("(not run)")
    lines.append("")

    # ─── ADDED YESTERDAY ─────────────────────────────────────────────────────
    lines.append("<b>ADDED YESTERDAY</b>")
    pm_added  = pm.get("added_7d", 0)
    fo_added  = fo.get("added_7d", 0)
    total_added = pm_added + fo_added
    pm_rej  = pm.get("rejected_7d", 0)
    fo_rej  = fo.get("rejected_7d", 0)
    total_rej = pm_rej + fo_rej
    total_7d  = total_added + total_rej + pm.get("pending_7d", 0) + fo.get("pending_7d", 0)
    appr_rate = round(total_added / max(total_7d, 1) * 100)
    lines.append(f"+{total_added} approved (7d)  (PM: {pm_added}, FO: {fo_added})")
    lines.append(f"Approval rate: {appr_rate}%  (rejected: {total_rej} / 7d)\n")

    # ─── ENRICHMENT ──────────────────────────────────────────────────────────
    lines.append("<b>ENRICHMENT</b>")
    if i6:
        lines.append(f"Stuck > 48h: {i6.get('total_stuck', 0)}")
        lines.append(f"  Auto-rejected (unscrapeable): {i6.get('auto_rejected', 0)}")
        lines.append(f"  Re-queued: {i6.get('requeued', 0)}")
    lines.append("")

    # ─── QUALITY AUDIT ───────────────────────────────────────────────────────
    lines.append("<b>QUALITY AUDIT (20 sampled)</b>")
    if i2:
        passed  = i2.get("passed", 0)
        sampled = i2.get("sampled", 0)
        failed  = i2.get("failed", 0)
        icon    = "✅" if failed == 0 else ("⚠️" if failed <= 3 else "🔴")
        lines.append(f"{icon} {passed}/{sampled} passed all criteria")
        for f in i2.get("failures", []):
            reasons = "; ".join(f.get("reasons", []))
            lines.append(f'    • "{f["title"]}" @ {f["company"]} — {reasons}')
    else:
        lines.append("(not run)")
    lines.append("")

    # ─── SOURCE PERFORMANCE ──────────────────────────────────────────────────
    lines.append("<b>SOURCE PERFORMANCE (7d)</b>")
    if i5 and i5.get("sources"):
        for src in i5["sources"]:
            pct  = src.get("approval_pct", 0)
            flag = {"ok": "✅", "warn": "⚠️", "bad": "🔴"}.get(src.get("flag", "ok"), "")
            bar  = _bar(min(pct, 100), 8)
            lines.append(f"  {src['source']:<14} {bar}  {pct}%  {flag}")
    else:
        lines.append("  (no signal data yet)")
    lines.append("")

    # ─── INDIA WHITELIST ─────────────────────────────────────────────────────
    lines.append("<b>INDIA WHITELIST</b>")
    if i4:
        added = i4.get("added", [])
        if added:
            lines.append(f"+{len(added)} companies auto-added: {', '.join(added)}")
        else:
            lines.append("No new additions")
    else:
        lines.append("(not run)")
    lines.append("")

    # ─── NEEDS ATTENTION ─────────────────────────────────────────────────────
    attention = []

    # PM/FO target gaps
    pm_issues = pm.get("issues", [])
    fo_issues = fo.get("strategy", {}).get("issues", [])
    if "low_intake" in pm_issues:
        attention.append("PM intake low this week — check pipeline sources")
    if "high_rejection" in pm_issues:
        attention.append(f"PM rejection rate high ({pm.get('conv_rate_pct',0)}% conv) — check classifier or India filter")
    if "low_intake" in fo.get("issues", []):
        attention.append("FO intake low — check CoS/Founder's Office search queries")

    # Quality failures
    if i2 and i2.get("failed", 0) > 5:
        attention.append(f"Quality audit: {i2['failed']} failures — enrichment may be producing dirty records")

    # Reliability issues
    if reliability:
        for check_id, info in reliability.items():
            if info.get("result") in ("warn", "critical"):
                meta = info.get("meta", {})
                attention.append(f"[{check_id}] {info['result'].upper()}: {_format_meta_short(meta)}")

    if attention:
        lines.append("<b>NEEDS ATTENTION</b>")
        for item in attention:
            lines.append(f"⚠️ {item}")
    else:
        lines.append("<b>NEEDS ATTENTION</b>")
        lines.append("✅ Nothing flagged")
    lines.append("")

    # ─── AI ADVISOR ──────────────────────────────────────────────────────────
    if ai_result and not ai_result.get("error"):
        lines.append("<b>AI ADVISOR (Gemini 2.5)</b>")

        analysis = ai_result.get("analysis", "")
        if analysis:
            lines.append(analysis)
            lines.append("")

        # Code fixes (highest value — show these prominently)
        code_results = ai_result.get("code_changes_results", [])
        applied_code = [r for r in code_results if r.get("status") == "applied"]
        skipped_code = [r for r in code_results if r.get("status") in ("skipped", "rejected")]
        if applied_code:
            lines.append("🔧 Code fixes applied:")
            for r in applied_code:
                lines.append(f"  ✅ {r['description']}")
        if skipped_code:
            for r in skipped_code:
                lines.append(f"  ⚠️ Skipped ({r['file']}): {r.get('reason','')[:60]}")

        # Config changes
        applied = ai_result.get("applied", {})
        config_items = []
        if applied.get("add_queries"):
            config_items.append(f"+{len(applied['add_queries'])} queries: {', '.join(applied['add_queries'][:2])}")
        if applied.get("retire_queries"):
            config_items.append(f"-{len(applied['retire_queries'])} retired")
        if applied.get("add_companies"):
            config_items.append(f"+{len(applied['add_companies'])} companies: {', '.join(applied['add_companies'][:2])}")
        if config_items:
            lines.append("⚙️ Config: " + " | ".join(config_items))

        if not applied_code and not config_items:
            lines.append("✅ No changes needed today")

        # Human-direction items
        report_only = ai_result.get("report_only", [])
        if report_only:
            lines.append("\nNeeds your direction:")
            for item in report_only[:3]:
                lines.append(f"  → {item}")
        lines.append("")

    elif ai_result and ai_result.get("error"):
        lines.append(f"<b>AI ADVISOR</b>: skipped ({ai_result['error']}) — will retry tomorrow\n")

    lines.append("<b>OPEN THE BOARD</b>")
    lines.append(f"PM roles:  {BOARD_URL}/")
    lines.append(f"Founder's Office:  {BOARD_URL}/?domain=strategy")
    return "\n".join(lines)


def _format_meta_short(meta: dict) -> str:
    parts = []
    for k, v in (meta or {}).items():
        if k == "issues":
            parts.extend(str(i) for i in (v or []))
        elif v is not None:
            parts.append(f"{k}={v}")
    return ", ".join(parts[:4])


def send_digest(message: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat  = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        logger.warning("Telegram not configured — printing digest to stdout")
        print(message)
        return

    # Telegram has 4096 char limit — split if needed
    chunks = _split_message(message, 4000)
    for i, chunk in enumerate(chunks):
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": chunk, "parse_mode": "HTML"},
                timeout=15,
            )
            if resp.status_code != 200:
                logger.warning(f"Telegram send failed: {resp.status_code} {resp.text[:80]}")
        except Exception as e:
            logger.warning(f"Telegram send error: {e}")


def _split_message(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        # Split at last newline before limit
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks
