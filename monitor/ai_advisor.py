"""
AI Advisor — Gemini 2.5 Flash, code-aware.

Runs daily after I1-I6 checks. Receives:
  - Full metrics snapshot (what the system produced)
  - Actual source files (pipeline.py, enrich.py) so Gemini can see WHY

Gemini diagnoses the root cause and returns EITHER:
  - Config changes (add queries, add companies) — applied immediately
  - Code changes (file edits as before/after blocks) — syntax-checked then applied
  - Report-only items — appear in digest for Pranav to direct

Safeguards on code changes:
  1. Python syntax check (ast.parse) before applying
  2. Only touches pipeline.py and enrich.py (the two data pipeline files)
  3. Diff-size cap — any single change over MAX_CHANGE_LINES is rejected
  4. Each change committed separately with a clear message
  5. Feedback rail — the next run checks whether the last change regressed the
     quality audit; if it did, the change is auto-reverted instead of built on
  6. Revert path — `python ai_advisor.py --revert-last` (or the advisor-revert
     workflow) undoes the last change on demand
"""

import os
import re
import ast
import json
import time
import logging
import requests
import subprocess
import tempfile

logger = logging.getLogger(__name__)

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")

GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]

SCRAPER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scraper"))
SPEC_PATH   = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "SPEC.md"))

# Only allow Gemini to edit these files (safety boundary)
EDITABLE_FILES = {
    "pipeline.py": os.path.join(SCRAPER_DIR, "pipeline.py"),
    "enrich.py":   os.path.join(SCRAPER_DIR, "enrich.py"),
}

# ── Rail on the self-improvement loop ────────────────────────────────────────
REPO_ROOT        = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# State file, committed to the repo so it survives across (stateless) CI runs.
STATE_PATH       = os.path.join(os.path.dirname(__file__), "advisor_state.json")
MAX_CHANGE_LINES = 40   # reject any single code change larger than this (blast-radius cap)
REGRESSION_DROP  = 3    # quality-audit pass-count drop (out of 20) that triggers an auto-revert


# ─── Source file loader ───────────────────────────────────────────────────────

def _load_source(filename: str, max_chars: int = 15000) -> str:
    """Load source file, trimmed to most relevant sections."""
    path = EDITABLE_FILES.get(filename, "")
    if not path or not os.path.exists(path):
        return f"# {filename} not found"
    with open(path) as f:
        content = f.read()
    if len(content) <= max_chars:
        return content
    # For large files, keep first 8K (config/constants) + last 7K (main logic)
    return content[:8000] + "\n\n# ... [middle truncated for brevity] ...\n\n" + content[-7000:]


def _load_spec() -> str:
    try:
        with open(SPEC_PATH) as f:
            return f.read()[:3000]
    except FileNotFoundError:
        return "Targets: 50 PM jobs, 30 FO/CoS jobs. India-eligible only."


# ─── Symptom detection — decides which files to include ──────────────────────

