"use client";

import { useEffect, useMemo, useState } from "react";
import type { Job, JobStats, PipelineStats } from "@/lib/jobs";

const SOURCE_LABELS: Record<string, string> = {
  linkedin_feed: "LinkedIn Feed",
  linkedin_jobs: "LinkedIn Jobs",
  web_search: "Web Search",
  telegram: "Telegram",
  whatsapp: "WhatsApp",
  manual: "Manual",
};

const WORK_MODE_STYLES: Record<string, string> = {
  remote: "bg-emerald-50 text-emerald-700 border-emerald-200",
  hybrid: "bg-blue-50 text-blue-700 border-blue-200",
  onsite: "bg-zinc-100 text-zinc-500 border-zinc-200",
};

const DOMAIN_LABELS: Record<string, string> = {
  pm: "Product Manager",
  design: "Design",
  data: "Data",
  strategy: "Founder's Office",
  other: "Other",
};

function timeAgo(dateStr: string | null | undefined) {
  if (!dateStr) return "never";
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(diff / 3600000);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

const SOURCE_SHORT: Record<string, string> = {
  linkedin_feed:   "LinkedIn",
  greenhouse:      "Greenhouse",
  lever:           "Lever",
  parallel_search: "Parallel",
  jobspy_indeed:   "Indeed",
  hn_hiring:       "HN",
  naukri:          "Naukri",
};

const DOMAIN_LABELS_SHORT: Record<string, string> = {
  pm: "PM", design: "Design", data: "Data", strategy: "FO / CoS",
};

const PAGE_SIZE = 25;

function getDateOnly(dateStr: string | null | undefined): string | null {
  if (!dateStr) return null;
  const parsed = new Date(dateStr);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toISOString().slice(0, 10);
}

function matchesDateRange(dateStr: string | null | undefined, from: string, to: string) {
  const dateOnly = getDateOnly(dateStr);
  if (!dateOnly) return !(from || to);
  if (from && dateOnly < from) return false;
  if (to && dateOnly > to) return false;
  return true;
}

export function JobBoard({
  jobs, stats, pipeline, initialDomain = "pm", initialUnconfirmed = false,
}: {
  jobs: Job[]; stats: JobStats; pipeline: PipelineStats;
  initialDomain?: string; initialUnconfirmed?: boolean;
}) {
  const [domain, setDomain] = useState(initialDomain);
  const [source, setSource] = useState("all");
  const [workMode, setWorkMode] = useState("all");
  const [knownOnly, setKnownOnly] = useState(false);
  const [includeUnconfirmed, setIncludeUnconfirmed] = useState(initialUnconfirmed);
  const [createdFrom, setCreatedFrom] = useState("");
  const [createdTo, setCreatedTo] = useState("");
  const [updatedFrom, setUpdatedFrom] = useState("");
  const [updatedTo, setUpdatedTo] = useState("");
  const [page, setPage] = useState(1);

  const relevantJobs = useMemo(
    () => jobs.filter((j) => ["pm", "design", "data", "strategy"].includes(j.domain)),
    [jobs],
  );

  // v0.4: the Founder's Office tab stays hidden until 10+ listings qualify.
  const foCount = useMemo(
    () => relevantJobs.filter(
      (j) => j.domain === "strategy" && j.india_hiring === "confirmed",
    ).length,
    [relevantJobs],
  );
  const foTabVisible = foCount >= 10;

  const filtered = useMemo(
    () =>
      relevantJobs
        .filter((j) => {
          // India hiring filter — default: only confirmed
          if (!includeUnconfirmed && j.india_hiring !== "confirmed") return false;
          if (domain !== "all" && j.domain !== domain) return false;
          if (source !== "all" && j.source !== source) return false;
          if (workMode !== "all" && j.employment_type !== workMode) return false;
          if (knownOnly && !j.known_to_pranav) return false;
          if (!matchesDateRange(j.first_seen_at, createdFrom, createdTo)) return false;
          if (!matchesDateRange(j.updated_at, updatedFrom, updatedTo)) return false;
          return true;
        }),
    // Order is the SQL ORDER BY from getJobs (domain, then seniority ladder,
    // then recency) — kept as the single source of truth, not re-sorted here.
    [relevantJobs, includeUnconfirmed, domain, source, workMode, knownOnly, createdFrom, createdTo, updatedFrom, updatedTo],
  );

  useEffect(() => {
    setPage(1);
  }, [includeUnconfirmed, domain, source, workMode, knownOnly, createdFrom, createdTo, updatedFrom, updatedTo]);

  // Never sit on the Founder's Office view while that tab is still locked.
  useEffect(() => {
    if (domain === "strategy" && !foTabVisible) setDomain("pm");
  }, [domain, foTabVisible]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const paginated = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  return (
    <div className="min-h-screen bg-zinc-50 font-sans">
      <div className="max-w-7xl mx-auto px-4 py-10">

        {/* Header */}
        <div className="mb-6">
          <div className="flex items-baseline justify-between">
            <div>
              <h1 className="text-2xl font-semibold text-zinc-900">Product Manager & Founder's Office Roles</h1>
              <p className="text-xs text-zinc-400 mt-0.5">India · v0.1 PM + v0.4 Founder's Office / Chief of Staff · design/data coming later</p>
            </div>
            <span className="text-xs text-zinc-400">
              enriched {timeAgo(pipeline.lastEnrichedAt)} · added {timeAgo(pipeline.lastAddedAt)}
            </span>
          </div>

          {/* Pipeline stats bar */}
          <div className="mt-4 rounded-xl border border-zinc-200 bg-white px-5 py-4 grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <p className="text-2xl font-semibold text-zinc-900">{pipeline.total}</p>
              <p className="text-xs text-zinc-400 mt-0.5">total jobs</p>
            </div>
            <div>
              <p className="text-2xl font-semibold text-emerald-600">+{pipeline.addedToday}</p>
              <p className="text-xs text-zinc-400 mt-0.5">added today</p>
            </div>
            <div>
              <p className="text-2xl font-semibold text-blue-600">+{pipeline.addedThisWeek}</p>
              <p className="text-xs text-zinc-400 mt-0.5">this week</p>
            </div>
            <div>
              <p className="text-xs text-zinc-500 font-medium mb-1.5">by domain</p>
              <div className="flex flex-wrap gap-1">
                {pipeline.byDomain.map(({ domain: d, count }) => (
                  <span key={d} className="text-xs bg-zinc-100 text-zinc-600 rounded px-1.5 py-0.5">
                    {DOMAIN_LABELS_SHORT[d] ?? d} {count}
                  </span>
                ))}
              </div>
            </div>
          </div>

          {/* Sources row */}
          <div className="mt-2 flex flex-wrap gap-1.5 items-center">
            <span className="text-xs text-zinc-400">sources:</span>
            {pipeline.bySource.map(({ source: src, count }) => (
              <span key={src} className="text-xs bg-zinc-100 text-zinc-500 rounded-full px-2 py-0.5">
                {SOURCE_SHORT[src] ?? src} · {count}
              </span>
            ))}
            <span className="ml-auto text-xs text-zinc-400 flex gap-2">
              <a href="/review" className="hover:text-amber-600">
                {stats.pending} pending review
              </a>
            </span>
          </div>
        </div>

        {/* PM / Founder's Office tabs (v0.1 + v0.4 kept as separate boards) */}
        <div className="mb-5 flex gap-1 border-b border-zinc-200">
          <button
            onClick={() => setDomain("pm")}
            className={`px-4 py-2 text-sm font-medium -mb-px border-b-2 transition-colors ${
              domain === "pm"
                ? "border-zinc-900 text-zinc-900"
                : "border-transparent text-zinc-400 hover:text-zinc-600"
            }`}
          >
            Product Manager
          </button>
          {foTabVisible && (
            <button
              onClick={() => setDomain("strategy")}
              className={`px-4 py-2 text-sm font-medium -mb-px border-b-2 transition-colors ${
                domain === "strategy"
                  ? "border-zinc-900 text-zinc-900"
                  : "border-transparent text-zinc-400 hover:text-zinc-600"
              }`}
            >
              Founder&apos;s Office
            </button>
          )}
        </div>

        {/* Filters */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3 mb-6">
          <label className="text-xs text-zinc-500 flex flex-col gap-1">
            Source
            <select
              value={source}
              onChange={(e) => setSource(e.target.value)}
              className="text-sm border border-zinc-200 rounded-lg px-3 py-1.5 bg-white text-zinc-700 focus:outline-none focus:ring-2 focus:ring-zinc-900"
            >
              <option value="all">All sources</option>
              {Object.entries(SOURCE_LABELS).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
          </label>

          <label className="text-xs text-zinc-500 flex flex-col gap-1">
            Work mode
            <select
              value={workMode}
              onChange={(e) => setWorkMode(e.target.value)}
              className="text-sm border border-zinc-200 rounded-lg px-3 py-1.5 bg-white text-zinc-700 focus:outline-none focus:ring-2 focus:ring-zinc-900"
            >
              <option value="all">All work modes</option>
              <option value="remote">Remote</option>
              <option value="hybrid">Hybrid</option>
              <option value="onsite">Onsite</option>
            </select>
          </label>

          {/* "Known contacts only" filter hidden until LinkedIn feed scraper lands.
              State and SQL data still flow so re-enabling is one-line. */}

          <div className="text-xs text-zinc-500 flex items-end">
            <button
              onClick={() => setIncludeUnconfirmed(!includeUnconfirmed)}
              className={`text-sm rounded-lg px-3 py-1.5 border transition-colors ${
                includeUnconfirmed
                  ? "bg-amber-50 text-amber-700 border-amber-200"
                  : "bg-white text-zinc-700 border-zinc-200 hover:border-zinc-400"
              }`}
              title="Toggle to include remote roles where India hiring isn't yet confirmed"
            >
              {includeUnconfirmed ? "✓ Including unconfirmed" : "Include unconfirmed remote"}
            </button>
          </div>

          <label className="text-xs text-zinc-500 flex flex-col gap-1">
            Created from
            <input
              type="date"
              value={createdFrom}
              onChange={(e) => setCreatedFrom(e.target.value)}
              className="text-sm border border-zinc-200 rounded-lg px-3 py-1.5 bg-white text-zinc-700 focus:outline-none focus:ring-2 focus:ring-zinc-900"
            />
          </label>

          <label className="text-xs text-zinc-500 flex flex-col gap-1">
            Created to
            <input
              type="date"
              value={createdTo}
              onChange={(e) => setCreatedTo(e.target.value)}
              className="text-sm border border-zinc-200 rounded-lg px-3 py-1.5 bg-white text-zinc-700 focus:outline-none focus:ring-2 focus:ring-zinc-900"
            />
          </label>

          <label className="text-xs text-zinc-500 flex flex-col gap-1">
            Updated from
            <input
              type="date"
              value={updatedFrom}
              onChange={(e) => setUpdatedFrom(e.target.value)}
              className="text-sm border border-zinc-200 rounded-lg px-3 py-1.5 bg-white text-zinc-700 focus:outline-none focus:ring-2 focus:ring-zinc-900"
            />
          </label>

          <label className="text-xs text-zinc-500 flex flex-col gap-1">
            Updated to
            <input
              type="date"
              value={updatedTo}
              onChange={(e) => setUpdatedTo(e.target.value)}
              className="text-sm border border-zinc-200 rounded-lg px-3 py-1.5 bg-white text-zinc-700 focus:outline-none focus:ring-2 focus:ring-zinc-900"
            />
          </label>
        </div>
        <div className="mb-4 text-sm text-zinc-400">
          {filtered.length} result{filtered.length !== 1 ? "s" : ""}
        </div>

        {/* Job list */}
        <div className="bg-white rounded-xl border border-zinc-200 overflow-hidden">
          {filtered.length === 0 ? (
            <div className="py-16 text-center text-zinc-400 text-sm">
              No signals match your filters.
            </div>
          ) : (
            <>
              {/* ── Mobile cards (< md) ─────────────────────────────────── */}
              <div className="md:hidden divide-y divide-zinc-100">
                {paginated.map((job) => (
                  <a
                    key={job.id}
                    href={job.signal_url || undefined}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={`block px-4 py-3 transition-colors ${job.signal_url ? "hover:bg-zinc-50 active:bg-zinc-100 cursor-pointer" : "cursor-default"}`}
                    onClick={!job.signal_url ? (e) => e.preventDefault() : undefined}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <span className="font-medium text-zinc-800 text-sm leading-snug">{job.title}</span>
                          {job.urgency_signals?.length > 0 && (
                            <span className="text-xs bg-amber-50 text-amber-700 border border-amber-200 rounded px-1.5 py-0.5">urgent</span>
                          )}
                          {job.india_hiring === "unknown" && (
                            <span className="text-xs bg-amber-50 text-amber-600 border border-amber-200 rounded px-1.5 py-0.5">unconfirmed</span>
                          )}
                        </div>
                        <p className="text-xs text-zinc-500 mt-0.5 truncate">{job.company}{job.location ? ` · ${job.location}` : ""}</p>
                        {job.description_summary && (
                          <p className="text-xs text-zinc-400 mt-0.5 line-clamp-2">{job.description_summary}</p>
                        )}
                        <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
                          {job.employment_type && (
                            <span className={`text-xs border rounded px-1.5 py-0.5 ${WORK_MODE_STYLES[job.employment_type] ?? "bg-zinc-100 text-zinc-500 border-zinc-200"}`}>
                              {job.employment_type}
                            </span>
                          )}
                          <span className="text-xs bg-zinc-100 text-zinc-500 rounded px-2 py-0.5">
                            {SOURCE_LABELS[job.source] ?? job.source}
                          </span>
                        </div>
                      </div>
                      <span className="text-xs text-zinc-400 whitespace-nowrap shrink-0 pt-0.5">{timeAgo(job.post_date)}</span>
                    </div>
                  </a>
                ))}
              </div>

              {/* ── Desktop table (≥ md) ─────────────────────────────────── */}
              <div className="hidden md:block overflow-x-auto">
                <table className="w-full table-fixed text-sm">
                  <thead>
                    <tr className="border-b border-zinc-100 text-left text-xs text-zinc-400 uppercase tracking-wide">
                      <th className="w-10 px-2 py-3 font-medium">#</th>
                      <th className="w-52 px-2 py-3 font-medium">Role</th>
                      <th className="w-36 px-2 py-3 font-medium">Company</th>
                      <th className="w-24 px-2 py-3 font-medium">Location</th>
                      <th className="w-20 px-2 py-3 font-medium">Mode</th>
                      <th className="w-24 px-2 py-3 font-medium">Source</th>
                      <th className="w-24 px-2 py-3 font-medium">Created</th>
                      <th className="w-24 px-2 py-3 font-medium">Updated</th>
                      <th className="w-16 px-2 py-3 font-medium">When</th>
                    </tr>
                  </thead>
                  <tbody>
                    {paginated.map((job, i) => (
                      <tr
                        key={job.id}
                        onClick={() => job.signal_url && window.open(job.signal_url, "_blank", "noopener,noreferrer")}
                        className={`border-b border-zinc-50 transition-colors ${i === paginated.length - 1 ? "border-b-0" : ""} ${job.signal_url ? "hover:bg-zinc-50 cursor-pointer" : ""}`}
                      >
                        <td className="px-2 py-3 text-zinc-400 text-xs">
                          {(page - 1) * PAGE_SIZE + i + 1}
                        </td>
                        <td className="px-2 py-3">
                          <div className="flex items-center gap-1.5 flex-wrap">
                            <span className="text-zinc-700 truncate" title={job.description_summary ?? undefined}>
                              {job.title}
                            </span>
                            {job.urgency_signals?.length > 0 && (
                              <span className="text-xs bg-amber-50 text-amber-700 border border-amber-200 rounded px-1.5 py-0.5">urgent</span>
                            )}
                            {job.india_hiring === "unknown" && (
                              <span className="text-xs bg-amber-50 text-amber-600 border border-amber-200 rounded px-1.5 py-0.5" title="Remote role — India hiring not yet confirmed">unconfirmed</span>
                            )}
                          </div>
                          {job.description_summary && (
                            <p className="text-xs text-zinc-400 mt-0.5 line-clamp-1">{job.description_summary}</p>
                          )}
                        </td>
                        <td className="px-2 py-3 text-zinc-600 truncate">{job.company}</td>
                        <td className="px-2 py-3 text-zinc-500 truncate">{job.location}</td>
                        <td className="px-2 py-3">
                          {job.employment_type && (
                            <span className={`text-xs border rounded px-1.5 py-0.5 ${WORK_MODE_STYLES[job.employment_type] ?? "bg-zinc-100 text-zinc-500 border-zinc-200"}`}>
                              {job.employment_type}
                            </span>
                          )}
                        </td>
                        <td className="px-2 py-3">
                          <span className="text-xs bg-zinc-100 text-zinc-500 rounded px-2 py-0.5">
                            {SOURCE_LABELS[job.source] ?? job.source}
                          </span>
                        </td>
                        <td className="px-2 py-3 text-zinc-500 text-xs">{getDateOnly(job.first_seen_at) ?? "-"}</td>
                        <td className="px-2 py-3 text-zinc-500 text-xs">{getDateOnly(job.updated_at) ?? "-"}</td>
                        <td className="px-2 py-3 text-zinc-400 text-xs">{timeAgo(job.post_date)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>

        {totalPages > 1 && (
          <div className="mt-4 flex items-center justify-end gap-2">
            <button
              className="text-sm rounded border border-zinc-200 px-3 py-1.5 disabled:opacity-50"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
            >
              Prev
            </button>
            <span className="text-sm text-zinc-500">
              Page {page} / {totalPages}
            </span>
            <button
              className="text-sm rounded border border-zinc-200 px-3 py-1.5 disabled:opacity-50"
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page === totalPages}
            >
              Next
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
