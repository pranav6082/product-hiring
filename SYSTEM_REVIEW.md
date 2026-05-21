# System Review — Product Hiring Project

**Date:** 2026-05-21
**Reviewer:** Claude Code (3 parallel layer reviews + verification pass + live data audit)
**Method:** Full read of `scraper/`, `monitor/`, `board/`, `db/`, both `.github/workflows/` trees, and all spec docs. Cross-checked highest-stakes findings against source. Live audit: extracted and scored the 294 rows the production board actually serves (see Appendix A).
**Coverage gap — now closed.** An earlier draft of this review could not reach the live board or Neon DB, so data-quality findings were inferred from code + git history. That gap is now filled. The board embeds every row it serves in its page payload, so the live dataset was recovered without DB credentials and audited against the SPEC's five quality criteria — see **Appendix A**. The audit confirms the inference.

---

## 1. The Objective

### As it stands today

- **v0.1 — Product Manager roles only.** 50 high-quality, India-eligible PM listings on the board. Quality is the *only* metric; volume is not.
- **v0.4 — Founder's Office / Chief of Staff** (specced). 30 listings.
- **The quality bar (5 criteria):** real PM title · clean role name · real + clean company name · India-eligible · live/active link.
- **Operating constraints (CLAUDE.md):** zero-cost (free tiers only, genuinely production-viable) · CLI/AI-agent operable · no local-only infra · single operator (Pranav + Claude, no second person).

### The new objective (added by this review, per owner instruction)

> **Objective 6 — True AI-native, production-ready.**
> The system must use frontier LLM capability as the *default* tool for any task that involves judgement over unstructured text — classification, extraction, eligibility, quality grading, summarisation. Hand-rolled keyword/regex heuristics are a code smell to be **replaced, not extended**.
> Every autonomous loop must: (a) use a model tier matched to the stakes — a reasoning-capable frontier model for anything that writes to production; (b) have a measurable feedback signal that says whether its last action helped or hurt; (c) have a revert path.
> "Production-ready" means: no silent failures, no fake-data fallbacks that mask outages, observable change history, and genuinely inside the zero-cost envelope.

Rationale: model capability has moved substantially in the last two months. A system designed around brittle heuristics in early 2026 is leaving its single biggest leverage unused — especially for a solo operator whose only "team" is the model.

### A framing note

`README.md` calls this a 5%-clarity exploration sandbox ("NOT a business plan"). `CLAUDE.md` calls it "a production-grade, single-person company." The system as built — Neon, Vercel, 7 cron workflows, a self-modifying pipeline — is unambiguously the second. **The exploration framing is stale.** This review treats it as a production system, which is the higher and correct bar.

---

## 2. Architecture (as-built)

```
SOURCES                    INGEST                  STORE            ENRICH                 SERVE
Greenhouse/Lever  ─┐
JobSpy / Indeed    ─┤
HN Who-is-Hiring   ─┼─►  scraper/pipeline.py  ─►  Neon Postgres  ─►  scraper/enrich.py  ─►  board/ (Next.js
Naukri API         ─┤    (classify, extract,      jobs + signals     (fetch JD, re-       on Vercel)
Parallel websearch ─┘     India-eligibility)       + sources/people    extract, dead-link)  /review  /status

SELF-RUNNING LOOPS
  monitor/reliability.py   — every 2h — 6 health checks, rule-based workflow re-triggers, Telegram pulse
  monitor/improvement.py   — daily   — dead-link sweep + quality checks + AI advisor + Telegram digest
  monitor/ai_advisor.py    — daily   — Gemini reads metrics + truncated source, edits pipeline.py, pushes to main
```

**Deliberately deferred (correct call):** the LinkedIn-feed "who-posted-this / warm-intro" layer. It was always intended as an *intelligence layer on top of* the board, not a replacement for it. It needs authenticated browser infra (Oracle VM + Steel) and carries real account-ban risk. Deferring it and shipping the board first is sound sequencing — not a drift from the thesis.

**The genuine surprise that worked:** the self-improvement loop. The owner did not expect a minor self-tuning loop to be valuable; it is. That instinct is right — but the loop currently runs without a safety rail (Finding C2).

---

## 3. The Solution & Data Quality

The board exists, the pipeline runs, jobs flow. But the system's quality control is **a large pile of hand-rolled keyword lists and regex** — and the git history is the clearest evidence that it is not converging:

