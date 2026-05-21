import { neon } from "@neondatabase/serverless";

export type Job = {
  id: string;
  title: string;
  company: string;
  location: string;
  domain: string;
  employment_type: string | null;
  description_summary: string | null;
  posted_by: string;
  posted_by_profile_url: string | null;
  source: string;
  signal_url: string | null;
  post_date: string;
  urgency_signals: string[];
  known_to_pranav: boolean;
  first_seen_at: string;
  updated_at: string | null;
  india_hiring: "confirmed" | "unknown" | "rejected" | null;
};

export type JobStats = {
  approved: number;
  pending: number;
  rejected: number;
};

export type PipelineStats = {
  total: number;
  addedToday: number;
  addedThisWeek: number;
  lastEnrichedAt: string | null;
  lastAddedAt: string | null;
  bySource: { source: string; count: number }[];
  byDomain: { domain: string; count: number }[];
};

// No fake-data fallback. On a missing DATABASE_URL or a DB error the board
// shows an honest empty state — it must never serve seed rows that look
// like real listings (review finding H6).

export async function getJobs(): Promise<Job[]> {
  if (!process.env.DATABASE_URL) return [];

  try {
    const sql = neon(process.env.DATABASE_URL);
    const rows = await sql`
      SELECT
        j.id,
        COALESCE(j.norm_title, j.title) AS title,
        COALESCE(j.norm_company, j.company) AS company,
        COALESCE(j.norm_location_city, j.location, 'India') AS location,
        COALESCE(j.norm_function, j.domain) AS domain,
        COALESCE(j.norm_remote_type, j.employment_type) AS employment_type,
        j.description_summary,
        j.first_seen_at, j.updated_at,
        COALESCE(p.name, 'Unknown') AS posted_by,
        p.linkedin_url AS posted_by_profile_url,
        s.name AS source,
        sig.signal_url,
        sig.post_date,
        sig.urgency_signals,
        COALESCE(p.known_to_pranav, false) AS known_to_pranav,
        j.india_hiring
      FROM signals sig
      JOIN jobs j ON j.id = sig.job_id
      JOIN sources s ON s.id = sig.source_id
      LEFT JOIN people p ON p.id = sig.person_id
      WHERE sig.validated = true
        AND (
          (j.review_status = 'approved' AND j.india_hiring = 'confirmed')
          OR (j.review_status = 'pending' AND j.india_hiring = 'unknown')
        )
        -- v0.1 + v0.4: PM and Founder's Office / Chief of Staff
        AND COALESCE(j.norm_function, j.domain) IN ('pm', 'strategy')
      ORDER BY
        -- domain sort: PM first, then Founder's Office
        CASE COALESCE(j.norm_function, j.domain)
          WHEN 'pm' THEN 1
          ELSE 2
        END ASC,
        -- seniority sort (covers both PM and CoS/FO titles)
        CASE
          WHEN LOWER(COALESCE(j.norm_title, j.title)) SIMILAR TO '%(cpo|vp |vice president|head of|chief product|entrepreneur in residence| eir |chief of staff)%' THEN 1
          WHEN LOWER(COALESCE(j.norm_title, j.title)) SIMILAR TO '%(director)%'                         THEN 2
          WHEN LOWER(COALESCE(j.norm_title, j.title)) SIMILAR TO '%(staff |principal |group )%'         THEN 3
          WHEN LOWER(COALESCE(j.norm_title, j.title)) SIMILAR TO '%(senior |lead |sr\. | sr )%'         THEN 4
          WHEN LOWER(COALESCE(j.norm_title, j.title)) SIMILAR TO '%(associate |junior |jr\.)%'          THEN 6
          ELSE 5
        END ASC,
        sig.scraped_at DESC
      LIMIT 500
    `;
    return rows as Job[];
  } catch {
    return [];
  }
}

