"use client";

import Link from "next/link";
import { useState } from "react";

import { AIStatusChip, Flags, ModeChip } from "@/components/Badges";
import { Mode, RunSummary, Status, postJSON, useApi } from "@/lib/api";
import { day, inr, num, when } from "@/lib/format";
import { useName } from "@/lib/useName";

interface History {
  runs: RunSummary[];
  check_attempts: { run_date: string; attempted_at: string; trigger: string; passed: boolean; failures: { file: string; detail: string }[] }[];
  skips: { skipped_at: string; trigger: string; reason: string }[];
  audit: { changed_at: string; changed_by: string; previous_mode: Mode; new_mode: Mode; reason: string; automatic: boolean }[];
}

export default function RunPage() {
  const status = useApi<Status>("/api/status");
  const history = useApi<History>("/api/history");
  const s = status.data;
  const paused = s?.kill_switch.mode === "PAUSED";

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Run</h1>
          <div className="muted">Runs automatically Mon & Thu 06:00 IST. Every order still needs a human approval.</div>
        </div>
      </div>
      {status.error && <div className="error">Can't reach the planning API: {status.error}</div>}

      <div className="grid two">
        <RunNow paused={paused} />
        <KillSwitch status={s} />
      </div>

      {s && <LastRun status={s} />}
      {s?.latest_check && <DataChecks check={s.latest_check} />}
      {history.data && <RunHistory history={history.data} />}
      {history.data && <AuditLog audit={history.data.audit} />}
    </>
  );
}

