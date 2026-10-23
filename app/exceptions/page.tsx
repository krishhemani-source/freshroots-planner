"use client";

import { useState } from "react";

import { AIStatusChip, ExceptionChip, Flags } from "@/components/Badges";
import SalesChart from "@/components/SalesChart";
import { RunSummary, postJSON, useApi } from "@/lib/api";
import { day, dec, inr, num, when } from "@/lib/format";
import { useName } from "@/lib/useName";

interface Suggestion {
  likely_cause: string;
  suggested_qty_min: number;
  suggested_qty_max: number;
  confidence: "low" | "medium" | "high";
  reasoning: string;
  questions_for_raghav: string[];
}

interface Item {
  sku_id: string;
  name: string;
  category: string;
  perishability: string;
  standard_order_qty: number;
  unit_cost_inr: number;
  rule_fired: string;
  recommended_qty: number | null;
  reason: string;
  exception_reasons: string[];
  metrics: Record<string, any>;
  trend: { week: string; units: number }[];
  top_distributors: { distributor_id: string; city: string; tier: string; avg_12w: number; avg_recent: number; change_pct: number; anomaly: boolean }[];
  ai: { status: string; suggestion: Suggestion | null; clamped: boolean; note: string } | null;
  default_qty: number;
  decision: { action: string; final_qty: number; reason: string; decided_by: string; decided_at: string } | null;
  po_sent: boolean;
}

type Filter = "pending" | "decided" | "all";

export default function ExceptionsPage() {
  const { data, error, reload } = useApi<{ run: RunSummary | null; items: Item[] }>("/api/exceptions");
  const [filter, setFilter] = useState<Filter>("pending");
  const [search, setSearch] = useState("");

  if (error) return <div className="error">{error}</div>;
  if (!data) return <p className="muted">Loading…</p>;
  if (!data.run) return <p className="muted">No completed run yet.</p>;

  const pending = data.items.filter((i) => !i.decision).length;
  const q = search.trim().toLowerCase();
  const shown = data.items.filter((i) => (filter === "all" || (filter === "pending") === !i.decision) &&
    (!q || i.sku_id.toLowerCase().includes(q) || i.name.toLowerCase().includes(q)));

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Exceptions</h1>
          <div className="muted">
            Run of {day(data.run.run_date)} · {data.items.length} exceptions · {pending} waiting for a decision.
            The AI suggests a range; you set the quantity. A reason is required.
          </div>
        </div>
        <div className="tabs-inline">
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Find SKU or product" style={{ width: 170 }} />
          {(["pending", "decided", "all"] as Filter[]).map((f) => (
            <button key={f} className={filter === f ? "on" : ""} onClick={() => setFilter(f)}>
              {f === "pending" ? `Pending (${pending})` : f === "decided" ? `Decided (${data.items.length - pending})` : "All"}
            </button>
          ))}
        </div>
      </div>
      {shown.length === 0 && <p className="muted">Nothing here.</p>}
      {shown.map((item) => <ExceptionCard key={item.sku_id} item={item} runId={data.run!.id} onSaved={reload} />)}
    </>
  );
}

function ExceptionCard({ item, runId, onSaved }: { item: Item; runId: number; onSaved: () => void }) {
  const m = item.metrics;
  const coverText = m.weeks_of_cover === null ? "∞" : dec(m.weeks_of_cover);
  return (
    <div className={`card ${item.decision ? "decided" : ""}`}>
      <div className="exception">
        <div>
          <h2 style={{ marginBottom: 4 }}>{item.name} <span className="muted small">{item.sku_id}</span></h2>
          <div className="small muted">{item.category} · {item.perishability} perishability · standard order {num(item.standard_order_qty)} · {inr(item.unit_cost_inr)}/unit</div>
          <div className="row" style={{ margin: "8px 0", gap: 4 }}>
            <Flags ruleFired={item.rule_fired} />
            {item.exception_reasons.map((r) => <ExceptionChip key={r} type={r.split(":")[0]} title={r} />)}
          </div>
          <ul className="small" style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            {item.exception_reasons.map((r) => <li key={r}>{r}</li>)}
          </ul>

          <div className="metrics">
            <div><span>Avg 12 weeks</span><b>{num(m.avg_12w)}</b></div>
            <div><span>Avg 4 weeks</span><b>{num(m.avg_4w)}</b></div>
            <div><span>Demand rate{m.festival_uplift_applied ? " (festival ×1.25)" : ""}</span><b>{num(m.demand_rate)}/wk</b></div>
            <div><span>On hand + on order</span><b>{num(m.on_hand)} + {num(m.on_order)}</b></div>
            <div><span>Weeks of cover</span><b>{coverText}</b> <span>target {m.cover_min}–{m.cover_max}</span></div>
            <div><span>Rule quantity</span><b>{item.recommended_qty === null ? "none (conflict)" : num(item.recommended_qty)}</b></div>
            <div><span>Weeks of history</span><b>{m.weeks_of_history}</b></div>
          </div>
          <SalesChart trend={item.trend} />
          <Distributors rows={item.top_distributors} />
        </div>

        <div>
          <AIPanel ai={item.ai} />
          <DecisionForm item={item} runId={runId} onSaved={onSaved} />
        </div>
      </div>
    </div>
  );
}

