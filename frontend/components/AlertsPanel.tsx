"use client";

import { Alert } from "@/lib/api";
import { memo, useState } from "react";

const SEVERITY: Record<
  string,
  { icon: string; cls: string }
> = {
  high: {
    icon: "▲",
    cls: "text-rose-400 border-rose-400/30 bg-rose-400/5",
  },
  warning: {
    icon: "◆",
    cls: "text-amber-400 border-amber-400/30 bg-amber-400/5",
  },
  info: {
    icon: "●",
    cls: "text-blue-400 border-blue-400/30 bg-blue-400/5",
  },
};

interface Props {
  alerts: Alert[];
  loading: boolean;
}

function AlertsPanel({ alerts, loading }: Props) {
  const [dismissed, setDismissed] = useState<Set<number>>(new Set());

  if (loading || !alerts.length) return null;

  const visible = alerts.filter((_, i) => !dismissed.has(i));
  if (!visible.length) return null;

  const dismiss = (i: number) =>
    setDismissed((prev) => new Set([...prev, i]));

  return (
    <div className="space-y-2">
      {visible.map((alert, i) => {
        const cfg = SEVERITY[alert.severity] ?? SEVERITY.info;
        return (
          <div
            key={i}
            className={`flex items-start gap-3 p-3 rounded-lg border ${cfg.cls}`}
          >
            <span className="text-xs mt-0.5 opacity-70">{cfg.icon}</span>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium leading-snug">{alert.title}</p>
              {alert.detail && (
                <p className="text-xs opacity-70 mt-0.5">{alert.detail}</p>
              )}
            </div>
            <button
              onClick={() => dismiss(i)}
              className="text-slate-600 hover:text-slate-400 text-xs flex-shrink-0"
            >
              ✕
            </button>
          </div>
        );
      })}
    </div>
  );
}

export default memo(AlertsPanel);