function RunNow({ paused }: { paused: boolean }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  async function run() {
    setBusy(true);
    setMessage(null);
    try {
      const r = await postJSON<{ status: string; message?: string; track_a?: number; track_b?: number }>("/api/runs", {});
      setMessage(r.status === "completed"
        ? { ok: true, text: `Run completed: ${r.track_a} SKUs rule-clear, ${r.track_b} exceptions for review.` }
        : { ok: false, text: r.message || "Run stopped by the data check gate." });
    } catch (e) {
      setMessage({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h2>Run now</h2>
      <p className="muted small">
        Loads the SAP and Zoho exports, checks the data, applies Raghav's rules, and asks the AI about exceptions.
        Re-running today replaces today's results.
      </p>
      <button className="primary" onClick={run} disabled={busy || paused}>
        {busy ? "Running… (live AI can take a few minutes)" : "▶ Run now"}
      </button>
      {paused && <div className="notice" style={{ marginTop: 10 }}>The system is paused, so runs are blocked.</div>}
      {message && <div className={message.ok ? "success" : "error"} style={{ marginTop: 10 }}>{message.text}</div>}
    </div>
  );
}

function KillSwitch({ status }: { status: Status | undefined | null }) {
  const [name, setName] = useName();
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const mode = status?.kill_switch.mode;

  async function change(to: Mode) {
    setError(null);
    try {
      await postJSON("/api/killswitch", { mode: to, changed_by: name, reason });
      setReason("");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const ready = name.trim() && reason.trim();
  return (
    <div className="card">
      <h2>Kill switch {mode && <ModeChip mode={mode} />}</h2>
      <p className="muted small">
        <b>Pause</b>: no runs and no sending to Zoho. <b>AI off</b>: runs continue without Gemini.
        Name and reason are required and every change is logged.
      </p>
      <div className="row" style={{ marginBottom: 8 }}>
        <label className="field">Your name<input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Raghav" /></label>
        <label className="field" style={{ flex: 1, minWidth: 220 }}>
          Reason<input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why are you changing it?" />
        </label>
      </div>
      <div className="row">
        <button className="danger" disabled={!ready || mode === "PAUSED"} onClick={() => change("PAUSED")}>⏸ Pause</button>
        <button className="amber" disabled={!ready || mode === "AI_OFF"} onClick={() => change("AI_OFF")}>AI off</button>
        <button className="primary" disabled={!ready || mode === "ACTIVE"} onClick={() => change("ACTIVE")}>▶ Resume</button>
      </div>
      {error && <div className="error">{error}</div>}
    </div>
  );
}

function LastRun({ status }: { status: Status }) {
  const run = status.latest_run;
  if (!run) return <div className="card"><h2>Last run</h2><p className="muted">No runs yet. Press Run now.</p></div>;
  const c = run.counts;
  return (
    <div className="card">
      <h2>
        Last run: {day(run.run_date)} ({run.trigger}){" "}
        <span className="chip">{run.status === "completed" ? "✓ Completed" : "✕ Blocked"}</span>
      </h2>
      {run.status === "blocked" ? (
        <div className="error">{run.message}</div>
      ) : (
        <>
          <div className="tiles">
            <div className="tile"><div className="label">SKUs planned</div><div className="value">{num(c.skus)}</div></div>
            <div className="tile"><div className="label">Track A (rule-clear)</div><div className="value">{num(c.track_a)}</div></div>
            <div className="tile"><div className="label">Track B (exceptions)</div><div className="value">{num(c.track_b)}</div>
              <div className="small muted">{c.track_b_pct}% of SKUs</div></div>
            <div className="tile"><div className="label">Track A draft POs</div><div className="value">{num(c.track_a_draft_pos)}</div>
              <div className="small muted">{inr(c.track_a_order_value_inr)} at cost</div></div>
            <div className="tile"><div className="label">Waiting for Raghav</div><div className="value">{num(status.pending_reviews)}</div>
              <div className="small"><Link href="/exceptions">Review →</Link></div></div>
          </div>
          <div className="row small" style={{ gap: 16 }}>
            <span>Flags: {Object.entries(c.flags ?? {}).map(([f, n]) => (
              <span key={f} style={{ marginRight: 8 }}><Flags ruleFired={f} /> {n}</span>))}</span>
          </div>
          {c.ai && (
            <div className="row small" style={{ marginTop: 8 }}>
              AI ({c.ai.model}): {Object.entries(c.ai.statuses).map(([st, n]) => (
                <span key={st}><AIStatusChip status={st} /> {n}</span>))}
              <span className="muted">· {c.ai.requests} requests, {c.ai.cached} cached, {c.ai.failed_batches} failed batches</span>
            </div>
          )}
          <div className="small muted" style={{ marginTop: 8 }}>
            Finished {when(run.finished_at)} · Gemini requests today: {status.ai_today.used} of {status.ai_today.cap}
          </div>
        </>
      )}
    </div>
  );
}

function DataChecks({ check }: { check: NonNullable<Status["latest_check"]> }) {
  return (
    <div className="card">
      <h2>
        Data check gate: {check.passed ? <span className="success">passed</span> : <span style={{ color: "var(--critical)" }}>FAILED, run stopped</span>}
        <span className="muted small"> · {when(check.attempted_at)}</span>
      </h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Result</th><th>Check</th><th>File</th><th>Detail</th></tr></thead>
          <tbody>
            {check.results.map((r, i) => (
              <tr key={i}>
                <td className={r.passed ? "" : "bad"}>{r.passed ? "✓ Pass" : "✕ Fail"}</td>
                <td>{r.check.replace("_", " ")}</td>
                <td>{r.file}</td>
                <td>{r.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function RunHistory({ history }: { history: History }) {
  return (
    <div className="card">
      <h2>Run history</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Date</th><th>Status</th><th>Trigger</th><th className="num">Track A</th><th className="num">Track B</th>
            <th className="num">AI requests</th><th>Finished</th><th>Note</th></tr></thead>
          <tbody>
            {history.runs.map((r) => (
              <tr key={r.id}>
                <td>{day(r.run_date)}</td>
                <td className={r.status === "completed" ? "" : "bad"}>{r.status}</td>
                <td>{r.trigger}</td>
                <td className="num">{num(r.counts.track_a)}</td>
                <td className="num">{num(r.counts.track_b)}</td>
                <td className="num">{num(r.counts.ai?.requests)}</td>
                <td>{when(r.finished_at)}</td>
                <td className="small">{r.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {history.skips.length > 0 && (
        <>
          <h3 style={{ marginTop: 16 }}>Runs refused by the kill switch</h3>
          <table>
            <tbody>
              {history.skips.map((k, i) => (
                <tr key={i}><td>{when(k.skipped_at)}</td><td>{k.trigger}</td><td className="small">{k.reason}</td></tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {history.check_attempts.some((c) => !c.passed) && (
        <>
          <h3 style={{ marginTop: 16 }}>Data check failures</h3>
          <table>
            <tbody>
              {history.check_attempts.filter((c) => !c.passed).map((c, i) => (
                <tr key={i}><td>{when(c.attempted_at)}</td><td>{c.trigger}</td>
                  <td className="small">{c.failures.map((f) => `${f.file}: ${f.detail}`).join("; ")}</td></tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

function AuditLog({ audit }: { audit: History["audit"] }) {
  return (
    <div className="card">
      <h2>Kill switch history</h2>
      {audit.length === 0 ? <p className="muted">No changes yet.</p> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>When</th><th>Change</th><th>By</th><th>Reason</th></tr></thead>
            <tbody>
              {audit.map((a, i) => (
                <tr key={i}>
                  <td>{when(a.changed_at)}</td>
                  <td><ModeChip mode={a.previous_mode} /> → <ModeChip mode={a.new_mode} /></td>
                  <td>{a.changed_by}{a.automatic && <span className="chip" style={{ marginLeft: 6 }}>automatic</span>}</td>
                  <td>{a.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