def _detect_symptoms(metrics: dict) -> list[str]:
    """
    Returns list of symptom strings that drive which files get included
    and which questions get asked.
    """
    symptoms = []
    i3 = metrics.get("i3", {})
    pm = i3.get("pm", {})
    fo = i3.get("strategy", {})
    i6 = metrics.get("i6", {})
    i2 = metrics.get("i2", {})
    i5 = metrics.get("i5", {})

    pm_conv  = pm.get("conv_rate_pct", 100)
    fo_conv  = fo.get("conv_rate_pct", 100)
    pm_curr  = pm.get("current", 0)
    fo_curr  = fo.get("current", 0)
    pm_added = pm.get("added_7d", 0)
    fo_added = fo.get("added_7d", 0)

    if pm_conv < 15 and pm_added > 5:
        symptoms.append(f"CRITICAL: PM conversion rate is {pm_conv}% — only {pm.get('approved_7d',0)} of {pm_added} PM jobs ingested this week got approved. Something in the enrichment or classification logic is blocking approval.")

    if fo_conv < 15 and fo_added > 5:
        symptoms.append(f"CRITICAL: FO/CoS conversion rate is {fo_conv}% — only {fo.get('approved_7d',0)} of {fo_added} FO jobs got approved this week.")

    if pm_curr < 20:
        symptoms.append(f"PM board is critically low ({pm_curr}/50). Need more approved jobs urgently.")

    if fo_curr < 20:
        symptoms.append(f"FO/CoS board is low ({fo_curr}/30). Need more approved Founder's Office / Chief of Staff jobs.")

    if i6.get("total_stuck", 0) > 20:
        symptoms.append(f"ENRICHMENT STUCK: {i6['total_stuck']} jobs pending >48h without enrichment. Enrichment loop may have a bug.")

    if i2.get("failed", 0) > 5:
        symptoms.append(f"QUALITY: {i2['failed']}/20 sampled approved jobs failed quality criteria — dirty titles or bad company names reaching the board.")

    for src in (i5.get("sources") or []):
        if src.get("flag") == "bad" and src.get("total", 0) >= 10:
            symptoms.append(f"SOURCE: {src['source']} approval rate is {src['approval_pct']}% over 7 days ({src['approved']}/{src['total']}) — may need query tightening or disabling.")

    if not symptoms:
        symptoms.append("System appears healthy. Board counts may be below target but trending normally.")

    return symptoms


# ─── Prompt builder ───────────────────────────────────────────────────────────

def _build_prompt(metrics: dict, symptoms: list[str]) -> str:
    spec    = _load_spec()

    # Only include source code when there are real symptoms to diagnose.
    # Healthy-system runs get a much smaller prompt (faster, cheaper, avoids
    # output-token pressure from large inputs).
    healthy = (len(symptoms) == 1 and "healthy" in symptoms[0].lower())
    pipe_py   = _load_source("pipeline.py")   if not healthy else "# not included — system healthy"
    enrich_py = _load_source("enrich.py")     if not healthy else "# not included — system healthy"

    i3 = metrics.get("i3", {})
    pm = i3.get("pm", {})
    fo = i3.get("strategy", {})
    i1 = metrics.get("i1", {})
    i2 = metrics.get("i2", {})
    i5 = metrics.get("i5", {})
    i6 = metrics.get("i6", {})

    source_lines = "\n".join(
        f"  {s['source']}: {s['approval_pct']}% ({s['approved']}/{s['total']})"
        for s in (i5.get("sources") or [])
    ) or "  (no data)"

    failure_lines = "\n".join(
        f"  - \"{f['title']}\" @ {f['company']}: {'; '.join(f['reasons'])}"
        for f in (i2.get("failures") or [])
    ) or "  none"

    symptoms_text = "\n".join(f"  ⚠ {s}" for s in symptoms)

    prompt = f"""You are an autonomous agent maintaining a product hiring intelligence pipeline.
Your job: diagnose problems and produce concrete fixes.

## SPEC (objective function)
{spec}

## TODAY'S METRICS

Board: PM {pm.get('current','?')}/50, FO {fo.get('current','?')}/30
PM 7d: {pm.get('added_7d',0)} ingested → {pm.get('approved_7d',0)} approved ({pm.get('conv_rate_pct',0)}% conv), {pm.get('rejected_7d',0)} rejected
FO 7d: {fo.get('added_7d',0)} ingested → {fo.get('approved_7d',0)} approved ({fo.get('conv_rate_pct',0) if fo else 0}% conv)
Dead links killed today: {i1.get('rejected',0)}
Quality audit: {i2.get('passed',0)}/20 passed
Quality failures:
{failure_lines}
Source performance (7d):
{source_lines}
Stuck pending >48h: {i6.get('total_stuck',0)} (auto-rejected unscrapeable: {i6.get('auto_rejected',0)})

## DETECTED SYMPTOMS
{symptoms_text}

## SOURCE CODE

### pipeline.py (ingestion + classification)
```python
{pipe_py}
```

### enrich.py (enrichment + approval logic)
```python
{enrich_py}
```

## YOUR TASK

Read the symptoms and source code carefully. Diagnose the root cause of each symptom.
Produce fixes. Be specific — point to exact functions and line logic.

Respond with ONLY a JSON object (no markdown outside the JSON):

{{
  "analysis": "Root cause diagnosis in 3-5 sentences. Be specific about which function/logic is causing each symptom.",

  "code_changes": [
    {{
      "file": "pipeline.py",
      "description": "what this change does and why",
      "old_code": "EXACT string to find in the file (must be unique, include enough context)",
      "new_code": "replacement string"
    }}
  ],

  "add_queries": [
    {{"query": "search string", "domain": "pm|strategy", "reason": "why this helps"}}
  ],

  "retire_queries": [
    {{"query": "exact string", "reason": "why"}}
  ],

  "add_companies": [
    {{"company": "lowercase name", "reason": "why it qualifies"}}
  ],

  "report_only": [
    "anything requiring human judgment (new data source, schema change, etc.)"
  ]
}}

Rules:
- code_changes: max 3. Only edit pipeline.py or enrich.py. old_code must be a unique substring that exists in the file. Keep changes minimal and focused.
- add_queries: max 3. Only if intake is genuinely low.
- add_companies: max 5. Indian startups or global cos with India product offices only. No job titles.
- If the symptom is a code bug → fix it in code_changes. Don't add more queries into a broken pipe.
- If system is healthy → empty arrays are correct.
"""
    return prompt


