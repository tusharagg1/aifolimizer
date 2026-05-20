"use client";

import { useEffect, useRef, useCallback } from "react";
import type { PortfolioSummary, HealthScore } from "@/lib/api";

const WS_BASE =
  (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000")
    .replace(/^http/, "ws");

interface StreamFrame {
  type: "portfolio_update" | "error";
  summary?: PortfolioSummary;
  health?: HealthScore;
  position_count?: number;
  detail?: string;
}

interface Options {
  sessionId: string | null;
  onUpdate: (summary: PortfolioSummary, health: HealthScore) => void;
  onSessionExpired: () => void;
  enabled?: boolean;
}

export function usePortfolioStream({
  sessionId,
  onUpdate,
  onSessionExpired,
  enabled = true,
}: Options): { reconnect: () => void } {
  const wsRef = useRef<WebSocket | null>(null);
  const retriesRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);
  const connectRef = useRef<() => void>(() => {});

  const connect = useCallback(() => {
    if (!sessionId || !mountedRef.current || !enabled) return;

    const url = `${WS_BASE}/ws/stream?session_id=${encodeURIComponent(sessionId)}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onmessage = (ev) => {
      try {
        const frame: StreamFrame = JSON.parse(ev.data);
        if (frame.type === "portfolio_update" && frame.summary && frame.health) {
          retriesRef.current = 0;
          onUpdate(frame.summary, frame.health);
        } else if (frame.type === "error" && frame.detail === "session_expired") {
          onSessionExpired();
        }
      } catch {
        // malformed frame — ignore
      }
    };

    ws.onclose = () => {
      if (!mountedRef.current) return;
      const delay = Math.min(2000 * 2 ** retriesRef.current, 30_000);
      retriesRef.current += 1;
      timerRef.current = setTimeout(() => connectRef.current(), delay);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [sessionId, onUpdate, onSessionExpired, enabled]);

  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      if (timerRef.current) clearTimeout(timerRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  const reconnect = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    wsRef.current?.close();
    retriesRef.current = 0;
    connect();
  }, [connect]);

  return { reconnect };
}
