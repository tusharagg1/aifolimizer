"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchAllSnapshots,
  fetchSchedulerStatus,
  refreshSnapshots,
  SkillSnapshot,
  SchedulerStatus,
} from "@/lib/api";

interface Options {
  enabled?: boolean;
  pollIntervalMs?: number;
}

/**
 * Polls /skills/snapshots + /skills/scheduler/status on a cadence.
 * Default 60s. Returns snapshots, scheduler state, and a manual refresh fn.
 */
export function useSkillSnapshots({
  enabled = true,
  pollIntervalMs = 60_000,
}: Options = {}) {
  const [snapshots, setSnapshots] = useState<SkillSnapshot[]>([]);
  const [scheduler, setScheduler] = useState<SchedulerStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mountedRef = useRef(true);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    if (!enabled) return;
    setLoading(true);
    try {
      const [snaps, sched] = await Promise.all([
        fetchAllSnapshots(),
        fetchSchedulerStatus(),
      ]);
      if (!mountedRef.current) return;
      setSnapshots(snaps.snapshots);
      setScheduler(sched);
      setError(null);
    } catch (e) {
      if (mountedRef.current) {
        setError(e instanceof Error ? e.message : "fetch failed");
      }
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [enabled]);

  const refresh = useCallback(async (skill?: string) => {
    try {
      await refreshSnapshots(skill);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "refresh failed");
    }
  }, [load]);

  useEffect(() => {
    mountedRef.current = true;
    if (!enabled) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
    const tick = () => {
      load();
      timerRef.current = setTimeout(tick, pollIntervalMs);
    };
    timerRef.current = setTimeout(tick, pollIntervalMs);
    return () => {
      mountedRef.current = false;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [enabled, load, pollIntervalMs]);

  return { snapshots, scheduler, loading, error, refresh };
}