# ─── Gemini call ──────────────────────────────────────────────────────────────

def _call_gemini(prompt: str) -> dict | None:
    if not GEMINI_KEY:
        logger.warning("GEMINI_API_KEY not set")
        return None

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,   # low temp — we want deterministic fixes
            "maxOutputTokens": 8192,
            # No responseMimeType — that mode truncates responses mid-JSON on large outputs.
            # We parse JSON from free-form text instead.
            # Cap thinking tokens so output budget isn't consumed by internal reasoning.
            # 0 = disable thinking entirely (fastest, most output tokens for the response).
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }

    for model in GEMINI_MODELS:
        for attempt in range(2):
            try:
                resp = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    params={"key": GEMINI_KEY},
                    json=payload,
                    timeout=60,
                )
                if resp.status_code == 429:
                    logger.warning(f"{model} rate-limited (attempt {attempt+1}), waiting...")
                    time.sleep(20 * (attempt + 1))
                    continue
                if resp.status_code == 404:
                    logger.warning(f"{model} not found, trying next")
                    break
                if resp.status_code == 503:
                    logger.warning(f"{model} unavailable (attempt {attempt+1}), waiting...")
                    time.sleep(10)
                    continue
                resp.raise_for_status()

                rjson = resp.json()
                candidate = rjson["candidates"][0]
                finish_reason = candidate.get("finishReason", "?")
                text = candidate["content"]["parts"][0]["text"]
                logger.info(f"{model} finishReason={finish_reason}, raw_len={len(text)}")
                # Strip markdown fences
                text = re.sub(r'^```(?:json)?\s*', '', text.strip())
                text = re.sub(r'\s*```$', '', text.strip())
                # Extract outermost JSON object (handles preamble text)
                m = re.search(r'\{', text)
                if m:
                    # Find matching closing brace
                    start = m.start()
                    depth = 0
                    end = start
                    for i, ch in enumerate(text[start:], start):
                        if ch == '{': depth += 1
                        elif ch == '}':
                            depth -= 1
                            if depth == 0:
                                end = i + 1
                                break
                    text = text[start:end]

                result = json.loads(text)
                logger.info(f"Gemini succeeded: {model}")
                return result

            except json.JSONDecodeError as e:
                raw = text if 'text' in dir() else '?'
                logger.warning(f"{model} invalid JSON: {e} — len={len(raw)}, text[:300]: {raw[:300]}")
                # Don't retry same model on JSON error — try next model
                break
            except Exception as e:
                logger.warning(f"{model} attempt {attempt+1} error: {e}")
                time.sleep(5)

    logger.warning("All Gemini models failed")
    return None


# ─── Apply code changes ───────────────────────────────────────────────────────

def _syntax_ok(code: str) -> bool:
    """Check Python syntax without executing."""
    try:
        ast.parse(code)
        return True
    except SyntaxError as e:
        logger.warning(f"Syntax error: {e}")
        return False


