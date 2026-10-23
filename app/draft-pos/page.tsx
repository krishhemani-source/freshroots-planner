"use client";

import Link from "next/link";
import { useState } from "react";

import { RunSummary, Status, postJSON, useApi } from "@/lib/api";
import { day, inr, num, when } from "@/lib/format";
import { useName } from "@/lib/useName";

interface Line {
  sku_id: string;
  name: string;
  category: string;
  vendor_name: string;
  qty: number;
  value_inr: number;
  source: "rule" | "review";
  reason: string;
  status: "draft" | "sent";
  zoho_po_id: string | null;
  sent_by: string | null;
  sent_at: string | null;
}

export default function DraftPOsPage() {
  const { data, error } = useApi<{ run: RunSummary | null; lines: Line[]; pending_reviews: number }>("/api/draft-pos");
  const status = useApi<Status>("/api/status");
  const [name, setName] = useName();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  if (error) return <div className="error">{error}</div>;
  if (!data) return <p className="muted">Loading…</p>;
  if (!data.run) return <p className="muted">No completed run yet.</p>;

  const paused = status.data?.kill_switch.mode === "PAUSED";
  const drafts = data.lines.filter((l) => l.status === "draft");
  const total = data.lines.reduce((t, l) => t + l.value_inr, 0);
  const zohoMode = status.data?.modes.zoho ?? "mock";

  async function send() {
    setBusy(true);
    setMessage(null);
    try {
      const r = await postJSON<{ sent: number; value_inr: number }>("/api/draft-pos/send", { run_id: data!.run!.id, sent_by: name });
      setMessage({ ok: true, text: `Sent ${r.sent} draft POs (${inr(r.value_inr)}) to Zoho as DRAFTS. Procurement approves them there.` });
    } catch (e) {
      setMessage({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Draft POs</h1>
          <div className="muted">
            Run of {day(data.run.run_date)}: rule-clear orders (Track A) plus exceptions Raghav approved (Track B).
          </div>
        </div>
      </div>

      <div className="tiles">
        <div className="tile"><div className="label">Draft PO lines</div><div className="value">{num(data.lines.length)}</div></div>
        <div className="tile"><div className="label">Total value (at cost)</div><div className="value">{inr(total)}</div></div>
        <div className="tile"><div className="label">From rules (Track A)</div><div className="value">{num(data.lines.filter((l) => l.source === "rule").length)}</div></div>
        <div className="tile"><div className="label">Approved by Raghav (Track B)</div><div className="value">{num(data.lines.filter((l) => l.source === "review").length)}</div></div>
        <div className="tile"><div className="label">Sent to Zoho</div><div className="value">{num(data.lines.length - drafts.length)}</div></div>
      </div>

      {data.pending_reviews > 0 && (
        <div className="notice">
          {data.pending_reviews} exceptions are still waiting for Raghav. Approved ones appear here.{" "}
          <Link href="/exceptions">Review them →</Link>
        </div>
      )}

      <div className="card">
        <h2>Send to Zoho</h2>
        <p className="small muted">
          Creates each unsent line as a <b>DRAFT</b> purchase order in Zoho Inventory ({zohoMode === "live" ? "live Zoho" : "mock Zoho: saved in the local database"}).
          Nothing is issued to a supplier until procurement approves it in Zoho.
        </p>
        <div className="row">
          <label className="field">Your name<input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Procurement" /></label>
          <button className="primary" onClick={send} disabled={busy || paused || !name.trim() || drafts.length === 0}>
            {busy ? "Sending…" : `Send ${drafts.length} drafts to Zoho`}
          </button>
        </div>
        {paused && <div className="notice" style={{ marginTop: 10 }}>The system is paused: sending to Zoho is blocked.</div>}
        {message && <div className={message.ok ? "success" : "error"} style={{ marginTop: 10 }}>{message.text}</div>}
      </div>

      <div className="card table-wrap">
        <table>
          <thead>
            <tr><th>SKU</th><th>Product</th><th>Vendor</th><th className="num">Qty</th><th className="num">Value</th>
              <th>Source</th><th>Reason</th><th>Status</th></tr>
          </thead>
          <tbody>
            {data.lines.map((l) => (
              <tr key={l.sku_id}>
                <td>{l.sku_id}</td>
                <td>{l.name}<div className="small muted">{l.category}</div></td>
                <td className="small">{l.vendor_name}</td>
                <td className="num">{num(l.qty)}</td>
                <td className="num">{inr(l.value_inr)}</td>
                <td><span className="chip">{l.source === "rule" ? "Rule (A)" : "Raghav (B)"}</span></td>
                <td className="small" style={{ maxWidth: 420 }}>{l.reason}</td>
                <td className="small">
                  {l.status === "sent"
                    ? <><b>✓ Sent</b><br />{l.zoho_po_id}<br /><span className="muted">{l.sent_by}, {when(l.sent_at)}</span></>
                    : "Draft"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
