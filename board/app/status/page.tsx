import { getStatusFeed, type EnrichedJob } from "@/lib/jobs";
import { StatusRefresher } from "@/components/StatusRefresher";

export const dynamic = "force-dynamic";

const SOURCE_LABELS: Record<string, string> = {
  linkedin_feed:    "LinkedIn Feed",
  linkedin_jobs:    "LinkedIn Jobs",
  web_search:       "Web Search",
  jobspy:           "JobSpy",
  hn_who_is_hiring: "HN Hiring",
  greenhouse:       "Greenhouse",
  lever:            "Lever",
  telegram:         "Telegram",
  manual:           "Manual",
};

const DOMAIN_COLORS: Record<string, string> = {
  pm:       "bg-violet-100 text-violet-700",
  design:   "bg-pink-100 text-pink-700",
  data:     "bg-blue-100 text-blue-700",
  strategy: "bg-amber-100 text-amber-700",
  other:    "bg-zinc-100 text-zinc-500",
};

function timeAgo(dateStr: string | null | undefined) {
  if (!dateStr) return "—";
  const diff = Date.now() - new Date(dateStr).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

// Shows a before→after field diff. Hides row if both are null.
function FieldRow({
  label,
  raw,
  norm,
}: {
  label: string;
  raw: string | null;
  norm: string | null;
}) {
  if (!raw && !norm) return null;
  const changed = norm && norm !== raw;
  return (
    <div className="flex items-start gap-2 text-xs">
      <span className="w-20 shrink-0 text-zinc-400 pt-px">{label}</span>
      <div className="flex items-center gap-1.5 flex-wrap">
        {raw && (
          <span className={`font-mono px-1.5 py-0.5 rounded ${changed ? "line-through text-zinc-400 bg-zinc-50" : "text-zinc-700 bg-zinc-100"}`}>
            {raw}
          </span>
        )}
        {changed && (
          <>
            <span className="text-zinc-300">→</span>
            <span className="font-mono px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-700">{norm}</span>
          </>
        )}
      </div>
    </div>
  );
}

function EnrichCard({ job }: { job: EnrichedJob }) {
  const fields: { label: string; filled: boolean }[] = [
    { label: "title",    filled: !!job.norm_title },
    { label: "company",  filled: !!job.norm_company },
    { label: "location", filled: !!job.norm_location_city },
    { label: "mode",     filled: !!job.norm_remote_type },
    { label: "summary",  filled: !!job.description_summary },
  ];
  const filledCount = fields.filter((f) => f.filled).length;

  return (
    <div className="bg-white border border-zinc-200 rounded-lg p-4 space-y-3">
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-semibold truncate">
            {job.norm_title ?? job.raw_title}
          </div>
          <div className="text-xs text-zinc-400 truncate">
            {job.norm_company ?? job.raw_company}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {/* filled count badge */}
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
            filledCount === 5 ? "bg-emerald-100 text-emerald-700" :
            filledCount >= 3  ? "bg-blue-100 text-blue-700"       :
                                "bg-amber-100 text-amber-700"
          }`}>
            {filledCount}/5 fields
          </span>
          <span
            className={`text-xs px-1.5 py-0.5 rounded border ${
              job.review_status === "approved" ? "border-emerald-200 text-emerald-600" :
              job.review_status === "pending"  ? "border-amber-200 text-amber-600"    :
                                                 "border-red-200 text-red-500"
            }`}
          >
            {job.review_status}
          </span>
          <span className="text-xs text-zinc-400">{timeAgo(job.last_enriched_at)}</span>
        </div>
      </div>

      {/* Field diffs */}
      <div className="space-y-1.5 border-t border-zinc-100 pt-2">
        <FieldRow label="title"    raw={job.raw_title}           norm={job.norm_title} />
        <FieldRow label="company"  raw={job.raw_company}         norm={job.norm_company} />
        <FieldRow label="location" raw={job.raw_location}        norm={job.norm_location_city} />
        <FieldRow label="mode"     raw={job.raw_employment_type} norm={job.norm_remote_type} />
        {job.description_summary && (
          <div className="flex items-start gap-2 text-xs pt-0.5">
            <span className="w-20 shrink-0 text-zinc-400">summary</span>
            <span className="text-zinc-600 leading-relaxed line-clamp-3">
              {job.description_summary}
            </span>
          </div>
        )}
        {!job.description_summary && (
          <div className="flex items-start gap-2 text-xs">
            <span className="w-20 shrink-0 text-zinc-400">summary</span>
            <span className="text-zinc-300 italic">not extracted</span>
          </div>
        )}
      </div>
    </div>
  );
}

export default async function StatusPage() {
  const feed = await getStatusFeed();

  const healthColor =
    feed.needsEnrichment === 0 ? "text-emerald-600" :
    feed.needsEnrichment < 20  ? "text-amber-600"   : "text-red-500";

  return (
    <div className="min-h-screen bg-zinc-50 text-zinc-800 font-sans">
      {/* Header */}
      <header className="bg-white border-b border-zinc-200 px-6 py-4">
        <div className="max-w-5xl mx-auto flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold">Pipeline Status</h1>
            <p className="text-xs text-zinc-400 mt-0.5">What ran, what got enriched, what's waiting</p>
          </div>
          <div className="flex items-center gap-4">
            <a href="/" className="text-xs text-zinc-400 hover:text-zinc-600 underline underline-offset-2">
              ← Board
            </a>
            <StatusRefresher fetchedAt={feed.fetchedAt} />
          </div>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-8 space-y-10">

        {/* ── Pulse ── */}
        <section>
          <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-400 mb-3">Pulse</h2>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <StatCard label="Approved" value={feed.approved} color="text-emerald-600" />
            <StatCard label="Pending review" value={feed.pending}
              color={feed.pending > 0 ? "text-amber-600" : "text-zinc-400"} />
            <StatCard label="Not enriched" value={feed.needsEnrichment} color={healthColor} />
            <div className="bg-white border border-zinc-200 rounded-lg p-4 space-y-1">
              <div className="text-xs text-zinc-400">Last enriched</div>
              <div className="text-sm font-medium">{timeAgo(feed.lastEnrichedAt)}</div>
              <div className="text-xs text-zinc-400 mt-1">Last added</div>
              <div className="text-sm font-medium">{timeAgo(feed.lastAddedAt)}</div>
            </div>
          </div>
        </section>

        {/* ── Enriched in last 48h ── */}
        <section>
          <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-400 mb-1">
            Enriched in last 48h
          </h2>
          <p className="text-xs text-zinc-400 mb-3">
            {feed.enrichedJobs.length} jobs · strikethrough = raw value replaced · green = what was filled in
          </p>
          {feed.enrichedJobs.length === 0 ? (
            <div className="bg-white border border-zinc-200 rounded-lg px-4 py-8 text-sm text-zinc-400 text-center">
              No enrichment runs in the last 48 hours
            </div>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              {feed.enrichedJobs.map((job) => (
                <EnrichCard key={job.id} job={job} />
              ))}
            </div>
          )}
        </section>

        {/* ── Source activity ── */}
        <section>
          <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-400 mb-3">Sources</h2>
          <div className="bg-white border border-zinc-200 rounded-lg divide-y divide-zinc-100">
            {feed.sourceActivity.length === 0 && (
              <div className="px-4 py-6 text-sm text-zinc-400 text-center">No source data yet</div>
            )}
            {feed.sourceActivity.map((s) => (
              <div key={s.source} className="px-4 py-3 flex items-center justify-between gap-4">
                <div className="text-sm font-medium truncate">
                  {SOURCE_LABELS[s.source] ?? s.source}
                </div>
                <div className="flex items-center gap-3 shrink-0 text-xs text-zinc-500">
                  <span className={`font-semibold tabular-nums ${s.added_24h > 0 ? "text-emerald-600" : "text-zinc-300"}`}>
                    +{s.added_24h} today
                  </span>
                  <span className={`tabular-nums ${s.added_7d > 0 ? "text-blue-600" : "text-zinc-300"}`}>
                    +{s.added_7d} wk
                  </span>
                  <span className="text-zinc-300 tabular-nums">{s.total} total</span>
                  <span className="text-zinc-400 w-16 text-right">{timeAgo(s.last_seen_at)}</span>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* ── Added in last 48h ── */}
        <section>
          <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-400 mb-3">
            Added in last 48h
            <span className="ml-2 text-zinc-300 normal-case tracking-normal font-normal">
              ({feed.recentJobs.length} jobs)
            </span>
          </h2>
          <div className="bg-white border border-zinc-200 rounded-lg divide-y divide-zinc-100">
            {feed.recentJobs.length === 0 && (
              <div className="px-4 py-6 text-sm text-zinc-400 text-center">No jobs in the last 48 hours</div>
            )}
            {feed.recentJobs.map((job) => (
              <div key={job.id} className="px-4 py-2.5 flex items-center gap-3">
                <span
                  title={job.review_status}
                  className={`shrink-0 w-2 h-2 rounded-full ${
                    job.review_status === "approved"  ? "bg-emerald-400" :
                    job.review_status === "pending"   ? "bg-amber-400"   : "bg-red-400"
                  }`}
                />
                <div className="flex-1 min-w-0">
                  <span className="text-sm font-medium truncate block">{job.title}</span>
                  <span className="text-xs text-zinc-400 truncate block">{job.company}</span>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className={`text-xs px-2 py-0.5 rounded font-medium ${DOMAIN_COLORS[job.domain] ?? "bg-zinc-100 text-zinc-500"}`}>
                    {job.domain}
                  </span>
                  <span className="text-xs text-zinc-400 w-20 text-right truncate">
                    {SOURCE_LABELS[job.source] ?? job.source}
                  </span>
                  <span
                    title={job.last_enriched_at ? `Enriched ${timeAgo(job.last_enriched_at)}` : "Not enriched"}
                    className={`text-xs w-4 text-center ${job.has_summary ? "text-emerald-500" : "text-zinc-300"}`}
                  >
                    {job.has_summary ? "✓" : "○"}
                  </span>
                  <span className="text-xs text-zinc-400 w-14 text-right tabular-nums">
                    {timeAgo(job.first_seen_at)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </section>

      </main>
    </div>
  );
}

function StatCard({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="bg-white border border-zinc-200 rounded-lg p-4">
      <div className="text-xs text-zinc-400 mb-1">{label}</div>
      <div className={`text-2xl font-bold tabular-nums ${color}`}>{value}</div>
    </div>
  );
}
