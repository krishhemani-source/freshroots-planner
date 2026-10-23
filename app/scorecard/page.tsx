"use client";

import { Bar, BarChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { useApi } from "@/lib/api";
import { day, dec, inr, num, weekLabel } from "@/lib/format";

interface Criterion { key: string; label: string; why: string; max: number; value: number | null; breached: boolean }
interface RunRow {
  run_id: number; run_date: string; stockout_risk_skus: number; overstock_value_inr: number; track_b_share_pct: number;
  reviewed: number; exceptions: number; override_rate_pct: number | null; data_check_failures: number; ai_requests: number;
}
interface WeekRow {
  week: string; runs: number; stockout_risk_skus: number | null; overstock_value_inr: number | null;
  track_b_share_pct: number | null; override_rate_pct: number | null; decisions_with_ai: number;
  data_check_failures: number; ai_requests: number; runs_skipped: number; auto_trips: number;
}
interface Scorecard {
  per_run: RunRow[];
  per_week: WeekRow[];
  kill_criteria: Criterion[];
  ai_today: { used: number; cap: number };
  kill_switch_this_week: { runs_skipped: number; auto_trips: number };
}

const UNIT: Record<string, string> = { override_rate_pct: "%", track_b_share_pct: "%", ai_quota_used_pct: "%", data_check_failures_per_week: "" };

export default function ScorecardPage() {
  const { data, error } = useApi<Scorecard>("/api/scorecard");
  if (error) return <div className="error">{error}</div>;
  if (!data) return <p className="muted">Loading…</p>;

  const limit = (key: string) => data.kill_criteria.find((c) => c.key === key)?.max ?? Infinity;
  const over = (key: string, v: number | null) => v !== null && v > limit(key);
  const quotaPct = data.ai_today.cap ? (100 * data.ai_today.used) / data.ai_today.cap : 0;
  const chart = [...data.per_run].reverse().map((r) => ({ ...r, label: day(r.run_date) }));

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Scorecard</h1>
          <div className="muted">Is the system working? Red means a kill criterion from config.yaml has been crossed.</div>
        </div>
      </div>

      <h2>Kill criteria</h2>
      <div className="tiles">
        {data.kill_criteria.map((c) => (
          <div key={c.key} className={`tile ${c.breached ? "bad" : ""}`}>
            <div className="label">{c.label}</div>
            <div className="value">{c.value === null ? "–" : `${dec(c.value)}${UNIT[c.key] ?? ""}`}</div>
            <div className="small">{c.breached ? "✕ BREACHED" : "✓ OK"}: limit {c.max}{UNIT[c.key] ?? ""}</div>
            {c.breached && <div className="small" style={{ marginTop: 4 }}>{c.why}</div>}
          </div>
        ))}
      </div>

      <div className="grid two">
        <div className="card">
          <h2>Gemini free tier today</h2>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span><b>{num(data.ai_today.used)}</b> of {num(data.ai_today.cap)} requests used</span>
            <span className="muted">{dec(quotaPct)}%</span>
          </div>
          <div className={`progress ${over("ai_quota_used_pct", quotaPct) ? "bad" : ""}`} style={{ marginTop: 6 }}>
            <div style={{ width: `${Math.min(100, quotaPct)}%` }} />
          </div>
          <p className="small muted">Mock-mode runs and cached answers don't count. At the cap, AI switches off automatically until tomorrow.</p>
        </div>
        <div className="card">
          <h2>Kill switch this week</h2>
          <div className="tiles" style={{ marginBottom: 0 }}>
            <div className="tile"><div className="label">Runs skipped (paused)</div><div className="value">{num(data.kill_switch_this_week.runs_skipped)}</div></div>
            <div className="tile"><div className="label">Automatic AI-off trips</div><div className="value">{num(data.kill_switch_this_week.auto_trips)}</div></div>
          </div>
        </div>
      </div>

      {chart.length > 0 && (
        <div className="card">
          <h2>Share of SKUs sent to Track B, by run</h2>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={chart} margin={{ top: 10, right: 16, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="var(--border)" strokeDasharray="2 4" vertical={false} />
              <XAxis dataKey="label" tick={{ fontSize: 11, fill: "var(--text-3)" }} tickLine={false} axisLine={false} />
              <YAxis unit="%" tick={{ fontSize: 11, fill: "var(--text-3)" }} tickLine={false} axisLine={false} width={44}
                     domain={[0, (dataMax: number) => Math.ceil(Math.max(dataMax, limit("track_b_share_pct")) * 1.15)]} />
              <Tooltip formatter={(v) => [`${dec(Number(v))}%`, "Track B share"]}
                       contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 12 }}
                       cursor={{ fill: "var(--surface-2)" }} />
              <ReferenceLine y={limit("track_b_share_pct")} stroke="var(--critical)" strokeDasharray="4 4"
                             label={{ value: `kill limit ${limit("track_b_share_pct")}%`, position: "insideTopRight", fontSize: 11, fill: "var(--critical)" }} />
              <Bar dataKey="track_b_share_pct" fill="var(--accent)" radius={[4, 4, 0, 0]} maxBarSize={36} isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      <div className="card table-wrap">
        <h2>By week</h2>
        <table>
          <thead><tr><th>Week</th><th className="num">Runs</th><th className="num">Stockout-risk SKUs</th><th className="num">Overstock value</th>
            <th className="num">Track B share</th><th className="num">Override rate</th><th className="num">Data-check failures</th>
            <th className="num">AI requests</th><th className="num">Runs skipped</th><th className="num">Auto-trips</th></tr></thead>
          <tbody>
            {data.per_week.map((w) => (
              <tr key={w.week}>
                <td>{weekLabel(w.week)}</td>
                <td className="num">{w.runs}</td>
                <td className="num">{num(w.stockout_risk_skus)}</td>
                <td className="num">{inr(w.overstock_value_inr)}</td>
                <td className={`num ${over("track_b_share_pct", w.track_b_share_pct) ? "bad" : ""}`}>{w.track_b_share_pct === null ? "–" : `${dec(w.track_b_share_pct)}%`}</td>
                <td className={`num ${over("override_rate_pct", w.override_rate_pct) ? "bad" : ""}`}>
                  {w.override_rate_pct === null ? "–" : `${dec(w.override_rate_pct)}%`}<span className="small muted"> {w.decisions_with_ai ? `(${w.decisions_with_ai})` : ""}</span></td>
                <td className={`num ${over("data_check_failures_per_week", w.data_check_failures) ? "bad" : ""}`}>{w.data_check_failures}</td>
                <td className="num">{w.ai_requests}</td>
                <td className="num">{w.runs_skipped}</td>
                <td className="num">{w.auto_trips}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card table-wrap">
        <h2>By run</h2>
        <table>
          <thead><tr><th>Run</th><th className="num">Stockout-risk SKUs</th><th className="num">Overstock value</th><th className="num">Track B share</th>
            <th className="num">Reviewed</th><th className="num">Override rate</th><th className="num">Data-check failures</th><th className="num">AI requests</th></tr></thead>
          <tbody>
            {data.per_run.length === 0 && <tr><td colSpan={8} className="muted">No completed runs in this period.</td></tr>}
            {data.per_run.map((r) => (
              <tr key={r.run_id}>
                <td>{day(r.run_date)}</td>
                <td className="num">{num(r.stockout_risk_skus)}</td>
                <td className="num">{inr(r.overstock_value_inr)}</td>
                <td className={`num ${over("track_b_share_pct", r.track_b_share_pct) ? "bad" : ""}`}>{dec(r.track_b_share_pct)}%</td>
                <td className="num">{r.reviewed} / {r.exceptions}</td>
                <td className={`num ${over("override_rate_pct", r.override_rate_pct) ? "bad" : ""}`}>{r.override_rate_pct === null ? "–" : `${dec(r.override_rate_pct)}%`}</td>
                <td className="num">{r.data_check_failures}</td>
                <td className="num">{r.ai_requests}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="small muted">Override rate = share of Raghav's decisions where his final quantity fell outside the AI's suggested range.</p>
      </div>
    </>
  );
}
