"use client";

import { useMemo, useState } from "react";

import { ExceptionChip, Flags } from "@/components/Badges";
import { RunSummary, useApi } from "@/lib/api";
import { day, dec, inr, num } from "@/lib/format";

interface Row {
  sku_id: string;
  name: string;
  category: string;
  perishability: string;
  rule_fired: string;
  flags: string[];
  track: "A" | "B";
  recommended_qty: number | null;
  order_value_inr: number;
  weeks_of_cover: number | null;
  avg_12w: number;
  avg_4w: number;
  on_hand: number;
  on_order: number;
  exception_types: string[];
  reason: string;
}

const COLUMNS: { key: keyof Row; label: string; numeric?: boolean }[] = [
  { key: "sku_id", label: "SKU" },
  { key: "name", label: "Product" },
  { key: "rule_fired", label: "Flag" },
  { key: "track", label: "Track" },
  { key: "weeks_of_cover", label: "Cover (wks)", numeric: true },
  { key: "avg_12w", label: "Avg 12w", numeric: true },
  { key: "avg_4w", label: "Avg 4w", numeric: true },
  { key: "on_hand", label: "On hand", numeric: true },
  { key: "on_order", label: "On order", numeric: true },
  { key: "recommended_qty", label: "Rule qty", numeric: true },
  { key: "order_value_inr", label: "Order value", numeric: true },
];

export default function DashboardPage() {
  const { data, error } = useApi<{ run: RunSummary | null; rows: Row[] }>("/api/dashboard");
  const [flag, setFlag] = useState("");
  const [track, setTrack] = useState("");
  const [category, setCategory] = useState("");
  const [perish, setPerish] = useState("");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<{ key: keyof Row; asc: boolean }>({ key: "sku_id", asc: true });

  const rows = data?.rows ?? [];
  const categories = useMemo(() => [...new Set(rows.map((r) => r.category))].sort(), [rows]);
  const filtered = useMemo(() => {
    const q = search.toLowerCase();
    const out = rows.filter((r) =>
      (!flag || r.flags.includes(flag)) && (!track || r.track === track) && (!category || r.category === category) &&
      (!perish || r.perishability === perish) && (!q || r.name.toLowerCase().includes(q) || r.sku_id.toLowerCase().includes(q)));
    const dir = sort.asc ? 1 : -1;
    return out.sort((a, b) => {
      const x = a[sort.key] ?? Infinity, y = b[sort.key] ?? Infinity;   // "no cover limit" sorts last
      return (x < y ? -1 : x > y ? 1 : 0) * dir;
    });
  }, [rows, flag, track, category, perish, search, sort]);

  if (error) return <div className="error">{error}</div>;
  if (!data) return <p className="muted">Loading…</p>;
  if (!data.run) return <p className="muted">No completed run yet.</p>;

  const count = (f: string) => filtered.filter((r) => r.flags.includes(f)).length;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Dashboard</h1>
          <div className="muted">All {rows.length} SKUs from the run of {day(data.run.run_date)}. Click a column to sort.</div>
        </div>
      </div>

      <div className="card row">
        <label className="field">Flag
          <select value={flag} onChange={(e) => setFlag(e.target.value)}>
            <option value="">All</option>
            <option value="STOCKOUT_RISK">Stockout risk</option><option value="OVERSTOCK">Overstock</option>
            <option value="HIGH_DEMAND">High demand</option><option value="OK">OK</option>
          </select>
        </label>
        <label className="field">Track
          <select value={track} onChange={(e) => setTrack(e.target.value)}>
            <option value="">All</option><option value="A">A: rule-clear</option><option value="B">B: exception</option>
          </select>
        </label>
        <label className="field">Category
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">All</option>{categories.map((c) => <option key={c}>{c}</option>)}
          </select>
        </label>
        <label className="field">Perishability
          <select value={perish} onChange={(e) => setPerish(e.target.value)}>
            <option value="">All</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option>
          </select>
        </label>
        <label className="field" style={{ flex: 1, minWidth: 180 }}>Search
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="SKU or product name" />
        </label>
      </div>

      <div className="tiles">
        <div className="tile"><div className="label">SKUs shown</div><div className="value">{num(filtered.length)}</div></div>
        <div className="tile"><div className="label">▼ Stockout risk</div><div className="value">{num(count("STOCKOUT_RISK"))}</div></div>
        <div className="tile"><div className="label">▲ Overstock</div><div className="value">{num(count("OVERSTOCK"))}</div></div>
        <div className="tile"><div className="label">↗ High demand</div><div className="value">{num(count("HIGH_DEMAND"))}</div></div>
        <div className="tile"><div className="label">Track B (exceptions)</div><div className="value">{num(filtered.filter((r) => r.track === "B").length)}</div></div>
        <div className="tile"><div className="label">Rule order value</div><div className="value">{inr(filtered.reduce((t, r) => t + r.order_value_inr, 0))}</div></div>
      </div>

      <div className="card table-wrap">
        <table>
          <thead>
            <tr>
              {COLUMNS.map((c) => (
                <th key={c.key} className={`sortable ${c.numeric ? "num" : ""}`}
                    onClick={() => setSort({ key: c.key, asc: sort.key === c.key ? !sort.asc : true })}>
                  {c.label}{sort.key === c.key ? (sort.asc ? " ↑" : " ↓") : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.map((r) => (
              <tr key={r.sku_id} title={r.reason}>
                <td>{r.sku_id}</td>
                <td>{r.name}<div className="small muted">{r.category} · {r.perishability}</div></td>
                <td><Flags ruleFired={r.rule_fired} /></td>
                <td>{r.track === "B"
                  ? <>B {r.exception_types.map((t) => <ExceptionChip key={t} type={t} />)}</>
                  : "A"}</td>
                <td className="num">{r.weeks_of_cover === null ? "∞" : dec(r.weeks_of_cover)}</td>
                <td className="num">{num(r.avg_12w)}</td>
                <td className="num">{num(r.avg_4w)}</td>
                <td className="num">{num(r.on_hand)}</td>
                <td className="num">{num(r.on_order)}</td>
                <td className="num">{r.recommended_qty === null ? "–" : num(r.recommended_qty)}</td>
                <td className="num">{inr(r.order_value_inr)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