# ─── Self-improvement rail: feedback signal, diff cap, revert path ───────────

def _git(*args) -> subprocess.CompletedProcess:
    """Run a git command in the repo with the ai-advisor identity."""
    return subprocess.run(
        ["git", "-C", REPO_ROOT, *args],
        capture_output=True, text=True,
        env={**os.environ,
             "GIT_AUTHOR_NAME": "ai-advisor", "GIT_AUTHOR_EMAIL": "ai@pipeline",
             "GIT_COMMITTER_NAME": "ai-advisor", "GIT_COMMITTER_EMAIL": "ai@pipeline"},
    )


def _load_state() -> dict:
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict, commit_msg: str) -> None:
    """Persist advisor state and commit it so the next CI run can read it."""
    try:
        with open(STATE_PATH, "w") as f:
            json.dump(state, f, indent=2)
        if _git("add", STATE_PATH).returncode == 0 and \
           _git("commit", "-m", commit_msg).returncode == 0:
            _git("push", "origin", "HEAD:main")
    except Exception as e:
        logger.warning(f"Could not persist advisor state: {e}")


def _quality_score(metrics: dict) -> int:
    """Objective signal: how many of the 20 sampled jobs passed the quality audit."""
    return int(metrics.get("i2", {}).get("passed", 0))


def _change_too_big(old_code: str, new_code: str) -> bool:
    """Blast-radius cap — reject a change whose old or new block exceeds the line cap."""
    return (len(old_code.splitlines()) > MAX_CHANGE_LINES or
            len(new_code.splitlines()) > MAX_CHANGE_LINES)


def _revert_commits(shas: list) -> bool:
    """git revert the given advisor commits (newest-first) and push. True on success."""
    if not shas:
        return False
    for sha in shas:
        if _git("revert", "--no-edit", sha).returncode != 0:
            logger.warning(f"revert {sha[:8]} failed — aborting")
            _git("revert", "--abort")
            return False
    return _git("push", "origin", "HEAD:main").returncode == 0


def _apply_code_changes(changes: list, applied: dict) -> list:
    """
    Apply Gemini's code changes to source files.
    Returns list of dicts describing what was applied or skipped.
    """
    results = []
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    for change in (changes or [])[:3]:
        filename    = change.get("file", "")
        description = change.get("description", "")
        old_code    = change.get("old_code", "")
        new_code    = change.get("new_code", "")

        if filename not in EDITABLE_FILES:
            results.append({"file": filename, "status": "skipped", "reason": "file not in editable set"})
            continue

        if not old_code or not new_code:
            results.append({"file": filename, "status": "skipped", "reason": "empty old_code or new_code"})
            continue

        if _change_too_big(old_code, new_code):
            results.append({"file": filename, "status": "rejected",
                            "reason": f"change exceeds {MAX_CHANGE_LINES}-line cap — not applied"})
            continue

        filepath = EDITABLE_FILES[filename]
        try:
            with open(filepath) as f:
                content = f.read()
        except Exception as e:
            results.append({"file": filename, "status": "error", "reason": str(e)})
            continue

        if old_code not in content:
            results.append({"file": filename, "status": "skipped",
                            "reason": f"old_code not found in {filename} (may already be fixed)"})
            continue

        new_content = content.replace(old_code, new_code, 1)

        # Syntax check the modified file
        if not _syntax_ok(new_content):
            results.append({"file": filename, "status": "rejected",
                            "reason": "syntax check failed — not applied"})
            continue

        # Write the change
        with open(filepath, "w") as f:
            f.write(new_content)

        # Commit
        try:
            msg = f"ai-advisor: {description[:72]} [gemini]"
            subprocess.run(["git", "-C", repo_root, "add", filepath], check=True)
            subprocess.run(["git", "-C", repo_root, "commit", "-m", msg], check=True,
                           env={**os.environ,
                                "GIT_AUTHOR_NAME": "ai-advisor",
                                "GIT_AUTHOR_EMAIL": "ai@pipeline",
                                "GIT_COMMITTER_NAME": "ai-advisor",
                                "GIT_COMMITTER_EMAIL": "ai@pipeline"})
            subprocess.run(["git", "-C", repo_root, "push", "origin", "HEAD:main"], check=True)
            logger.info(f"Applied + committed: {description}")
            results.append({"file": filename, "status": "applied", "description": description})
            applied.setdefault("code_changes", []).append(description)
        except subprocess.CalledProcessError as e:
            logger.warning(f"Git error: {e}")
            results.append({"file": filename, "status": "error", "reason": f"git error: {e}"})

    return results