- 12+ consecutive `ai-advisor:` commits, almost all *relaxing* or *expanding* classifier rules ("Relax the `known_job_board` check…", "Expand `TITLE_DOMAINS['strategy']`…", "Relax company extraction for iimjobs…").
- A dedicated one-shot workflow, `fix-iimjobs-once.yml`, to patch bad rows after the fact.
- `monitor: auto-add N companies to India whitelist` commits, and a follow-up fix — `53ca0ed fix(i4): filter job-title strings from company whitelist auto-adds` — i.e. job-title fragments **had already leaked into the company whitelist**.

That pattern is the signature of heuristics fighting reality. Every fix is local; the next source breaks a different way. **The live audit (Appendix A) confirms it: a 10-random-row PM sample scored 1–2 / 10 against the SPEC's success metric, which requires 10/10. Only 22% of the 294 rows the board serves clear all five quality criteria.**

---

## 4. Findings (prioritised)

| ID | Sev | Finding |
|----|-----|---------|
| C1 | 🔴 Critical | GitHub Actions usage is ~5–11× the free-tier budget — zero-cost constraint is broken |
| C2 | 🔴 Critical | Self-improvement loop runs open-loop: no feedback signal, no regression guard, structurally biased toward loosening quality |
| C3 | 🔴 Critical | Pipeline quality control is brittle heuristics, not AI-native — git history proves non-convergence |
| C4 | 🔴 Critical | PM and Founder's Office commingled on the board — v0.1's "50 PM" goal is not measurable |
| H1 | 🟠 High | Workflow files are duplicated and drifted (root vs project copies) |
| H2 | 🟠 High | `MONITOR_SPEC.md` describes a system that was never built |
| H3 | 🟠 High | Client re-sort silently overrides the SQL seniority sort with a different, wrong ladder |
| H4 | 🟠 High | Schema drift — `domain` CHECK forbids `'strategy'`; `last_enriched_at` not in `schema.sql`; CLAUDE.md schema obsolete |
| H5 | 🟠 High | Review/approval is a pure human bottleneck — caps a solo operator far below 50+30 |
| H6 | 🟠 High | Silent failure everywhere — board serves fake seed data on any DB error; scraper/monitor swallow exceptions |
| H7 | 🟠 High | India whitelist is self-corrupting (`gohighlevel` auto-added despite SPEC naming it non-India) |
| H8 | 🟠 High | iimjobs/instahyre rows force-set `confirmed` → skip enrichment — the worst extraction source never gets cleaned |
| M1 | 🟡 Med | Self-improvement uses the cheapest non-reasoning model (gemini-2.5-flash, thinking disabled) |
| M2 | 🟡 Med | Production board ships default "Create Next App" metadata |
| M3 | 🟡 Med | Dead code: `enrich_jobs.py`, `seed_jobs.py`, `scraper.py` (LinkedIn) |
| M4 | 🟡 Med | Broad-scope `GH_TOKEN` PAT + auto-push to `main` with no review gate = large injection blast radius |
| M5 | 🟡 Med | HTML entities never stripped from titles (`clean_title`) — violates quality criterion 2 |
| M6 | 🟡 Med | The monitor runs on GitHub Actions — the very thing it monitors (not independent, as MONITOR_SPEC requires) |
| M7 | 🟡 Med | `digest.py:131` bug — Founder's Office attention items never fire |

### Critical findings — detail

**C1 — Cost constraint broken.** The live schedule (root `.github/workflows/`): enrich every 15 min (96 runs/day), pipeline-fast every 30 min (48/day), jobspy every 2h, parallel every 3h, hn every 6h, monitor-reliability every 2h, monitor-improvement daily — **~181 runs/day**. At 2–4 min each that is ~11,000–22,000 Actions-minutes/month. CLAUDE.md states the repo is **private**, where the free tier is **2,000 min/month**. The system runs out of minutes mid-month, every month — which is exactly the "runner queue timeout / minutes exhausted" failure MONITOR_SPEC.md already calls out as a known symptom. *Direction:* cut enrich to hourly and pipeline-fast to 2h (already the intent in the drifted copies — see H1); or make the repo public (Actions then free and unlimited); or move cron to the planned Oracle VM. This is the first thing to fix — nothing else matters if the pipeline halts.

**C2 — The self-improvement loop has no safety rail.** `ai_advisor.py` collects daily metrics, asks Gemini for code edits, runs `ast.parse` as the *only* check, then `git push origin HEAD:main`. There is **no feedback signal** — nothing measures whether yesterday's change helped or hurt; the loop never attributes a metric move to a prior commit. Its symptom thresholds (e.g. "board low → need more jobs") **structurally bias it toward loosening filters to chase volume** — directly against v0.1's "quality is the only metric." There is no test run, no diff-size cap, no precision check, no human gate, no revert path. This is the crown jewel and it is the riskiest component. *It should not be removed — it should be given a measurable objective and a revert path* (see §7).

