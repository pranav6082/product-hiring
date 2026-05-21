"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

const INTERVAL_MS = 15 * 60 * 1000; // 15 minutes

export function StatusRefresher({ fetchedAt }: { fetchedAt: string }) {
  const router = useRouter();
  const [secsLeft, setSecsLeft] = useState(INTERVAL_MS / 1000);

  useEffect(() => {
    const deadline = Date.now() + INTERVAL_MS;

    const tick = setInterval(() => {
      const remaining = Math.max(0, Math.round((deadline - Date.now()) / 1000));
      setSecsLeft(remaining);
      if (remaining === 0) {
        clearInterval(tick);
        router.refresh();
      }
    }, 1000);

    return () => clearInterval(tick);
  }, [router]);

  const mins = Math.floor(secsLeft / 60);
  const secs = secsLeft % 60;
  const countdown = mins > 0
    ? `${mins}m ${String(secs).padStart(2, "0")}s`
    : `${secs}s`;

  const fetched = new Date(fetchedAt);
  const timeStr = fetched.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  return (
    <div className="flex items-center gap-3 text-xs text-zinc-400">
      <span>Updated {timeStr}</span>
      <span className="text-zinc-300">·</span>
      <span className="flex items-center gap-1">
        <span className="inline-block w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
        refreshes in {countdown}
      </span>
    </div>
  );
}
