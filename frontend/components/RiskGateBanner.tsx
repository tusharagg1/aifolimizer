"use client";

import { memo, useState } from "react";
import type { RiskGateState } from "@/lib/api";
import { overrideRiskGate } from "@/lib/api";

interface RiskGateBannerProps {
  gate: RiskGateState | null;
  sessionId: string;
  onOverride?: () => void;
}

function RiskGateBannerImpl({
  gate, sessionId, onOverride,
}: RiskGateBannerProps) {
  const [busy, setBusy] = useState(false);

  if (!gate || gate.status === "trade") return null;

  const isHalt = gate.status === "halt";
  const colors = isHalt
    ? {
        border: "border-rose-500/40",
        bg: "bg-rose-500/10",
        text: "text-rose-200",
        sub: "text-rose-300/80",
        icon: "🛑",
      }
    : {
        border: "border-amber-500/40",
        bg: "bg-amber-500/10",
        text: "text-amber-200",
        sub: "text-amber-300/80",
        icon: "⚠",
      };

  const validUntil = new Date(gate.valid_until).toLocaleString();

  async function handleOverride() {
    const reason = window.prompt(
      "Override risk gate. Reason (logged):",
      "manual override",
    );
    if (!reason) return;
    try {
      setBusy(true);
      await overrideRiskGate(sessionId, reason, 24);
      onOverride?.();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className={`rounded-lg border ${colors.border} ${colors.bg} p-4 flex items-start gap-3`}
    >
      <span className="text-xl leading-none" aria-hidden>
        {colors.icon}
      </span>
      <div className="flex-1 space-y-1">
        <p className={`text-sm font-semibold ${colors.text}`}>
          Risk gate: {gate.status.toUpperCase()}
          {!isHalt && ` (size × ${gate.size_multiplier.toFixed(2)})`}
        </p>
        <p className={`text-xs ${colors.sub}`}>
          {isHalt
            ? "New BUY/ADD recommendations are suppressed. SELL/TRIM still active."
            : "New BUY/ADD position sizes are halved."}
        </p>
        <p className={`text-xs ${colors.sub}`}>
          Triggers: {gate.reasons.join(" · ")}
        </p>
        <p className={`text-xs ${colors.sub}`}>
          Auto-clears at {validUntil}
        </p>
      </div>
      <button
        type="button"
        onClick={handleOverride}
        disabled={busy}
        className={`px-3 py-1.5 rounded text-sm border ${colors.border} ${colors.text} hover:bg-white/5 disabled:opacity-50`}
      >
        {busy ? "…" : "Override"}
      </button>
    </div>
  );
}

export const RiskGateBanner = memo(RiskGateBannerImpl);
RiskGateBanner.displayName = "RiskGateBanner";