**C3 — Quality control is not AI-native.** Classification, company extraction, India-eligibility, and summarisation are all keyword/regex (`pipeline.py` `classify`, `_extract_company_from_search_result`, `classify_india_hiring`, `has_india_office`). Substring matching causes real bugs — `has_india_office` does `known in co or co in known`, so `"ola"` matches "Solana"/"Coca-Cola". The "AI-generated summary" (`description_summary`, documented as AI in CLAUDE.md) is actually `enrich.py` grabbing the first prose paragraph and truncating to 300 chars — **no generative model runs anywhere in the pipeline.** The irony: `enrich.py` already fetches the full JD markdown, then throws regex at it instead of one structured-extraction LLM call. *Direction:* replace `classify` + company extraction + summary with a single LLM call over the fetched JD; it would delete ~200 lines of regex and the entire churn class the git log documents.

**C4 — PM and Founder's Office are commingled.** `board/lib/jobs.ts:93`: `... domain IN ('pm', 'strategy')`. The board's default view mixes PM and Founder's Office rows. SPEC.md:373 says the Founder's Office section must be a **separate tab, hidden until 10+ listings qualify**. There is no separate query and no gate. Consequence: v0.1's success metric — "50 PM listings, audit any 10 random rows" — **cannot be measured**, because a random sample of board rows is not a PM sample. *Direction:* split into two queries / two tabs; gate FO behind the 10-listing threshold.

### High findings — detail

**H1 — Duplicated, drifted workflows.** Workflow files exist in **two** places: `/.github/workflows/` (repo root — the only one GitHub actually executes) and `/pranav-personal/product-hiring-stuff/.github/workflows/` (a copy GitHub ignores entirely). They have drifted: root `enrich.yml` = `*/15`, project copy = `0 * * * *`; root `pipeline-fast.yml` = `*/30`, project copy = `0 */2`. Anyone reading the project-folder copies (the natural place to look) is reading **inert, stale config**. *Direction:* delete the project-folder copies; keep one source of truth at root. Note the drifted copies hold the *cheaper* schedule the project apparently wants — adopt those values at root (ties into C1).

**H2 — The monitor spec is fiction.** `MONITOR_SPEC.md` describes a reliability monitor that invokes **Claude Code** for auto-fix, with a 3-attempt anti-loop state machine, `state.json` on an Oracle VM, `/ack` escalation, and a `checks.py/fix.py/state.py` module split. **None of that exists.** What was built instead — the AI-advisor self-improvement loop, the dead-link sweeper, the daily digest — the spec never mentions. The spec actively misleads. *Direction:* rewrite MONITOR_SPEC.md to describe the system that exists, or delete it.

**H3 — Two seniority ladders; the wrong one wins.** `jobs.ts` builds a 6-tier SQL `ORDER BY`; `JobBoard.tsx:117-121` then re-sorts every row client-side with `getSeniorityRank` — a *different* 5-tier ladder (Director collapsed into the CPO/VP tier). The client sort overrides the SQL sort, so the board's actual order is the wrong ladder. SPEC.md:193 explicitly requires board and enrichment to share "exactly the same expression." *Direction:* one shared seniority definition; sort once.

**H4 — Schema drift.** `schema.sql:39` constrains `domain` to `('pm','design','data','other')` — `'strategy'` is **not allowed**, yet v0.4 stores Founder's Office as `domain='strategy'`. It only works because code reads `norm_function` first. `last_enriched_at` is queried all over the board but is **not defined in `schema.sql`** (added by an undocumented migration). CLAUDE.md's schema section omits ~10 columns the board depends on. *Direction:* reconcile schema.sql + migrations into one true current schema; fix the CHECK; update CLAUDE.md.

**H5 — Human approval is the throughput ceiling.** `review_status` flips to approved/rejected only via the `/review` page, one human, hardcoded `reviewed_by='pranav'`. For a solo operator targeting 80 listings the manual gate is the binding constraint. The 5-criteria bar (clean title, real company, India-eligible) is precisely an LLM grading task. *Direction:* an LLM grader that auto-approves/rejects with a confidence score and a written rationale, escalating only genuine edge cases to the human.

