# SPEC.md

The product spec, versioned. Read top-to-bottom before any pipeline or board change.

---

## Version 0.1 — Product Manager roles only

### Goal

**50 high-quality Product Manager job listings on the board.** Quality is the only metric. Volume is not. If we have 30 listings that all clear the bar and 20 borderline, ship 30. Don't dilute.

### Scope

**In scope: Product Manager roles ONLY.** Nothing else.

A role qualifies if its title is in the PM family:

| Acceptable title pattern | Examples |
|---|---|
| Product Manager | Product Manager, PM |
| Associate Product Manager | APM, Associate PM |
| Senior Product Manager | Sr PM, Senior PM |
| Staff Product Manager | Staff PM |
| Principal Product Manager | Principal PM |
| Lead Product Manager | Lead PM |
| Group Product Manager | GPM |
| Director, Product / Director of Product | Director Product Management |
| VP Product / VP of Product / Vice President Product | VP, Product |
| Head of Product | Head of Product (region/area) |
| Chief Product Officer | CPO |

Variations such as "Senior Product Manager, Payments" or "Product Manager — Growth" are accepted **as long as the core role is PM**.

### Out of scope for v0.1 (each gets its own section in later versions)

- **Product Designer / UX / UI** → "Design" section (v0.2)
- **Data Analyst / Data Scientist / Product Analyst** → "Data" section (v0.3)
- **Chief of Staff / EIR / Founding Team / Strategy** → "Strategy" section (v0.4)
- **Software Engineer / Developer / Architect** → "Engineering" section (v0.5)

Permanently out of scope (not on this board at any version):

- Marketing Manager, Sales, Account Manager, Project Manager (≠ Product Manager), Customer Success, HR, Legal, Finance.

---

## Quality criteria — every listing must meet ALL FIVE

### 1. Title is a real PM title

- Must match the PM whitelist above.
- "Software Development Engineer" pretending to be PM → reject.
- "Account Manager", "Project Manager", "Marketing Manager" → reject.
- "Implementation Lead", "Customer Success Manager" → reject.

### 2. Role name displayed on the board is clean

No gibberish. Specifically:

- No HTML artifacts: `039` (apostrophe entity), `&#x2F;`, `&amp;`, `&#`
- No years-of-experience suffix: "Product Manager 2-10 Yrs"
- No URL fragments: "Product Manager https://..."
- No iimjobs/iim-isb-mdi qualification suffixes
- No truncated junk like "Sr, Product Manager II"
- Casing must be human-readable, not slug-style ("Senior Product Manager", not "Senior-Product-Manager")

### 3. Company name is correct, real, and clean

The company name must be:

- A real, recognisable company name (a person reading the board would know what it is, or could Google it and find the company website on first result)
- Not a country / region: not "USA", "India", "Global", "Remote"
- Not a role keyword: not "Senior", "Lead", "Group", "Manager", "Director"
- Not a job-board domain: not "wellfound.com", "iimjobs.com", "linkedin.com", "instahyre.com"
- Not a URL or HTML fragment
- Not "Unknown" or empty
- Not someone's personal name (LinkedIn profile leaks)

### 4. India-eligible (the hard bar)

The role must be **realistically hireable for a candidate based in India**.

| Setup | Eligible? |
|---|---|
| Onsite + Indian city (Bangalore, Mumbai, Delhi-NCR, Hyderabad, Pune, Chennai, etc.) | ✅ Yes |
| Hybrid + Indian city | ✅ Yes (company has an office there) |
| Remote, AND company has a publicly-known India office | ✅ Yes |
| Remote, AND company has no India office | ❌ No |
| Onsite + non-India city | ❌ No |
| Vague "remote" with no country signal in JD | ❌ Reject as default; only pass if enrichment finds India signal |

**Key principle:** "Remote" on a US company's job board does not mean "we hire from India." US-remote = US-remote unless the company has an India office or explicit "we hire in India" language. Always require positive India signal.

The Indian-office whitelist is therefore the primary check for remote roles. Examples:

- ✅ **Has India office:** Razorpay, Meesho, PhonePe, Swiggy, Zomato, CRED, Groww, Zepto, Microsoft India, Google India, Amazon India, Atlassian India, Salesforce India, etc.
- ❌ **No India office:** GoHighLevel (US SMB SaaS, no India office), Caterpillar (heavy machinery, no India PM hiring), Bayer (pharma, no India PM hiring), most YC US-only startups.
- ⚠️ **Unsure:** treat as `unknown`, ride enrichment to verify before approving.