export async function getPipelineStats(): Promise<PipelineStats> {
  const empty: PipelineStats = {
    total: 0, addedToday: 0, addedThisWeek: 0,
    lastEnrichedAt: null, lastAddedAt: null,
    bySource: [], byDomain: [],
  };
  if (!process.env.DATABASE_URL) return empty;

  try {
    const sql = neon(process.env.DATABASE_URL);

    const [summary, sources, domains] = await Promise.all([
      sql`
        SELECT
          COUNT(*)::int                                                            AS total,
          COUNT(*) FILTER (WHERE first_seen_at > NOW() - INTERVAL '24 hours')::int AS added_today,
          COUNT(*) FILTER (WHERE first_seen_at > NOW() - INTERVAL '7 days')::int   AS added_this_week,
          MAX(last_enriched_at)                                                    AS last_enriched_at,
          MAX(first_seen_at)                                                       AS last_added_at
        FROM jobs
        WHERE review_status = 'approved'
      `,
      sql`
        SELECT s.name AS source, COUNT(DISTINCT sig.job_id)::int AS count
        FROM signals sig
        JOIN sources s  ON s.id  = sig.source_id
        JOIN jobs j     ON j.id  = sig.job_id
        WHERE j.review_status = 'approved'
        GROUP BY s.name
        ORDER BY count DESC
      `,
      sql`
        SELECT COALESCE(norm_function, domain) AS domain, COUNT(*)::int AS count
        FROM jobs
        WHERE review_status = 'approved'
          AND COALESCE(norm_function, domain) IN ('pm','design','data','strategy')
        GROUP BY COALESCE(norm_function, domain)
        ORDER BY count DESC
      `,
    ]);

    const s = summary[0] as Record<string, unknown>;
    return {
      total:          s.total          as number,
      addedToday:     s.added_today    as number,
      addedThisWeek:  s.added_this_week as number,
      lastEnrichedAt: s.last_enriched_at ? String(s.last_enriched_at) : null,
      lastAddedAt:    s.last_added_at   ? String(s.last_added_at)    : null,
      bySource: sources as { source: string; count: number }[],
      byDomain: domains as { domain: string; count: number }[],
    };
  } catch {
    return empty;
  }
}

export type RecentJob = {
  id: string;
  title: string;
  company: string;
  domain: string;
  source: string;
  first_seen_at: string;
  last_enriched_at: string | null;
  review_status: string;
  has_summary: boolean;
};

export type EnrichedJob = {
  id: string;
  // raw (as scraped)
  raw_title: string;
  raw_company: string;
  raw_location: string | null;
  raw_employment_type: string | null;
  // normalised (filled by enrichment)
  norm_title: string | null;
  norm_company: string | null;
  norm_location_city: string | null;
  norm_remote_type: string | null;
  description_summary: string | null;
  last_enriched_at: string;
  review_status: string;
};

export type SourceActivity = {
  source: string;
  total: number;
  added_24h: number;
  added_7d: number;
  last_seen_at: string | null;
};

export type StatusFeed = {
  fetchedAt: string;
  approved: number;
  pending: number;
  needsEnrichment: number;
  lastEnrichedAt: string | null;
  lastAddedAt: string | null;
  recentJobs: RecentJob[];           // added in last 48h
  enrichedJobs: EnrichedJob[];       // enriched in last 48h, newest first
  sourceActivity: SourceActivity[];
};

