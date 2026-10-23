"use client";

import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { num } from "@/lib/format";

/** 12 weeks of total units for one SKU. One series, so the title names it (no legend). */
export default function SalesChart({ trend }: { trend: { week: string; units: number }[] }) {
  const data = trend.map((t) => ({
    ...t,
    label: new Date(t.week + "T00:00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short" }),
  }));
  return (
    <div>
      <div className="small muted">Weekly units sold, last 12 weeks</div>
      <ResponsiveContainer width="100%" height={130}>
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--border)" strokeDasharray="2 4" vertical={false} />
          <XAxis dataKey="label" tick={{ fontSize: 10, fill: "var(--text-3)" }} tickLine={false} axisLine={false}
                 interval="preserveStartEnd" />
          <YAxis tick={{ fontSize: 10, fill: "var(--text-3)" }} tickLine={false} axisLine={false} width={44}
                 tickFormatter={(v: number) => num(v)} />
          <Tooltip
            formatter={(v) => [num(Number(v)), "Units"]}
            labelFormatter={(l) => `Week of ${l}`}
            contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 12 }}
            cursor={{ stroke: "var(--text-3)", strokeDasharray: "3 3" }}
          />
          <Line type="monotone" dataKey="units" stroke="var(--accent)" strokeWidth={2} dot={{ r: 2 }}
                activeDot={{ r: 5, stroke: "var(--surface)", strokeWidth: 2 }} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
