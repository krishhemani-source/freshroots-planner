"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { ModeChip } from "@/components/Badges";
import { Status, useApi } from "@/lib/api";
import { when } from "@/lib/format";

const PAGES = [
  { href: "/", label: "Run" },
  { href: "/exceptions", label: "Exceptions" },
  { href: "/draft-pos", label: "Draft POs" },
  { href: "/dashboard", label: "Dashboard" },
  { href: "/scorecard", label: "Scorecard" },
];

export default function Header() {
  const path = usePathname();
  const { data } = useApi<Status>("/api/status");
  const ks = data?.kill_switch;

  return (
    <>
      <header className="top">
        <span className="brand">Freshroots Demand Planning</span>
        <nav className="tabs">
          {PAGES.map((p) => (
            <Link key={p.href} href={p.href} className={path === p.href ? "active" : ""}>
              {p.label}
              {p.href === "/exceptions" && data?.pending_reviews ? ` (${data.pending_reviews})` : ""}
            </Link>
          ))}
        </nav>
        {data && (
          <span className="modes small muted">
            {ks && <ModeChip mode={ks.mode} />}
            <span className="chip" title="AI_MODE in .env: used by the next run">
              Next run AI: {data.modes.ai === "live" ? data.modes.model : "mock"}
            </span>
            <span className="chip" title="ZOHO_MODE in .env">Zoho: {data.modes.zoho}</span>
            {data.modes.dataset && (
              <span className="chip" title="Dataset of the latest run (from the export manifest)">Data: {data.modes.dataset}</span>
            )}
          </span>
        )}
      </header>

      {/* Kill switch banner: on every page whenever the system is not ACTIVE */}
      {ks && ks.mode !== "ACTIVE" && (
        <div className={`banner ${ks.mode === "AI_OFF" ? "ai-off" : ""}`} role="alert">
          <strong>{ks.mode === "PAUSED" ? "⏸ SYSTEM PAUSED" : "⚠ AI OFF"}</strong>
          <span>
            {ks.mode === "PAUSED"
              ? "No runs and no sending to Zoho until someone resumes."
              : "Runs continue; exceptions go to Raghav without AI suggestions."}
          </span>
          <span>
            Set by <b>{ks.set_by}</b> at {when(ks.set_at)}: “{ks.reason}”
          </span>
          {path !== "/" && <Link href="/" style={{ color: "#fff" }}>Change on the Run page →</Link>}
        </div>
      )}
    </>
  );
}