function Distributors({ rows }: { rows: Item["top_distributors"] }) {
  if (!rows.length) return null;
  return (
    <details style={{ marginTop: 8 }}>
      <summary className="small">Top distributors: last 2 weeks vs their 12-week average</summary>
      <table className="small">
        <thead><tr><th>Distributor</th><th>City</th><th>Tier</th><th className="num">12-wk avg</th><th className="num">Last 2 wks</th><th className="num">Change</th></tr></thead>
        <tbody>
          {rows.map((d) => (
            <tr key={d.distributor_id}>
              <td>{d.distributor_id}</td><td>{d.city}</td><td>{d.tier}</td>
              <td className="num">{dec(d.avg_12w)}</td><td className="num">{dec(d.avg_recent)}</td>
              <td className={`num ${d.anomaly ? "bad" : ""}`}>{d.change_pct > 0 ? "▲ +" : "▼ "}{dec(d.change_pct)}%{d.anomaly ? " (anomaly)" : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

function AIPanel({ ai }: { ai: Item["ai"] }) {
  if (!ai) return null;
  const s = ai.suggestion;
  if (!s) {
    return (
      <div className="ai-box off">
        <AIStatusChip status={ai.status} />
        <p className="small" style={{ marginBottom: 0 }}>{ai.note || "No AI suggestion for this SKU."} Decide from the rule metrics.</p>
      </div>
    );
  }
  return (
    <div className="ai-box">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <AIStatusChip status={ai.status} />
        <span className="chip">Confidence: {s.confidence}</span>
      </div>
      <div style={{ marginTop: 8 }}><b>Likely cause:</b> {s.likely_cause}</div>
      <div style={{ fontSize: 18, fontWeight: 700, margin: "6px 0" }}>
        Suggested range: {num(s.suggested_qty_min)} – {num(s.suggested_qty_max)} units
      </div>
      {ai.clamped && <div className="notice small">The AI's range was outside 0–4× standard order and was clamped.</div>}
      <p className="small" style={{ margin: "4px 0" }}>{s.reasoning}</p>
      {s.questions_for_raghav.length > 0 && (
        <div className="small"><b>Questions for you:</b>
          <ul style={{ margin: "2px 0", paddingLeft: 18 }}>{s.questions_for_raghav.map((q) => <li key={q}>{q}</li>)}</ul>
        </div>
      )}
    </div>
  );
}

function DecisionForm({ item, runId, onSaved }: { item: Item; runId: number; onSaved: () => void }) {
  const [name, setName] = useName();
  const [qty, setQty] = useState(String(item.default_qty));
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(!item.decision);

  if (item.po_sent && item.decision) {
    return <DecisionSummary d={item.decision} note="PO already sent to Zoho; locked." />;
  }
  if (!editing && item.decision) {
    return (
      <>
        <DecisionSummary d={item.decision} />
        <button style={{ marginTop: 8 }} onClick={() => setEditing(true)}>Change decision</button>
      </>
    );
  }

  async function save(action: "approve" | "edit" | "reject") {
    setError(null);
    try {
      await postJSON("/api/exceptions/decision", {
        run_id: runId, sku_id: item.sku_id, action, qty: Number(qty), reason, decided_by: name,
      });
      setReason("");
      setEditing(false);
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const ready = name.trim() && reason.trim();
  const changed = Number(qty) !== item.default_qty;
  return (
    <div style={{ marginTop: 12 }}>
      <h3>Your decision</h3>
      <div className="row">
        <label className="field">Quantity (units)
          <input type="number" min={0} step={1} value={qty} onChange={(e) => setQty(e.target.value)} style={{ width: 130 }} />
        </label>
        <label className="field">Your name<input value={name} onChange={(e) => setName(e.target.value)} placeholder="Raghav" style={{ width: 140 }} /></label>
      </div>
      <div className="small muted" style={{ margin: "4px 0" }}>
        Pre-filled with {item.ai?.suggestion ? "the middle of the AI range" : "the rule quantity"} ({num(item.default_qty)}).
        Value: {inr(Number(qty) * item.unit_cost_inr)}.
      </div>
      <label className="field" style={{ marginTop: 6 }}>Reason (required)
        <textarea value={reason} onChange={(e) => setReason(e.target.value)} placeholder="What do you know that the data doesn't show?" />
      </label>
      <div className="row" style={{ marginTop: 8 }}>
        <button className="primary" disabled={!ready || changed} onClick={() => save("approve")}
                title={changed ? "You changed the quantity: use Save edited qty" : ""}>✓ Approve {num(item.default_qty)}</button>
        <button disabled={!ready || !changed || Number(qty) < 0} onClick={() => save("edit")}>✎ Save edited qty</button>
        <button className="danger" disabled={!ready} onClick={() => save("reject")}>✕ Reject (no order)</button>
        {item.decision && <button onClick={() => setEditing(false)}>Cancel</button>}
      </div>
      {error && <div className="error">{error}</div>}
    </div>
  );
}

function DecisionSummary({ d, note }: { d: NonNullable<Item["decision"]>; note?: string }) {
  const verb = d.action === "approve" ? "✓ Approved" : d.action === "edit" ? "✎ Edited" : "✕ Rejected";
  return (
    <div style={{ marginTop: 12 }}>
      <h3>{verb}: {num(d.final_qty)} units</h3>
      <div className="small">“{d.reason}”</div>
      <div className="small muted">{d.decided_by}, {when(d.decided_at)}</div>
      {note && <div className="small muted">{note}</div>}
    </div>
  );
}