# ─── Apply config changes (queries, companies) ────────────────────────────────

def _apply_config_changes(actions: dict, applied: dict):
    """Apply query and company list changes to pipeline.py."""
    pipeline_path = EDITABLE_FILES["pipeline.py"]
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    try:
        with open(pipeline_path) as f:
            content = f.read()
    except FileNotFoundError:
        return

    original = content

    # add_queries
    for item in (actions.get("add_queries") or [])[:3]:
        query  = item.get("query", "").strip().lower()
        domain = item.get("domain", "pm")
        if not query or len(query) < 4 or f'"query": "{query}"' in content:
            continue
        new_entry = f'    {{"query": "{query}", "domain": "{domain}"}},  # ai-added\n'
        content = re.sub(
            r'(PARALLEL_SEARCHES\s*=\s*\[.*?)(])',
            lambda m: m.group(1) + new_entry + m.group(2),
            content, count=1, flags=re.DOTALL,
        )
        applied.setdefault("add_queries", []).append(query)

    # retire_queries
    for item in (actions.get("retire_queries") or [])[:3]:
        query = item.get("query", "").strip().lower()
        if not query:
            continue
        lines = content.splitlines(keepends=True)
        new_lines, removed = [], False
        for line in lines:
            if f'"query": "{query}"' in line and not removed:
                new_lines.append(f"    # retired by ai-advisor: {line.strip()}\n")
                removed = True
            else:
                new_lines.append(line)
        if removed:
            content = "".join(new_lines)
            applied.setdefault("retire_queries", []).append(query)

    # add_companies
    JOB_TITLE_WORDS = {"product","manager","engineer","designer","analyst","developer",
                       "lead","director","vp","head","chief","principal","senior","junior",
                       "saas","b2b","b2c","remote","hiring","jobs","role","position"}
    for item in (actions.get("add_companies") or [])[:5]:
        co = item.get("company", "").strip().lower()
        if not co or len(co) < 2 or len(co) > 60:
            continue
        if re.search(r'[<>&"\'\\]', co) or f'"{co}"' in content:
            continue
        co_words = set(co.split())
        if len(co_words & JOB_TITLE_WORDS) >= 2 or len(co_words) > 5:
            continue
        new_entry = f'\n    "{co}",  # ai-added'
        content = re.sub(
            r'(INDIA_OFFICE_COMPANIES\s*=\s*\{[^}]+)',
            lambda m: m.group(1) + new_entry,
            content, count=1, flags=re.DOTALL,
        )
        applied.setdefault("add_companies", []).append(co)

    if content != original and _syntax_ok(content):
        with open(pipeline_path, "w") as f:
            f.write(content)
        parts = []
        if applied.get("add_queries"):    parts.append(f"+{len(applied['add_queries'])} queries")
        if applied.get("retire_queries"): parts.append(f"-{len(applied['retire_queries'])} retired")
        if applied.get("add_companies"):  parts.append(f"+{len(applied['add_companies'])} companies")
        if parts:
            try:
                msg = f"ai-advisor: config — {', '.join(parts)} [gemini]"
                subprocess.run(["git", "-C", repo_root, "add", pipeline_path], check=True)
                subprocess.run(["git", "-C", repo_root, "commit", "-m", msg], check=True,
                               env={**os.environ, "GIT_AUTHOR_NAME": "ai-advisor",
                                    "GIT_AUTHOR_EMAIL": "ai@pipeline",
                                    "GIT_COMMITTER_NAME": "ai-advisor",
                                    "GIT_COMMITTER_EMAIL": "ai@pipeline"})
                subprocess.run(["git", "-C", repo_root, "push", "origin", "HEAD:main"], check=True)
            except subprocess.CalledProcessError as e:
                logger.warning(f"Config commit failed: {e}")


