// Flag and status badges. Colour always comes with an icon and a label, never on its own.

const FLAGS: Record<string, { color: string; icon: string; label: string }> = {
  STOCKOUT_RISK: { color: "var(--critical)", icon: "▼", label: "Stockout risk" },
  OVERSTOCK: { color: "var(--serious)", icon: "▲", label: "Overstock" },
  HIGH_DEMAND: { color: "var(--warning)", icon: "↗", label: "High demand" },
  OK: { color: "var(--good)", icon: "✓", label: "OK" },
};

export function FlagBadge({ flag }: { flag: string }) {
  const f = FLAGS[flag] ?? { color: "var(--text-3)", icon: "•", label: flag };
  return (
    <span className="chip" title={flag}>
      <span className="dot" style={{ background: f.color }} />
      {f.icon} {f.label}
    </span>
  );
}

export function Flags({ ruleFired }: { ruleFired: string }) {
  return (
    <span className="row" style={{ gap: 4, display: "inline-flex" }}>
      {ruleFired.split("+").map((f) => <FlagBadge key={f} flag={f} />)}
    </span>
  );
}

const EXCEPTIONS: Record<string, string> = {
  NEW_SKU: "New SKU",
  CONFLICTING_FLAGS: "Conflicting flags",
  DISTRIBUTOR_ANOMALY: "Distributor anomaly",
  SUDDEN_SWING: "Sudden swing",
  QTY_ABOVE_LIMIT: "Qty above limit",
};

export function ExceptionChip({ type, title }: { type: string; title?: string }) {
  return <span className="chip" title={title}>{EXCEPTIONS[type] ?? type}</span>;
}

const AI_STATUS: Record<string, { label: string; color: string }> = {
  ok: { label: "AI suggestion", color: "var(--accent)" },
  cached: { label: "AI suggestion (cached)", color: "var(--accent)" },
  ai_off: { label: "AI off", color: "var(--text-3)" },
  ai_unavailable: { label: "AI unavailable", color: "var(--serious)" },
};

export function AIStatusChip({ status }: { status: string }) {
  const s = AI_STATUS[status] ?? { label: status, color: "var(--text-3)" };
  return <span className="chip"><span className="dot" style={{ background: s.color }} />{s.label}</span>;
}

export function ModeChip({ mode }: { mode: string }) {
  const color = mode === "ACTIVE" ? "var(--good)" : mode === "AI_OFF" ? "var(--warning)" : "var(--critical)";
  const label = mode === "ACTIVE" ? "Active" : mode === "AI_OFF" ? "AI off" : "Paused";
  return <span className="chip"><span className="dot" style={{ background: color }} />{label}</span>;
}