**H6 — Silent failure is the default.** `board/lib/jobs.ts` `getJobs` catches every error and returns `SEED_JOBS` — two hardcoded fake rows (incl. a CRED *Designer*, out of scope for v0.1). **A DB outage and a missing column both render as a normal-looking board with fake data.** `pipeline.py` `save_job_signal` swallows all exceptions and returns `False` with no log. The monitor swallows git/Telegram/Gemini errors at WARNING into ephemeral runner logs. *Direction:* fail loud — explicit empty/error states on the board, structured logging, alert on a source returning zero.

**H7 / H8 — Whitelist self-corrupts; iimjobs bypasses cleanup.** The India whitelist auto-adds companies with no review; `gohighlevel` (a US SMB SaaS the SPEC explicitly names as having *no* India office) was auto-confirmed. Separately, any iimjobs/instahyre URL is force-set `india_hiring='confirmed'`, and `enrich.py` only enriches `unknown` rows — so the source with the **worst** company-name extraction is the one that **never gets an enrichment cleanup pass**. *Direction:* whitelist auto-adds go to a pending state for confirmation; iimjobs rows must enrich, not bypass.

---

## 5. AI-Native Scorecard

Against the new Objective 6:

| Task | Today | AI-native target |
|------|-------|------------------|
| Title classification (is this PM?) | Keyword lists + substring match | One LLM call per job over the JD |
| Company-name extraction | Per-source regex / URL-slug parsing | LLM structured extraction from fetched JD |
| India-eligibility | Signal-counting + static whitelist | LLM with web knowledge: "does this company hire PMs in India?" |
| JD summary | First-paragraph truncation (mislabelled "AI") | Actual LLM summary keyed to what a candidate needs |
| Review / approval | 100% human | LLM grader + confidence; human only on edge cases |
| Self-improvement | Gemini-2.5-flash, **thinking disabled**, edits code blind from 15 KB of truncated source | Reasoning-grade frontier model, full-repo context, runs tests, measures own impact |

**One model call exists in the whole system** — the self-improvement advisor — and it uses the cheapest, non-reasoning tier to autonomously rewrite production code. That is exactly inverted: the highest-stakes action gets the weakest model, and the dozens of lower-stakes per-job judgements get no model at all. The system is, today, **not AI-native** — it is a conventional scraper with one risky LLM bolt-on.

---

## 6. Priority Sequence

1. **Stop the bleed (C1, H1).** Collapse to one set of workflows at repo root; cut enrich→hourly, pipeline-fast→2h; decide repo-public vs Oracle-VM cron. Until this is done the pipeline keeps stalling mid-month.
2. **Make the board measurable (C4, H3).** Split PM and Founder's Office into separate tabs; one shared seniority sort. Then run the first real 10-row quality audit.
3. **Put a rail on the crown jewel (C2, M1).** Before trusting the loop further: a feedback signal (did precision/approval-rate move after the last commit?), a diff-size cap, and a one-command revert. Move it to a reasoning-grade model.
4. **Make the pipeline AI-native (C3, H5).** Replace `classify` + company extraction + summary with a single LLM call over the JD `enrich.py` already fetches. Add an LLM review-grader. This is what kills the git-log churn permanently.
5. **Fail loud (H6).** Remove the fake-seed fallback; structured logging; alert on zero-result sources.
6. **Clean up (H2, H4, H7, H8, M2–M7).** Rewrite or delete MONITOR_SPEC.md; reconcile the schema; fix the whitelist and iimjobs paths; delete dead code; fix the production page title.

---

## 7. The One Thing Worth Saying Plainly

The project's instinct — let a small loop run itself — was correct and proved its value. The next move is **not** more autonomy; it is giving that loop something it currently lacks entirely: **a way to know if it is winning.** Right now it can only loosen filters and hope. A self-improving system without a feedback signal does not improve — it drifts. Give it a measurable objective (quality-audit pass rate), a memory of its own past changes, and a revert path, and the surprise that worked becomes a system you can actually trust to run while you sleep.

---

## Appendix A — Live Data Audit (2026-05-21)

