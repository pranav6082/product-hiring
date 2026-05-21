import { neon } from "@neondatabase/serverless";
import { revalidatePath } from "next/cache";

export const dynamic = "force-dynamic";

type ReviewRow = {
  id: string;
  title: string;
  company: string;
  location: string;
  source: string;
  normalization_confidence: number;
  signal_url: string | null;
  first_seen_at: string;
};

async function updateReviewStatus(formData: FormData) {
  "use server";
  const jobId = String(formData.get("jobId") ?? "");
  const status = String(formData.get("status") ?? "");
  if (!process.env.DATABASE_URL || !jobId || !["approved", "rejected"].includes(status)) return;

  const sql = neon(process.env.DATABASE_URL);
  await sql`
    UPDATE jobs
    SET review_status = ${status},
        needs_review = false,
        reviewed_at = NOW(),
        reviewed_by = 'pranav'
    WHERE id = ${jobId}
  `;
  revalidatePath("/");
  revalidatePath("/review");
}

async function getPendingRows(): Promise<ReviewRow[]> {
  if (!process.env.DATABASE_URL) return [];
  const sql = neon(process.env.DATABASE_URL);
  const rows = await sql`
    SELECT
      j.id,
      COALESCE(j.norm_title, j.title) AS title,
      COALESCE(j.norm_company, j.company) AS company,
      COALESCE(j.norm_location_city, j.location, 'India') AS location,
      s.name AS source,
      COALESCE(j.normalization_confidence, 0) AS normalization_confidence,
      sig.signal_url,
      j.first_seen_at
    FROM jobs j
    JOIN signals sig ON sig.job_id = j.id
    JOIN sources s ON s.id = sig.source_id
    WHERE j.review_status = 'pending'
    ORDER BY j.normalization_confidence ASC, sig.scraped_at DESC
    LIMIT 100
  `;
  return rows as ReviewRow[];
}

export default async function ReviewPage() {
  const rows = await getPendingRows();
  return (
    <div className="min-h-screen bg-zinc-50 font-sans">
      <div className="max-w-5xl mx-auto px-4 py-10">
        <h1 className="text-2xl font-semibold text-zinc-900">Review Queue</h1>
        <p className="text-sm text-zinc-500 mt-1">
          {rows.length} low-confidence jobs pending review
        </p>

        <div className="mt-6 bg-white rounded-xl border border-zinc-200 overflow-hidden">
          {rows.length === 0 ? (
            <div className="py-16 text-center text-zinc-400 text-sm">No pending jobs.</div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-zinc-100 text-left text-xs text-zinc-400 uppercase tracking-wide">
                  <th className="px-3 py-2">Role</th>
                  <th className="px-3 py-2">Company</th>
                  <th className="px-3 py-2">Location</th>
                  <th className="px-3 py-2">Source</th>
                  <th className="px-3 py-2">Confidence</th>
                  <th className="px-3 py-2">Action</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id} className="border-b border-zinc-50">
                    <td className="px-3 py-2 text-zinc-700">{row.title}</td>
                    <td className="px-3 py-2 text-zinc-600">{row.company}</td>
                    <td className="px-3 py-2 text-zinc-600">{row.location}</td>
                    <td className="px-3 py-2 text-zinc-500">{row.source}</td>
                    <td className="px-3 py-2 text-zinc-500">{row.normalization_confidence}</td>
                    <td className="px-3 py-2">
                      <div className="flex items-center gap-2">
                        {row.signal_url && (
                          <a
                            href={row.signal_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-xs text-zinc-500 hover:text-zinc-900"
                          >
                            View
                          </a>
                        )}
                        <form action={updateReviewStatus}>
                          <input type="hidden" name="jobId" value={row.id} />
                          <input type="hidden" name="status" value="approved" />
                          <button className="text-xs border border-emerald-200 text-emerald-700 rounded px-2 py-1">
                            Approve
                          </button>
                        </form>
                        <form action={updateReviewStatus}>
                          <input type="hidden" name="jobId" value={row.id} />
                          <input type="hidden" name="status" value="rejected" />
                          <button className="text-xs border border-zinc-200 text-zinc-600 rounded px-2 py-1">
                            Reject
                          </button>
                        </form>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