export async function getStatusFeed(): Promise<StatusFeed> {
  const empty: StatusFeed = {
    fetchedAt: new Date().toISOString(),
    approved: 0, pending: 0, needsEnrichment: 0,
    lastEnrichedAt: null, lastAddedAt: null,
    recentJobs: [], enrichedJobs: [], sourceActivity: [],
  };
  if (!process.env.DATABASE_URL) return empty;

  try {
    const sql = neon(process.env.DATABASE_URL);

    const [summary, recent, enriched, activity] = await Promise.all([
      sql`
        SELECT
          COUNT(*) FILTER (WHERE review_status = 'approved')::int   AS approved,
          COUNT(*) FILTER (WHERE review_status = 'pending')::int    AS pending,
          COUNT(*) FILTER (
            WHERE review_status = 'approved'
              AND last_enriched_at IS NULL
          )::int                                                     AS needs_enrichment,
          MAX(last_enriched_at)                                      AS last_enriched_at,
          MAX(first_seen_at)                                         AS last_added_at
        FROM jobs
      `,
      sql`
        SELECT
          j.id,
          COALESCE(j.norm_title, j.title)     AS title,
          COALESCE(j.norm_company, j.company) AS company,
          COALESCE(j.norm_function, j.domain) AS domain,
          s.name                              AS source,
          j.first_seen_at,
          j.last_enriched_at,
          j.review_status,
          (j.description_summary IS NOT NULL) AS has_summary
        FROM jobs j
        JOIN signals sig ON sig.job_id = j.id
        JOIN sources s   ON s.id = sig.source_id
        WHERE j.first_seen_at > NOW() - INTERVAL '48 hours'
        ORDER BY j.first_seen_at DESC
        LIMIT 60
      `,
      sql`
        SELECT
          id,
          title         AS raw_title,
          company       AS raw_company,
          location      AS raw_location,
          employment_type AS raw_employment_type,
          norm_title,
          norm_company,
          norm_location_city,
          norm_remote_type,
          description_summary,
          last_enriched_at,
          review_status
        FROM jobs
        WHERE last_enriched_at > NOW() - INTERVAL '48 hours'
        ORDER BY last_enriched_at DESC
        LIMIT 80
      `,
      sql`
        SELECT
          s.name                                                          AS source,
          COUNT(DISTINCT sig.job_id)::int                                 AS total,
          COUNT(DISTINCT sig.job_id) FILTER (
            WHERE j.first_seen_at > NOW() - INTERVAL '24 hours'
          )::int                                                          AS added_24h,
          COUNT(DISTINCT sig.job_id) FILTER (
            WHERE j.first_seen_at > NOW() - INTERVAL '7 days'
          )::int                                                          AS added_7d,
          MAX(sig.scraped_at)                                             AS last_seen_at
        FROM signals sig
        JOIN sources s ON s.id = sig.source_id
        JOIN jobs j    ON j.id = sig.job_id
        GROUP BY s.name
        ORDER BY added_24h DESC, total DESC
      `,
    ]);

    const sv = summary[0] as Record<string, unknown>;
    return {
      fetchedAt: new Date().toISOString(),
      approved:        sv.approved         as number,
      pending:         sv.pending          as number,
      needsEnrichment: sv.needs_enrichment as number,
      lastEnrichedAt:  sv.last_enriched_at ? String(sv.last_enriched_at) : null,
      lastAddedAt:     sv.last_added_at    ? String(sv.last_added_at)    : null,
      recentJobs:     recent      as RecentJob[],
      enrichedJobs:   enriched    as EnrichedJob[],
      sourceActivity: activity    as SourceActivity[],
    };
  } catch (e) {
    console.error("getStatusFeed error", e);
    return empty;
  }
}

export async function getJobStats(): Promise<JobStats> {
  if (!process.env.DATABASE_URL) {
    return { approved: 0, pending: 0, rejected: 0 };
  }

  try {
    const sql = neon(process.env.DATABASE_URL);
    const rows = await sql`
      SELECT review_status, COUNT(*)::int AS count
      FROM jobs
      GROUP BY review_status
    `;

    const stats: JobStats = { approved: 0, pending: 0, rejected: 0 };
    for (const row of rows as Array<{ review_status: string; count: number }>) {
      if (row.review_status === "approved") stats.approved = row.count;
      if (row.review_status === "pending") stats.pending = row.count;
      if (row.review_status === "rejected") stats.rejected = row.count;
    }
    return stats;
  } catch {
    return { approved: 0, pending: 0, rejected: 0 };
  }
}