The gap the main review flagged — no live board or DB access — is now closed. The board server-renders every row it serves and embeds the full objects in the page payload, so the live dataset was extracted from `board-pi-eight.vercel.app` **without DB credentials**. **294 rows** were recovered — the complete set `getJobs()` returns (approved+confirmed and pending+unknown, PM + Founder's Office). Each was scored against the SPEC's five quality criteria; every `signal_url` was HTTP-checked.

**The board is live** — real data, not the `SEED_JOBS` fallback. 232 rows are `pm`-domain, 62 Founder's Office; 208 `confirmed`, 86 `unknown`.

### A.1 — The SPEC success metric: 10 random PM rows must score 10/10

10 rows sampled at random from the 164 confirmed PM-domain rows (seed 521):

| # | Title (as shown on board) | Company (as stored) | Verdict |
|---|---------------------------|---------------------|---------|
| 1 | Principal Product Manager, Security | Commvault | ✅ pass |
| 2 | Viseven - Head of Product | Viseven | ✗ C2 — company name prefixed into the title |
| 3 | Snapmint - Senior Product Manager | Snapmint | ✗ C2 — company prefix |
| 4 | Fam - Senior Product Manager | Fampay | ✗ C2 — company prefix |
| 5 | Product Manager job | `Job 387420 Product Manager At Payu Gurgaon` | ✗ C2 + C3 |
| 6 | Senior Product Manager job | `Job 207206 Senior Product Manager At Navi 3 Bangalore` | ✗ C2 + C3 |
| 7 | Product Manager job | `Job 414860 Product Manager At Anaira Ai Mumbai` | ✗ C2 + C3 |
| 8 | Senior Product Manager | Arcadiacareers | ⚠ C3 — company is the Greenhouse board slug, not "Arcadia" |
| 9 | Clari - Senior Product Manager | Clari | ✗ C2 — company prefix |
| 10 | Product Manager | `Product Manager Ecommerce` | ✗ C3 — the role name stored as the company |

**Result: 1–2 of 10 pass. The SPEC requires 10/10. The v0.1 success metric fails decisively** — confirming, on live data, the inference in §3.

### A.2 — Full-set scan (all 294 served rows)

| Criterion | Rows failing | Dominant cause |
|-----------|--------------|----------------|
| C1 — real PM title | 72 / 294 | 41 Chief-of-Staff / Founder's-Office roles are mis-domained as `pm` (confirms C4); plus marketing and design roles in the PM bucket |
| C2 — clean role name | 159 / 294 (54%) | 76 carry the company name prefixed into the title ("Viseven - Head of Product"); 73 end in the literal word "job"; 8 contain mojibake |
| C3 — clean company name | 85 / 294 (29%) | **62 rows store a whole job-posting-title string as the company** — e.g. `Job 387420 Product Manager At Payu Gurgaon` — every one from the instahyre extraction path |
| C4 — India-eligible | 0 location mismatches | every `confirmed` row carries an India location string — but this checks consistency only, not whitelist correctness (H7 still stands) |
| C5 — live link | 23 hard-dead | 21 Greenhouse job URLs now redirect to the company's all-jobs index (posting removed); 1 "closed" page; 1 server error |

**Only 65 of 294 rows (22%) clear all five criteria. 88 (29%) clear C1–C4 ignoring the link check.**

### A.3 — Source concentration

`parallel_search` produces **254 of 294 rows (86%)** and **75% of its rows are dirty**. It is at once the board's main supply and its main quality problem. Direct `lever` is the cleanest source (15% dirty).

### A.4 — The "AI summary" is page chrome

Spot-check of `description_summary` on a top row returned: *"LinkedIn profile Loading... Apply with LinkedIn Profile added Authorize sharing... ATTACH RESUME/CV Couldn't auto-read resume. Analyzing resume..."* — scraped apply-form boilerplate, not a JD summary. Confirms C3: no generative model runs; the "summary" is first-paragraph truncation and the first paragraph is often page UI.

### A.5 — Honest limits of this audit

- **99 of 294 links could not be verified from this environment** — instahyre (72), wellfound (14), indeed (13) all return HTTP 403 to a non-browser client. They are *not* counted as dead; the true dead-link count is ≥ 23 and probably higher.
- **C4 was a location-consistency check only.** Verifying that each `confirmed` company genuinely hires PMs in India needs per-company web lookups — not done here. H7 (whitelist self-corruption) is unaffected.
- The audit reads the board's *served* set (approved+confirmed / pending+unknown). `rejected` rows were not visible — which is correct: this measures what a candidate actually sees, the right denominator for the SPEC metric.

### A.6 — Verdict

The main review inferred the board could not pass its own quality bar. **The live audit confirms it: 1–2 / 10 on the success metric, 22% fully clean overall.** The highest-leverage fix is unchanged from §6 step 4 — replace the parallel_search-fed regex/keyword extraction with one LLM structured-extraction call over the JD. That single change addresses the company-prefix titles, the "job" suffixes, and the job-id-string company names — together 90%+ of what this audit found.
