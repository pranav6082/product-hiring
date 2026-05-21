import { JobBoard } from "@/components/JobBoard";
import { getJobs, getJobStats, getPipelineStats } from "@/lib/jobs";

export const dynamic = "force-dynamic";

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<{ domain?: string; unconfirmed?: string }>;
}) {
  const params = await searchParams;
  const [jobs, stats, pipeline] = await Promise.all([
    getJobs(),
    getJobStats(),
    getPipelineStats(),
  ]);
  return (
    <JobBoard
      jobs={jobs}
      stats={stats}
      pipeline={pipeline}
      initialDomain={params.domain === "strategy" ? "strategy" : "pm"}
      initialUnconfirmed={params.unconfirmed === "1"}
    />
  );
}
