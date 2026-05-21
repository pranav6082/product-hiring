"""
LLM structured extraction for the job pipeline.

Replaces the per-source regex / keyword extractors (extract_company, extract_title,
classify, classify_india_hiring, extract_summary) with a single Gemini call over
the fetched job-description markdown. One call returns every board field, clean.

Why: the regex extractors produce ~78% dirty rows (live audit 2026-05-21) — company
names that are whole job-posting strings, titles with the company prefixed in,
trailing "job", etc. A model reading the actual JD does not make those mistakes.

Public API:
    extract_job_fields(markdown, existing) -> dict | None

`existing` is a dict of the current (possibly dirty) values, passed to the model
as a hint only — it is told to trust the JD text over the existing values.
Returns None when the model is unavailable or every model fails, so callers can
fall back to the legacy extractors without a regression.
"""

import os
import re
import json
import time
import logging
import requests

logger = logging.getLogger(__name__)

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite"]

# Second LLM provider — any OpenAI-compatible API (Groq, Cerebras, OpenRouter,
# Mistral, ...). Keeps extraction AI-native when Gemini is rate-limited or down,
# instead of dropping to regex. Set FALLBACK_LLM_API_KEY to enable; base URL and
# model default to Groq's free tier.
FALLBACK_KEY   = os.environ.get("FALLBACK_LLM_API_KEY", "")
FALLBACK_BASE  = os.environ.get("FALLBACK_LLM_BASE_URL", "https://api.groq.com/openai/v1")
FALLBACK_MODEL = os.environ.get("FALLBACK_LLM_MODEL", "llama-3.3-70b-versatile")

VALID_DOMAINS = {"pm", "strategy", "design", "data", "engineering", "other"}
VALID_INDIA = {"confirmed", "unknown", "rejected"}
VALID_MODE = {"remote", "hybrid", "onsite"}

_PROMPT = '''You are extracting clean, structured data for a Product-Manager job board.
Read the JOB DESCRIPTION below and return ONE JSON object. Trust the JD text itself —
the "current values" are scraped guesses and are often wrong.

Return EXACTLY this JSON shape and nothing else:
{
  "title":        "the role title only",
  "company":      "the hiring company's real name (or null)",
  "location":     "primary city, or Remote (or null)",
  "work_mode":    "remote | hybrid | onsite (or null)",
  "domain":       "pm | strategy | design | data | engineering | other",
  "is_pm_role":   true or false,
  "india_status": "confirmed | unknown | rejected",
  "summary":      "1-2 plain sentences on the role and product",
  "confidence":   0.0 to 1.0
}

RULES:

title — the job title and nothing else.
  - Remove any company-name prefix ("Acme - Senior PM" -> "Senior Product Manager").
  - Remove trailing "job"/"jobs", years-of-experience ("2-5 yrs"), qualification
    suffixes ("IIT/IIM"), location, and HTML entities.
  - Human casing ("Senior Product Manager", not "senior-product-manager").

company — the real, recognisable hiring company ONLY.
  - NEVER a job-posting string ("Job 387420 Product Manager At Payu Gurgaon" -> "PayU").
  - NEVER a country/region (India, USA, Remote, Global), a role word (Senior, Manager,
    Product Manager), a job board (iimjobs, instahyre, LinkedIn, wellfound), a URL,
    a person's name, or "Unknown".
  - If the JD genuinely does not name the company, return null.

domain — classify the role:
  - "pm"          = Product Manager family (PM, APM, Sr/Lead/Staff/Principal/Group PM,
                    Director/VP/Head of Product, CPO, Product Owner).
  - "strategy"    = Chief of Staff, Founder's Office, EIR, Founding team (non-PM).
  - "design"      = Product/UX/UI Designer.
  - "data"        = Data/Product Analyst, Data Scientist.
  - "engineering" = Software Engineer, Developer, Architect, SDE, etc.
  - "other"       = anything else (Marketing, Sales, Project Manager, etc.).

is_pm_role — true ONLY if domain is "pm" AND it is a genuine Product Manager role
  (not Product Marketing, not Product Design).

india_status — can a candidate based in India realistically be hired?
  - "confirmed" = onsite/hybrid in an Indian city, OR remote with explicit India
    hiring language or a well-known India office.
  - "rejected"  = onsite in a non-India city, OR US/other-only remote with no India
    signal, OR the JD says the position is closed/filled.
  - "unknown"   = cannot tell.

summary — 1-2 sentences, plain. What the role is + what the team/product does.
  NEVER apply-form boilerplate ("Attach resume", "LinkedIn profile", "Loading...").

confidence — your confidence the company + title are correct (0.0 to 1.0).

CURRENT VALUES (scraped guesses — may be wrong):
  title:    <<RAW_TITLE>>
  company:  <<RAW_COMPANY>>
  location: <<RAW_LOCATION>>

JOB DESCRIPTION:
<<MARKDOWN>>
'''