### 5. Live, active job — link works AND job is open

- URL returns HTTP 200 (not 404, 410, or redirected to a listing/jobs-home page)
- Page does not say:
  - "No longer accepting applications"
  - "This position has been filled"
  - "Position closed"
  - "Job has expired"
  - "We are no longer hiring for this role"
- Greenhouse / Lever pages must show the actual JD content, not the company's all-jobs index page

---

## Implementation deltas (current state → v0.1)

| # | Current behaviour | v0.1 target |
|---|---|---|
| 1 | Board shows PM, Design, Data, Strategy together | Board filters to PM only. Other domains hidden but data retained for v0.2+ |
| 2 | Title classifier accepts broad keyword matches | Tighten — explicit engineering/marketing/sales blocklist on title BEFORE PM check |
| 3 | iimjobs company extraction can return job-title fragments | Stricter: if extracted name fails any clean-name check (rule 3), return Unknown → job goes to pending |
| 4 | "Remote" treated as India-eligible if no US signals | Stricter: remote requires explicit India office whitelist OR India hiring language in JD |
| 5 | Dead-link check: HTTP 404/410 | Also: regex check on enriched JD for "no longer accepting", "position closed", etc. → reject |
| 6 | Default sort: PM + Strategy tied for top | Default sort: PM seniority ladder (CPO/VP/Head → Director → Group/Principal/Staff → Senior/Lead → Mid → Associate) |
| 7 | Company-name cleanliness checked at compute_review_status only | Apply at every save; on failure go to pending, never approved |
| 8 | Engineering roles slip through as "strategy" or "pm" | Hard pre-filter: any of [software engineer, developer, architect, sde, sre, devops, qa, tester, analytics engineer, data engineer] → reject from this section |

### India-office whitelist (v0.1 starter list)

Indian-headquartered or India-significant offices, eligible for "Remote" roles:

```
PhonePe, Razorpay, Zepto, Swiggy, Cred, Groww, Zomato, Locus,
Workindia, MoEngage, Chargebee, BrowserStack, Freshworks, Postman,
Hasura, Setu, Smallcase, Fi-Money, Jupiter, OlaMoney, Meesho,
Khatabook, Loopai, Dezerv, Juspay, Recko, Simpl, Cashfree, Niyo,
KreditBee, Mswipe, Zetwerk, Darwinbox, Rupeek, Open Financial,
Uni Cards, OfBusiness, ElasticRun, Delhivery, Acko, Slice (fintech),
PolicyBazaar, Cure.fit, Urban Company, Unacademy, BYJU'S, Vedantu,
PharmEasy, 1mg, Lenskart, Nykaa, Dream11, Games24x7, Mobile Premier League,
Paytm, BharatPe, Pine Labs, Whatfix, Innovaccer, Zoho, ThoughtSpot.

Global companies with major India product offices (PM roles eligible):
Microsoft, Google, Amazon, Salesforce, Atlassian, ServiceNow, Adobe,
LinkedIn, Stripe, Walmart Labs (Flipkart parent), Uber, Booking.com,
Cisco, Oracle, SAP, VMware, Twilio, Snowflake, Databricks, Confluent.
```

This list is not closed. It expands as we discover more companies. The `india_hiring=unknown` state exists exactly so enrichment can verify a company we haven't whitelisted yet.

### Engineering blocklist on title (v0.1)

Titles that must auto-reject from the PM section:

```
software development engineer, software engineer, full stack engineer,
backend engineer, frontend engineer, devops engineer, data engineer,
analytics engineer, sre, qa engineer, test engineer, automation engineer,
android engineer, ios engineer, mobile engineer, embedded engineer,
machine learning engineer, ai engineer, security engineer, platform engineer,
solutions architect, software architect, principal engineer, staff engineer,
lead engineer (when not "lead product manager")
```

