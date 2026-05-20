"use client";

import { memo, useEffect, useRef, useState } from "react";
import {
  fetchTrustDecay,
  fetchTrustAttribution,
  fetchTrustCalibration,
  fetchTrustTrackRecord,
  DecayCurve,
  AttributionReport,
  CalibrationReport,
  TrackRecord,
} from "@/lib/api";

const VERDICT_COLOR: Record<string, string> = {
  calibrated: "text-emerald-400 border-emerald-500/30 bg-emerald-500/10",
  weakly_calibrated: "text-amber-400 border-amber-500/30 bg-amber-500/10",
  uncalibrated: "text-rose-400 border-rose-500/30 bg-rose-500/10",
  insufficient_data: "text-slate-400 border-slate-700 bg-slate-800/40",
  carrying_alpha: "text-emerald-400",
  marginal_alpha: "text-amber-400",
  noise_or_anti_signal: "text-rose-400",
  neutral: "text-slate-400",
  no_data: "text-slate-500",
};

function num(n: number | null | undefined, suffix = "%"): string {
  if (n === null || n === undefined) return "—";
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}${suffix}`;
}

function TrustDashboardPanel() {
  const [decay, setDecay] = useState<DecayCurve | null>(null);
  const [attribution, setAttribution] = useState<AttributionReport | null>(null);
  const [calibration, setCalibration] = useState<CalibrationReport | null>(null);
  const [track, setTrack] = useState<TrackRecord | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [horizon, setHorizon] = useState<number>(21);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    const controller = new AbortController();
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    Promise.all([
      fetchTrustDecay(undefined, controller.signal).catch(() => null),
      fetchTrustAttribution(horizon, controller.signal).catch(() => null),
      fetchTrustCalibration(horizon, controller.signal).catch(() => null),
      fetchTrustTrackRecord(controller.signal).catch(() => null),
    ])
      .then(([d, a, c, t]) => {
        if (!mountedRef.current) return;
        setDecay(d);
        setAttribution(a);
        setCalibration(c);
        setTrack(t);
        setError(null);
      })
      .catch((e) => {
        if (mountedRef.current) {
          setError(e instanceof Error ? e.message : "fetch failed");
        }
      })
      .finally(() => {
        if (mountedRef.current) setLoading(false);
      });
    return () => {
      mountedRef.current = false;
      controller.abort();
    };
  }, [horizon]);

  const decayInsufficient = !decay || decay.error || (decay.peak_horizon === null);
  const attrInsufficient = !attribution || attribution.error || attribution.n_total_scored < 5;
  const calibInsufficient = !calibration || calibration.error || calibration.verdict === "insufficient_data";

  return (
    <div className="border border-slate-800/60 bg-slate-900/20 rounded-xl p-4 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-slate-200">Trust Dashboard</h3>
          <p className="text-xs text-slate-500 mt-0.5">
            Signal accuracy evidence — empirical, derived from logged decisions.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-xs text-slate-500">Horizon</label>
          <select
            value={horizon}
            onChange={(e) => setHorizon(Number(e.target.value))}
            className="text-xs bg-slate-800 border border-slate-700 text-slate-200 rounded px-2 py-1"
          >
            {[1, 3, 5, 10, 21, 42, 63].map((h) => (
              <option key={h} value={h}>{h}d</option>
            ))}
          </select>
        </div>
      </div>

      {error && (
        <div className="text-xs text-rose-400 bg-rose-500/10 border border-rose-500/20 rounded px-3 py-2">
          {error}
        </div>
      )}

      {/* Track record */}
      <div>
        <div className="text-[10px] uppercase tracking-wide text-slate-500 mb-1">
          Forward-Scored Track Record
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          {track?.windows && Object.entries(track.windows).map(([win, w]) => (
            <div key={win} className="border border-slate-800/60 rounded p-2">
              <div className="text-xs text-slate-400">{win}</div>
              <div className="text-sm text-slate-100 font-medium mt-0.5">
                {w.count === 0 ? "no data" : `${w.count ?? 0} recs`}
              </div>
              <div className="text-xs text-slate-300">
                win {w.win_rate_pct ?? "—"}%, ret {num(w.avg_return_pct)}
              </div>
              {w.avg_alpha_vs_spy_pct !== undefined && (
                <div className="text-xs text-slate-400">
                  α vs SPY {num(w.avg_alpha_vs_spy_pct)}
                </div>
              )}
            </div>
          ))}
        </div>
        {track?.total_logged !== undefined && (
          <div className="text-[10px] text-slate-500 mt-1">
            Total logged: {track.total_logged} · model {track.model_version}
          </div>
        )}
      </div>

      {/* Calibration */}
      <div>
        <div className="text-[10px] uppercase tracking-wide text-slate-500 mb-1">
          Confidence Calibration ({horizon}d)
        </div>
        {calibInsufficient ? (
          <div className="text-xs text-slate-500 px-2 py-3 bg-slate-900/40 rounded border border-slate-800/60">
            Not enough scored signals to calibrate. Confidence label is unproven — treat as experimental.
          </div>
        ) : (
          <div className="flex items-center gap-3">
            <span className={`text-xs px-2 py-1 rounded border ${VERDICT_COLOR[calibration!.verdict]}`}>
              {calibration!.verdict.replace(/_/g, " ")}
            </span>
            <span className="text-xs text-slate-400">{calibration!.suggested_action}</span>
          </div>
        )}
        {calibration?.buckets && (
          <div className="grid grid-cols-3 gap-2 mt-2">
            {["high", "medium", "low"].map((b) => {
              const stats = calibration.buckets[b];
              return (
                <div key={b} className="border border-slate-800/60 rounded p-2">
                  <div className="text-xs text-slate-400 capitalize">{b}</div>
                  <div className="text-sm text-slate-100 mt-0.5">
                    {stats?.insufficient_data || !stats || stats.n === 0
                      ? `${stats?.n ?? 0} recs`
                      : `${stats.n} recs · ${stats.win_rate_pct}% win`}
                  </div>
                  {stats?.avg_ret_pct !== undefined && (
                    <div className="text-xs text-slate-300">{num(stats.avg_ret_pct)}</div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Decay curve */}
      <div>
        <div className="text-[10px] uppercase tracking-wide text-slate-500 mb-1">
          Signal Decay Curve
        </div>
        {decayInsufficient ? (
          <div className="text-xs text-slate-500 px-2 py-3 bg-slate-900/40 rounded border border-slate-800/60">
            Insufficient horizon data. Best holding period unknown until ~5+ scored signals per horizon.
          </div>
        ) : (
          <>
            <div className="text-xs text-slate-300 mb-1">{decay!.interpretation}</div>
            <div className="grid grid-cols-7 gap-1">
              {Object.entries(decay!.curve).map(([h, stats]) => (
                <div
                  key={h}
                  className={`border rounded p-1.5 text-center ${
                    h === decay!.peak_horizon
                      ? "border-emerald-500/40 bg-emerald-500/10"
                      : "border-slate-800/60 bg-slate-900/30"
                  }`}
                >
                  <div className="text-[10px] text-slate-500">{h}</div>
                  <div className="text-xs text-slate-200 font-medium">
                    {stats.insufficient_data ? "—" : `${num(stats.avg_ret_pct ?? null)}`}
                  </div>
                  {!stats.insufficient_data && stats.win_rate_pct !== undefined && (
                    <div className="text-[10px] text-slate-400">{stats.win_rate_pct}%</div>
                  )}
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      {/* Per-source attribution */}
      <div>
        <div className="text-[10px] uppercase tracking-wide text-slate-500 mb-1">
          Per-Source Alpha Attribution ({horizon}d)
        </div>
        {attrInsufficient ? (
          <div className="text-xs text-slate-500 px-2 py-3 bg-slate-900/40 rounded border border-slate-800/60">
            Not enough single-source-dominant signals. Run for multiple weeks to surface which sub-signals
            carry stand-alone alpha.
          </div>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            {Object.entries(attribution!.by_source).map(([src, b]) => (
              <div key={src} className="border border-slate-800/60 rounded p-2">
                <div className="text-xs text-slate-400">{src.replace("_score", "")}</div>
                <div className="text-sm text-slate-100 mt-0.5">
                  {b.insufficient_data ? `${b.n} recs` : `${b.n} · ${b.win_rate_pct}% win`}
                </div>
                {b.avg_ret_pct !== undefined && (
                  <div className="text-xs text-slate-300">{num(b.avg_ret_pct)}</div>
                )}
                {b.verdict && (
                  <div className={`text-[10px] mt-1 ${VERDICT_COLOR[b.verdict] ?? "text-slate-400"}`}>
                    {b.verdict.replace(/_/g, " ")}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {loading && <div className="text-xs text-slate-500">Loading…</div>}
    </div>
  );
}

export default memo(TrustDashboardPanel);
