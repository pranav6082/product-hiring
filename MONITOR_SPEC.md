# MONITOR_SPEC.md

How the monitor actually works — as built, 2026-05.

> Earlier versions of this file described a different design (a reliability
> monitor that invoked Claude Code to auto-fix issues, with a 3-attempt
> anti-loop state machine and `state.json` on an Oracle VM). **That was never
> built.** This file describes the system that exists in `monitor/`.

---

## Where it runs

Everything runs on **GitHub Actions cron** — there is no Oracle VM. State that
must survive between runs lives in two places:

- the `monitor_state` table in Neon (alert/fix bookkeeping),
- `monitor/advisor_state.json`, committed to the repo (self-improvement rail).

Entry point: `monitor.py --loop {reliability|improvement|both}`.

## The three jobs

| Job | Workflow | Cadence | Entry |
|-----|----------|---------|-------|
| Reliability loop | `monitor-reliability.yml` | every 6h | `monitor.py --loop reliability` |
| Improvement loop | `monitor-improvement.yml` | daily | `monitor.py --loop improvement` |
| Dead-link sweep | `deadlinks.yml` | every 8h | `deadlinks.py` |

---

## Loop 1 — Reliability (`reliability.py`)

Six health checks. Each returns `ok` / `warn` / `critical`; some have a
rule-based fix; failures raise a Telegram alert.

| Check | Watches | Auto-fix |
|-------|---------|----------|
| R1 | Pipeline ingestion — are new jobs arriving? | re-trigger the pipeline workflow |
| R2 | Enrichment activity — is `enrich.py` running? | re-trigger the enrich workflow |
| R3 | Pending backlog — too many jobs stuck in review? | re-trigger enrichment |
| R4 | Rejection rate — is it abnormally high/low? | alert only |
| R5 | Board availability — is the Vercel board reachable? | alert only |
| R6 | GitHub Actions health — are workflows passing? | re-trigger a failed workflow |

## Loop 2 — Improvement (`improvement.py` + `ai_advisor.py`)

Runs daily. Six checks (I1–I6), then the AI advisor, then a Telegram digest.

| Check | Does |
|-------|------|
| I1 | Dead-link sweep (`deadlinks.py`) — see below |
| I2 | Quality audit — scores a random 20-job sample against the SPEC criteria |
| I3 | Target progress — PM and Founder's Office board counts vs the 50 / 30 goals |
| I4 | India-whitelist gaps — auto-adds confirmed India companies to `pipeline.py` |
| I5 | Source performance — approval rate per source over 7 days |
| I6 | Stuck-pending jobs — flags / auto-rejects jobs unscrapeable after 72h |

### AI advisor (`ai_advisor.py`)

Gemini receives the I1–I6 metrics plus the source of `pipeline.py` / `enrich.py`,
diagnoses root causes, and applies fixes (code edits, query/company list
changes), committing each to `main`. It is **railed** (see the docstring in
`ai_advisor.py`):

- **Diff cap** — any single code change over 40 lines is rejected.
- **Feedback check** — each run records its commits + the I2 quality score in
  `advisor_state.json`; the next run compares. If the score dropped ≥ 3/20 the
  change is auto-reverted instead of being built upon.
- **Revert path** — `python ai_advisor.py --revert-last`, or the
  `advisor-revert.yml` workflow, undoes the last change on demand.

## Dead-link sweep (`deadlinks.py`)

Concurrent GET against every live (approved + pending) PM / Founder's-Office
job. A job is auto-rejected when its URL is:

- HTTP 404 / 410 or any 4xx/5xx,
- redirected **off** the job platform,
- redirected to the company's **listing / index page** (the posting was
  removed — e.g. Greenhouse `/{company}/jobs/{id}` → `/{company}`),
- or the page body contains a closed-job phrase ("no longer accepting
  applications", etc.).

Runs every 8h via `deadlinks.yml`, and again inside the daily improvement loop.
`deadlinks.yml` also accepts a manual **dry-run** input that reports findings
without writing.

## Alerts & digest

- **Telegram alerts** — fired by the reliability loop on `warn` / `critical`.
- **Telegram daily digest** (`digest.py`) — the improvement loop's summary:
  board progress, quality-audit result, dead links killed, source performance,
  and what the AI advisor changed.