# ─── Main entry point ─────────────────────────────────────────────────────────

def run_ai_advisor(metrics: dict) -> dict:
    if not GEMINI_KEY:
        return {"error": "GEMINI_API_KEY not configured", "analysis": "",
                "applied": {}, "code_changes_results": [], "report_only": []}

    state         = _load_state()
    last_change   = state.get("last_change")
    current_score = _quality_score(metrics)

    # ── RAIL 1: feedback check — did the previous advisor change help or hurt? ──
    if last_change and last_change.get("commits"):
        prev_score = last_change.get("metric", {}).get("quality_passed")
        if prev_score is not None and current_score <= prev_score - REGRESSION_DROP:
            reverted = _revert_commits(last_change["commits"])
            note = (f"Previous advisor change regressed the quality audit "
                    f"({prev_score}/20 -> {current_score}/20). "
                    + ("Auto-reverted." if reverted else
                       "AUTO-REVERT FAILED — run the advisor-revert workflow."))
            logger.warning(note)
            state["last_change"] = None
            _save_state(state, "ai-advisor: auto-revert regressing change [gemini]")
            return {"analysis": note, "applied": {}, "code_changes_results": [],
                    "report_only": [], "symptoms": [], "reverted": last_change["commits"]}

    symptoms = _detect_symptoms(metrics)
    logger.info(f"Symptoms detected: {symptoms}")

    prompt = _build_prompt(metrics, symptoms)
    logger.info(f"Prompt size: {len(prompt)} chars, calling Gemini...")

    actions = _call_gemini(prompt)
    if not actions:
        return {"error": "Gemini call failed — will retry tomorrow",
                "analysis": "", "applied": {}, "code_changes_results": [], "report_only": []}

    logger.info(f"Gemini analysis: {actions.get('analysis','')[:200]}")

    start_sha = _git("rev-parse", "HEAD").stdout.strip()
    applied = {}

    # Apply code changes first (highest value)
    code_results = _apply_code_changes(actions.get("code_changes", []), applied)
    logger.info(f"Code changes: {code_results}")

    # Apply config changes
    _apply_config_changes(actions, applied)

    # ── RAIL 2: record this run's commits so the next run can evaluate them ──
    new_sha = _git("rev-parse", "HEAD").stdout.strip()
    new_commits = []
    if start_sha and new_sha and start_sha != new_sha:
        new_commits = [s for s in _git("rev-list", f"{start_sha}..{new_sha}").stdout.split() if s]

    if new_commits:
        state["last_change"] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "commits": new_commits,
            "metric": {
                "quality_passed": current_score,
                "pm_current": metrics.get("i3", {}).get("pm", {}).get("current", 0),
                "fo_current": metrics.get("i3", {}).get("strategy", {}).get("current", 0),
            },
            "descriptions": applied.get("code_changes", []) + [
                f"config: {k}" for k in ("add_queries", "retire_queries", "add_companies")
                if applied.get(k)
            ],
        }
        _save_state(state, "ai-advisor: record change for next-run evaluation [gemini]")
    elif last_change:
        # the previous change was evaluated this run and did not regress — stop tracking it
        state["last_change"] = None
        _save_state(state, "ai-advisor: clear evaluated change state [gemini]")

    return {
        "analysis":           actions.get("analysis", ""),
        "applied":            applied,
        "code_changes_results": code_results,
        "report_only":        actions.get("report_only", []),
        "symptoms":           symptoms,
    }


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if "--revert-last" in sys.argv:
        st = _load_state()
        lc = st.get("last_change")
        if lc and lc.get("commits"):
            ok = _revert_commits(lc["commits"])
            print("Reverted last advisor change." if ok else "Revert failed — check git state.")
            st["last_change"] = None
            _save_state(st, "ai-advisor: manual revert [gemini]")
        else:
            print("No advisor change on record to revert.")
    else:
        print("ai_advisor runs via monitor.py. Use --revert-last to undo the last change.")