(These will become the Engineering section in v0.5 — for now they're not on the board at all.)

---

## Sort and priority rules

### 1. Board sort order (default view, top → bottom)

Most senior roles at the top. Within each tier: newest first.

| Tier | Examples |
|---|---|
| **1. C-suite / VP / Head** | CPO, Chief Product Officer, VP Product, VP of Product, Head of Product |
| **2. Director** | Director of Product, Director, Product |
| **3. Staff / Principal / Group** | Staff PM, Principal PM, Group Product Manager |
| **4. Senior / Lead** | Senior PM, Sr. PM, Lead PM |
| **5. Mid (default)** | Product Manager (no seniority modifier) |
| **6. Associate / Junior** | Associate PM, APM, Junior PM |

Rationale: a senior candidate should see top roles first; a mid candidate scrolls down. Either way, the most consequential roles are most visible.

### 2. Enrichment priority order

When the enrichment cron runs (every 15 min, batch of 20), it must work the queue in this priority order:

1. **`pending` + `unknown` PM jobs at higher seniority first** (executive → director → staff → senior → mid → associate). These are the highest-impact unknowns to resolve — promoting a CPO role to confirmed is far more valuable than promoting one of fifty mid-level PM listings.
2. **`approved` + `confirmed` PM jobs whose enrichment is stale (> 7 days old)**, again seniority-first. This re-checks dead links and updates summaries.
3. **Skip non-PM domains entirely in v0.1.** Design/data/strategy jobs do not consume enrichment budget while they're out of scope.

The 20-job batch must use the v0.1 scope filter (`COALESCE(norm_function, domain) = 'pm'`) and the seniority `ORDER BY` — exactly the same expression the board uses, so the senior-first invariant holds end-to-end.

---

## Automation-alignment rule (meta)

**Every cron, every script, every automation job must encode this spec — not just the board UI.**

Concretely, before any code change ships:

- `pipeline.py` (every source) must respect: PM-only classification, engineering/non-product blocklist, India-office whitelist, title cleanup.
- `enrich.py` must respect: PM-only fetch, seniority-first priority, India-office whitelist for re-classification, closed-job rejection.
- `board/lib/jobs.ts` SQL must respect: PM-only filter, seniority sort, india_hiring tristate.
- `board/components/JobBoard.tsx` UI must respect: hidden long-term-only features (Posted by, Known contacts), unconfirmed-remote toggle, PM-only header.
- `.github/workflows/*` schedules must reflect what's actually wanted: if v0.1 is PM-only, the strategy/design/data branches should not be running searches that aren't being used.
- `SPEC.md` is the source of truth. If code disagrees with spec, code is wrong (or spec is updated explicitly with a version bump).

Every PR/commit should be answerable to: "which spec rule does this implement or maintain?"

---

## Success metric

The board shows **at least 50 listings**, and a sample audit of any 10 random rows must score **10/10 on every quality criterion above**.

If a 10-row sample scores 8/10, the bar is not met — fix the failures before adding more.

---

## Out-of-scope deferrals (long-term)

These are explicitly NOT v0.1 work, so we don't accidentally drift:

- **LinkedIn feed scraper** (warm connection layer) → deferred until Oracle VM
- **"Posted by" / known-contact column** → hidden on UI, code preserved
- **Telegram briefings** → not part of v0.1 board quality work
- **Other domain sections** (Design, Data, Strategy, Engineering) → v0.2+
- **Volume optimisation** → deliberately not pursuing more sources, more searches, more keywords

---

## Versioning rule

When we change the spec, bump the version section header (`## Version 0.2 — ...`). Don't overwrite v0.1 — append. The history of decisions is more valuable than a clean current-state.

---

## Version 0.4 — Founder's Office / Chief of Staff

> v0.2 (Design) and v0.3 (Data) are planned but not yet specced. v0.4 is specced here because the demand signal and candidate profile for Founder's Office / CoS roles are well understood.
>
> **Scope decision (recorded):** Corporate strategy leadership titles (CSO, VP Strategy, Director of Strategy, CBO) are explicitly excluded. Those roles attract a different candidate profile (large-company, functional strategy) that does not overlap with the founder-proximity operator that this section targets. The domain in code remains `'strategy'` for legacy compatibility but the board labels it "Founder's Office."

### Goal

**30 high-quality Founder's Office / Chief of Staff job listings on the board.** Quality over volume. This is a smaller market than PM — 30 clean listings beats 50 diluted ones.

### Why this domain

Founder's Office and Chief of Staff roles are structurally underserved by standard job boards. They are often:
- Posted informally (LinkedIn post, not a JD)
- Titled inconsistently across companies (same job, five different titles)
- Hard to assess from a JD alone (the actual scope depends entirely on the founder)

This is exactly the kind of high-signal, low-volume domain where deep matching beats volume.

### Scope

**In scope: Founder's Office, Chief of Staff, EIR, and StratOps at startups ONLY.**

A role qualifies if its title is in this family:

| Acceptable title pattern | Examples |
|---|---|
| Chief of Staff | CoS, Chief of Staff to CEO / CTO / Co-founder |
| Founder's Office | Head of Founder's Office, Founder's Office Associate, Founder's Office Lead |
| Entrepreneur in Residence | EIR (at a startup or VC fund with India presence) |
| Head of Special Projects | Special Projects Lead, Special Projects Manager |
| Strategic Initiatives | Strategic Initiatives Manager — **only at a startup** (enterprise title = reject) |

Variations such as "Chief of Staff to the CEO" or "Founder's Office — Growth" are accepted **as long as the core role maps to this family**.

**"Founder's Office" as a title is Indian-startup-specific.** It is almost always an India-based role at a high-growth startup. Do not reject it for being an unusual title — it is the standard term in the Indian startup ecosystem for this function.

### Out of scope for v0.4

**Permanently excluded (corporate strategy titles — wrong profile):**
- **CSO / Chief Strategy Officer** → large-company functional role. Reject.
- **VP Strategy / VP of Strategy** → same. Reject.
- **Director of Strategy** → same. Reject.
- **CBO / Chief Business Officer** → same. Reject.
- **Head of Strategy** (standalone, no startup context) → reject unless JD is clearly at a startup.

**Other exclusions:**
- **Co-founder / Co-Founder** → equity role, not a hire. Reject.
- **Founding Engineer / Founding Developer** → Engineering section (v0.5).
- **Founding Product Manager** → PM section (v0.1).
- **Founding Product Designer** → Design section (v0.2).
- **Founding Associate / Founding Member** → too ambiguous without "Founder's Office" in the title; reject unless the JD explicitly names Founder's Office or Chief of Staff context.
- **Strategy Consultant** (McKinsey/BCG/Bain title) → consulting, not an operating role. Reject.
- **Management Consultant** → same. Reject.
- **Business Analyst** at a consulting firm → reject.
- **Strategy & Operations / StratOps** → adjacent but too broad; attracts ops-heavy profiles that don't fit the founder-proximity mandate. Reject.
- **Business Operations Lead / Head of Biz Ops** → same reason. Reject.
- **General Manager** → too broad. Reject.

---

## Quality criteria — all five must be met (founders_office domain)

### 1. Title is a real CoS / Founder's Office / StratOps title

- Must match the v0.4 whitelist above.
- "VP Strategy" at any company → reject (corporate strategy, explicitly out of scope).
- "Strategy Consultant" → reject.
- "Co-Founder" → reject (equity role).
- "Operations Manager" → reject.
- "Project Manager" → reject (permanently out of scope; ≠ Chief of Staff).
- "StratOps / Strategy & Operations" → reject (out of scope for v0.4).
- "Founding Associate" without Founder's Office context → reject.
- "Strategic Initiatives Manager" at a large enterprise → reject; same title at a Series B startup → accept.

### 2. Role name displayed on the board is clean

Same rules as v0.1:

- No HTML artifacts: `&#039;`, `&#x2F;`, `&amp;`
- No years-of-experience suffix: "Chief of Staff 3-8 Yrs"
- No URL fragments
- No iimjobs/iim-isb-mdi qualification suffixes
- Casing must be human-readable, not slug-style

### 3. Company name is correct, real, and clean

Same rules as v0.1. Additionally for this domain:

- "VC Fund Name" is a valid company for EIR roles (Peak XV, Accel, Lightspeed India, etc.)
- "Stealth Startup" → acceptable **only** if the enriched JD has a legitimate founder/operator posting it with company context. Bare "Stealth" → reject.

### 4. India-eligible (same hard bar as v0.1)

Same logic and whitelist as v0.1, with one important nuance for this domain:

**Founder's Office and Chief of Staff roles are almost always onsite or hybrid** — founders want proximity. This makes India eligibility easier to assess:

| Setup | Eligible? |
|---|---|
| Onsite + Indian city (Bangalore, Mumbai, Delhi-NCR, Hyderabad, Pune, Chennai) | ✅ Yes |
| Hybrid + Indian city | ✅ Yes |
| "Remote" + Indian-HQ startup | ✅ Yes — but flag: most CoS roles expect you in the room |
| Remote + US/EU company with no India office | ❌ No |
| EIR at VC fund with India office | ✅ Yes |
| EIR at US-only VC fund | ❌ No |

**VC fund whitelist for EIR roles** (India offices confirmed):

```
Peak XV Partners (fka Sequoia India), Accel India, Lightspeed India,
Matrix Partners India, Blume Ventures, Elevation Capital, Nexus Venture Partners,
Stellaris Venture Partners, Kalaari Capital, 3one4 Capital, Chiratae Ventures,
Bessemer Venture Partners India, Tiger Global (India deals team), Insight Partners India.
```

### 5. Live, active job — same rules as v0.1

URL returns HTTP 200, no "position closed / no longer accepting" signals.

---

## Implementation deltas (v0.1 → v0.4 additions)

These are the changes needed to activate the founders_office domain. None of these modify v0.1 PM behaviour.

| # | Area | Change required |
|---|---|---|
| 1 | `pipeline.py` TITLE_DOMAINS["strategy"] | Replace existing partial list with the v0.4 keyword list below. Remove corporate strategy keywords (vp strategy, director of strategy, etc.) |
| 2 | `pipeline.py` blocklist | Add "strategy consultant", "management consultant", "vp strategy", "director of strategy", "chief strategy officer", "chief business officer" |
| 3 | `pipeline.py` | Add VC fund whitelist for EIR india_hiring resolution (same pattern as company whitelist) |
| 4 | `enrich.py` | Extend scope filter to include `domain = 'strategy'` in enrichment batch when v0.4 is active |
| 5 | `board/lib/jobs.ts` | Add founders_office domain query with `COALESCE(norm_function, domain) = 'strategy'` and v0.4 seniority sort |
| 6 | `board/components/JobBoard.tsx` | Add "Founder's Office" tab/section (label "Founder's Office", not "Strategy"); hidden until 10+ listings qualify |
| 7 | `.github/workflows/*` / cron | Activate founders_office-domain search queries when v0.4 ships |
| 8 | `SPEC.md` | This section. Versioned. |

### v0.4 keyword list for `pipeline.py` TITLE_DOMAINS["strategy"]

```python
"strategy": [
    # Chief of Staff
    "chief of staff",
    # Founder's Office (India-specific term — must be explicit)
    "founder's office", "founders office", "founder office",
    # EIR
    "entrepreneur in residence", " eir ",
    # Special Projects
    "head of special projects", "special projects lead", "special projects manager",
    # Strategic Initiatives — startup-context guard required
    "strategic initiatives",
]
```

**Removed from the old list:** `head of strategy`, `vp of strategy`, `vp, strategy`, `vp strategy`, `director of strategy`, `chief strategy officer`, `chief business officer`, `head of business`, `general manager`, `strategy & operations`, `stratops`, `business operations lead`, `founding associate`, `founding member`. All out of scope for v0.4.

**Startup-context guard** (applied during enrichment for `strategic initiatives`): JD must contain at least one of: "series A", "series B", "series C", "seed", "startup", "early stage", "growth stage", "founder". Otherwise reclassify as `other`.

---

## Sort and priority rules (founders_office domain)

### 1. Board sort order (default view, top → bottom)

| Tier | Examples |
|---|---|
| **1. EIR** | Entrepreneur in Residence |
| **2. Head / Senior CoS** | Head of Founder's Office, Senior Chief of Staff |
| **3. Chief of Staff** | Chief of Staff (to CEO / CTO / Co-founder) |
| **4. Special Projects / Strategic Initiatives** | Head of Special Projects, Strategic Initiatives Manager |
| **5. Associate** | Founder's Office Associate, Founder's Office Lead (junior) |

Rationale: EIR sits at the top — it's the highest-leverage role in this family. CoS sits at tier 3 because it's inherently a senior-ish function regardless of modifier.

### 2. Enrichment priority order (founders_office domain)

When v0.4 is active, the enrichment batch extends to:

1. `pending` + `unknown` founders_office jobs, seniority-first (EIR → Head/Senior CoS → CoS → Special Projects/Strategic Initiatives → Associate)
2. `approved` founders_office jobs with stale enrichment (> 7 days)
3. Skip founders_office enrichment if PM enrichment queue is non-empty — PM is still the primary domain

---

## Success metric (v0.4)

The Founder's Office section shows **at least 30 listings**, and a sample audit of any 10 random rows must score **10/10 on every quality criterion above**.

Target is lower than PM (50) because this is a structurally smaller market. Do not dilute by re-admitting the excluded corporate strategy titles to hit the number.

---

## Out-of-scope deferrals (v0.4)

- **Co-founder sourcing** → not a hiring role; out of scope permanently for this board
- **Corporate strategy titles (CSO, VP Strategy, etc.)** → permanently out of scope for this domain; they may get their own section in a later version if demand signal emerges
- **Consulting strategy roles** (McKinsey Engage, BCG Platinion) → permanently out of scope