def _call_gemini(prompt: str) -> dict | None:
    """POST to the Gemini REST API; return the parsed JSON object, or None."""
    if not GEMINI_KEY:
        logger.warning("GEMINI_API_KEY not set — llm_extract disabled")
        return None

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.0,          # deterministic extraction
            "maxOutputTokens": 2048,
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
                    timeout=45,
                )
                if resp.status_code == 429:
                    time.sleep(15 * (attempt + 1))
                    continue
                if resp.status_code == 404:
                    break  # model name gone — try the next
                if resp.status_code == 503:
                    time.sleep(8)
                    continue
                resp.raise_for_status()

                text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
                text = re.sub(r"^```(?:json)?\s*", "", text.strip())
                text = re.sub(r"\s*```$", "", text.strip())
                # isolate the outermost {...} in case of preamble
                start = text.find("{")
                if start != -1:
                    depth = 0
                    for i, ch in enumerate(text[start:], start):
                        if ch == "{":
                            depth += 1
                        elif ch == "}":
                            depth -= 1
                            if depth == 0:
                                text = text[start:i + 1]
                                break
                return json.loads(text)

            except json.JSONDecodeError as e:
                logger.warning(f"{model}: invalid JSON ({e}) — trying next model")
                break
            except Exception as e:
                logger.warning(f"{model} attempt {attempt + 1} failed: {e}")
                time.sleep(4)

    logger.warning("llm_extract: all Gemini models failed")
    return None


def _call_fallback_llm(prompt: str) -> dict | None:
    """
    Second LLM provider via an OpenAI-compatible chat API (Groq by default).
    Called only when Gemini returned nothing — keeps extraction AI-native
    instead of dropping to the regex fallback. Disabled if no key is set.
    """
    if not FALLBACK_KEY:
        return None
    try:
        resp = requests.post(
            f"{FALLBACK_BASE.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {FALLBACK_KEY}"},
            json={
                "model": FALLBACK_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
            },
            timeout=45,
        )
        if resp.status_code != 200:
            logger.warning(f"fallback LLM ({FALLBACK_MODEL}): HTTP {resp.status_code}")
            return None
        text = resp.json()["choices"][0]["message"]["content"]
        text = re.sub(r"^```(?:json)?\s*", "", text.strip())
        text = re.sub(r"\s*```$", "", text.strip())
        start = text.find("{")
        if start != -1:
            depth = 0
            for i, ch in enumerate(text[start:], start):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        text = text[start:i + 1]
                        break
        logger.info(f"fallback LLM ({FALLBACK_MODEL}) succeeded")
        return json.loads(text)
    except Exception as e:
        logger.warning(f"fallback LLM failed: {e}")
        return None


def _norm(result: dict) -> dict | None:
    """Validate + normalise the model's JSON into the fields the pipeline expects."""
    if not isinstance(result, dict):
        return None

    def _str(v):
        v = (v or "").strip() if isinstance(v, str) else ""
        return v or None

    title = _str(result.get("title"))
    company = _str(result.get("company"))
    if company and company.lower() in ("null", "none", "unknown", "n/a"):
        company = None

    domain = (result.get("domain") or "").strip().lower()
    if domain not in VALID_DOMAINS:
        domain = None

    india = (result.get("india_status") or "").strip().lower()
    if india not in VALID_INDIA:
        india = "unknown"

    mode = (result.get("work_mode") or "").strip().lower()
    if mode not in VALID_MODE:
        mode = None

    try:
        confidence = float(result.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    if not title:
        return None  # a result with no title is unusable

    return {
        "title": title,
        "company": company,
        "location": _str(result.get("location")),
        "work_mode": mode,
        "domain": domain,
        "is_pm_role": bool(result.get("is_pm_role")),
        "india_status": india,
        "summary": _str(result.get("summary")),
        "confidence": confidence,
    }


def extract_job_fields(markdown: str, existing: dict | None = None) -> dict | None:
    """
    Extract clean structured fields from a job-description markdown blob.

    Args:
        markdown: the fetched JD text (see enrich.fetch_page).
        existing: optional dict with raw_title / raw_company / raw_location hints.

    Returns a dict with keys title, company, location, work_mode, domain,
    is_pm_role, india_status, summary, confidence — or None if extraction failed
    (caller should fall back to the legacy regex extractors).
    """
    if not markdown or len(markdown) < 120:
        return None
    existing = existing or {}

    prompt = (
        _PROMPT
        .replace("<<RAW_TITLE>>", str(existing.get("raw_title") or "(none)"))
        .replace("<<RAW_COMPANY>>", str(existing.get("raw_company") or "(none)"))
        .replace("<<RAW_LOCATION>>", str(existing.get("raw_location") or "(none)"))
        .replace("<<MARKDOWN>>", markdown[:12000])
    )
    result = _call_gemini(prompt)
    if result is None:
        result = _call_fallback_llm(prompt)   # second provider, AI-native fallback
    if result is None:
        return None
    return _norm(result)
