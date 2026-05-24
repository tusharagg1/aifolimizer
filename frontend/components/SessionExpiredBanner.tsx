"use client";

import { memo } from "react";
import type { SkillSnapshot } from "@/lib/api";

interface SessionExpiredBannerProps {
  snapshots: SkillSnapshot[] | undefined;
  onReauth?: () => void;
}

/** Render a yellow banner when ≥1 portfolio-dependent skill snapshot
 * is tagged status='session_expired'. Falls through to null otherwise.
 */
function SessionExpiredBannerImpl({
  snapshots,
  onReauth,
}: SessionExpiredBannerProps) {
  if (!snapshots) return null;
  const expired = snapshots.filter((s) => s.status === "session_expired");
  if (expired.length === 0) return null;

  return (
    <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 flex items-start gap-3">
      <span className="text-amber-400 text-xl leading-none" aria-hidden>
        ⚠
      </span>
      <div className="flex-1 space-y-1">
        <p className="text-sm font-semibold text-amber-200">
          Wealthsimple session expired
        </p>
        <p className="text-xs text-amber-300/80">
          Portfolio-dependent skills are paused. Sign in again to resume:
          health, risk, recommendations, cash-deployment, tax-loss review,
          dividend strategy, earnings analyzer. Market/macro signals still
          updating.
        </p>
        <p className="text-xs text-amber-300/60">
          Skills affected: {expired.map((s) => s.skill).join(", ")}
        </p>
      </div>
      {onReauth && (
        <button
          type="button"
          onClick={onReauth}
          className="px-3 py-1.5 rounded bg-amber-500/20 text-amber-100 text-sm hover:bg-amber-500/30 border border-amber-500/40"
        >
          Re-authenticate
        </button>
      )}
    </div>
  );
}

export const SessionExpiredBanner = memo(SessionExpiredBannerImpl);
SessionExpiredBanner.displayName = "SessionExpiredBanner";
