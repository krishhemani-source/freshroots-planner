// Indian number formats: 1,23,456 and ₹ in lakh / crore.

const units = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const oneDecimal = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 1 });

export function num(n: number | null | undefined): string {
  return n === null || n === undefined ? "–" : units.format(n);
}

export function dec(n: number | null | undefined): string {
  return n === null || n === undefined ? "–" : oneDecimal.format(n);
}

export function inr(n: number | null | undefined): string {
  if (n === null || n === undefined) return "–";
  if (Math.abs(n) >= 1e7) return `₹${oneDecimal.format(n / 1e7)} cr`;
  if (Math.abs(n) >= 1e5) return `₹${oneDecimal.format(n / 1e5)} L`;
  return `₹${units.format(n)}`;
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "–";
  const d = new Date(iso);
  return d.toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function day(iso: string | null | undefined): string {
  if (!iso) return "–";
  return new Date(iso + (iso.length === 10 ? "T00:00:00" : "")).toLocaleDateString("en-IN", {
    weekday: "short", day: "numeric", month: "short",
  });
}

export function weekLabel(iso: string): string {
  return "w/c " + new Date(iso + "T00:00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}
