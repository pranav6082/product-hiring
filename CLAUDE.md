# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## The Central Direction — Read This First, Every Session

**This is a production-grade, single-person company — not a side project or exploration.**

Every technical and product decision must be made from the perspective of an owner actively helping real candidates and real companies. The standard is: would this hold up if 50 candidates were relying on it tomorrow? If not, it's not good enough.

This means:
- **No local-only infrastructure.** If Pranav's laptop is closed, the system must still work.
- **No "good enough for now" shortcuts** that create debt before the first real user.
- **Every feature exists to serve a match** — a candidate placed well, a company's real need met. If it doesn't trace back to that, question whether to build it.
- **Pranav is the only operator.** Nothing that requires a second person to maintain, monitor, or fix.
- **Zero spend until revenue.** Every infrastructure choice must have a free tier that is genuinely production-viable. No trials, no "upgrade later" assumptions.
- **CLI-native and AI-agent-operable only.** Every tool must be fully controllable via CLI and API. If operating it requires clicking through a UI, it is the wrong tool. Claude Code runs everything — Pranav does not touch code or dashboards.

## How This Gets Built

Pranav is a product manager, not a developer. Claude Code is the engineering team. Pranav makes product decisions; Claude Code writes the code, runs the commands, sets up infrastructure, and debugs. The only things Pranav does personally: one-time account signups, LinkedIn session export (once), and approving significant actions before Claude proceeds.

## What This Is

A solo hiring intelligence practice for the product domain (PM, Product Designer, Product Data). The core thesis: hiring is broken by information asymmetry — JDs don't reflect reality, urgency is opaque, candidate context is unknown. The offering is deep 1:1 matching powered by network intelligence, not volume.

The differentiator: **LinkedIn feed, not job boards.** The feed shows who posted the job. That person is the warm entry point — Pranav may already know them or have a mutual connection. Public job boards strip that signal entirely.

## Validated Zero-Cost Stack

Each tool was validated via live Parallel CLI web search before being confirmed. Reasoning is documented below.

| Layer | Tool | CLI | Cost |
|---|---|---|---|
| Compute + cron | Oracle Cloud free ARM VM | `oci` CLI | $0 permanent |
| Browser sessions | Self-hosted Steel browser (open source) on Oracle VM | REST API + Playwright | $0 |
| Scraper | Playwright (Python) | Python scripts | $0 |
| Database | Neon (Postgres) | `neon` CLI + psql | $0 permanent, never pauses |
| Board UI | Vercel | `vercel` CLI | $0 |
| Web validation | Parallel CLI (`parallel-cli`) | `parallel-cli` | $0 (16k free searches) |
| Alerts + commands | Telegram Bot API | Python SDK | $0 |
| Code + secrets | GitHub (private repo) | `gh` CLI | $0 |

**Why Oracle Cloud:** Permanent free ARM VM (4 cores, 24GB RAM). Enough to run Chrome via Steel browser. Cron runs nightly with no machine dependency.

**Why self-hosted Steel browser over raw Playwright:** Steel (open source, `github.com/steel-dev/steel-browser`) manages authenticated browser sessions properly, with better anti-detection than raw Playwright. Runs on Oracle VM. Paid Steel.dev cloud was rejected — $29/month, no free tier.

**Why Oracle over GitHub Actions for scraping:** GitHub Actions datacenter IPs are in known ASN ranges flagged by LinkedIn. Oracle Cloud IPs are in Oracle's ASN — also datacenter, same risk — but Oracle gives a persistent VM with full session management, which is harder to distinguish from a real user than a short-lived Actions runner. Risk is accepted for authenticated, low-volume, read-only scraping (once/night). If blocked, fallback is Gmail API parsing LinkedIn email digests — no browser needed, zero block risk.

**Why Neon over Supabase:** Supabase free projects pause after 7 days of inactivity. Neon confirmed (via Parallel search, 2026) to never pause. `neon` CLI handles all provisioning without touching a UI.

**Why Parallel CLI for validation:** Agents hallucinate company details, poster roles, and urgency. Parallel validates scraped data against live web before any briefing goes to Telegram. Already installed and authenticated on this machine.

## Schema

Four tables. Designed to support multiple sources (LinkedIn feed, LinkedIn jobs, Telegram, WhatsApp, and any future source) without schema changes.

### `sources` — config table, one row per source type
```
id, name ('linkedin_feed' | 'linkedin_jobs' | 'telegram' | 'whatsapp'),
scraper_class, is_active, config (JSONB)
```
Adding a new source = inserting a row. No schema change.

### `people` — the network anchor (who posted or shared)
```
id, name,
linkedin_url, linkedin_id (stable internal ID),
telegram_handle, whatsapp_number,
known_to_pranav (bool), relationship_strength (1-3),
notes, first_seen_at, updated_at
```
Built up over time. The foundation for warm outreach.

### `jobs` — canonical job record, deduplicated across all sources
```
id, title, company, location, employment_type,
domain ('pm' | 'design' | 'data' | 'other'),
job_url (direct application link if available),
description_summary (AI-generated),
first_seen_at, updated_at, is_active
```
One record per real job, regardless of how many places it appears.

### `signals` — every sighting from every source (many per job)
```
id,
job_id → jobs (nullable until matched),
source_id → sources,
person_id → people (who posted/shared),
signal_url (URL of the post/message),
profile_url (poster's profile on that platform),
raw_text, urgency_signals (TEXT[]),
post_date, scraped_at,
validated (bool), validation_result (JSONB), validated_at,
briefed (bool), briefed_at
```
`briefed = false` index drives Telegram — agent queries only unbriefed, validated signals.

### Key indexes
- `signals(scraped_at DESC)` — board default sort
- `signals(briefed) WHERE briefed = false` — Telegram queue
- `people(known_to_pranav) WHERE known_to_pranav = true` — warm outreach filter
- `jobs(domain)`, `jobs(company)` — board filters

### How deduplication works
When a signal arrives, scraper checks `jobs` for same company + similar title. Match found → link signal to existing job. No match → create new job record. Logic lives in the scraper, not the schema.

## Build Order

1. Oracle Cloud VM provisioned via `oci` CLI
2. Steel browser self-hosted on VM
3. Neon DB + schema via `neon` CLI
4. LinkedIn scraper (Playwright, Python) deployed to VM
5. Job board UI deployed to Vercel
6. Telegram bot wired for briefings
7. Nightly cron enabled on VM

## Directory Structure

- `CLAUDE.md` — this file. Read every session.
- `README.md` — original philosophy
- `EVOLUTION.md` — living log of insights. Always append, never overwrite. Mark breakthroughs with ⭐.
- `log/` — raw captures, voice notes, seed thoughts
- `scraper/` — LinkedIn feed scraper
- `db/` — schema, migrations
- `board/` — job board frontend
- `agent/` — Telegram integration and briefing logic

## LinkedIn Safety Rule

LinkedIn actions (posting, messaging, connecting) are **always taken by Pranav manually**. The scraper is read-only. Agent drafts; human sends.
