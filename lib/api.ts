"use client";

import { useCallback, useEffect, useState } from "react";

// ---- types (mirror planner/service.py) ------------------------------------------------

export type Mode = "ACTIVE" | "AI_OFF" | "PAUSED";

export interface KillSwitch {
  mode: Mode;
  set_by: string;
  set_at: string;
  reason: string;
  auto_trip: string | null;
}

export interface CheckResult {
  check: string;
  file: string;
  passed: boolean;
  detail: string;
}

export interface RunSummary {
  id: number;
  run_date: string;
  status: "completed" | "blocked";
  trigger: string;
  started_at: string;
  finished_at: string | null;
  message: string;
  counts: {
    skus?: number;
    track_a?: number;
    track_b?: number;
    track_b_pct?: number;
    track_a_draft_pos?: number;
    track_a_order_value_inr?: number;
    flags?: Record<string, number>;
    exception_types?: Record<string, number>;
    ai?: { requests: number; failed_batches: number; cached: number; model: string; statuses: Record<string, number> };
  };
}

export interface Status {
  kill_switch: KillSwitch;
  latest_run: RunSummary | null;
  latest_completed_run: RunSummary | null;
  latest_check: { run_date: string; attempted_at: string; trigger: string; passed: boolean; results: CheckResult[] } | null;
  pending_reviews: number;
  ai_today: { used: number; cap: number };
  modes: { ai: string; zoho: string; model: string };
}

// ---- fetching ---------------------------------------------------------------------------

async function handle<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `Request failed (${res.status})`);
  return body as T;
}

export function getJSON<T>(path: string): Promise<T> {
  return fetch(path, { cache: "no-store" }).then((r) => handle<T>(r));
}

export function postJSON<T>(path: string, body: unknown): Promise<T> {
  return fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
    .then((r) => handle<T>(r))
    .finally(() => window.dispatchEvent(new Event(STATUS_CHANGED)));
}

/** Fired after every change, so the banner and pages refresh themselves. */
export const STATUS_CHANGED = "freshroots:status-changed";

export function useApi<T>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => {
    getJSON<T>(path)
      .then((d) => { setData(d); setError(null); })
      .catch((e: Error) => setError(e.message));
  }, [path]);
  useEffect(() => {
    reload();
    window.addEventListener(STATUS_CHANGED, reload);
    return () => window.removeEventListener(STATUS_CHANGED, reload);
  }, [reload]);
  return { data, error, reload };
}
