"""Backtest the rules over the last 52 weeks of synthetic data (no AI calls).

Usage:  python scripts/backtest.py
Writes: reports/backtest_summary.md (for the presentation)
        reports/backtest_weekly.csv, backtest_episodes.csv, backtest_skus.csv (detail)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planner.backtest import run_backtest  # noqa: E402
from planner.config import ROOT, load_config  # noqa: E402


def pct(new, old):
    return f"{100 * (new - old) / old:+.0f}%" if old else "n/a"


def markdown(r: dict, cfg) -> str:
    base, rules = r["monthly_cycle"], r["rules_twice_weekly"]
    so, wo, fl = r["stockouts"], r["write_offs"], r["stockout_flags"]
    lo, hi = r["scaled_saving_lakh_per_qtr"]
    reported_lo = sum(x[0] for x in [cfg.backtest["reported_stockout_loss_lakh"], cfg.backtest["reported_writeoff_loss_lakh"]])
    reported_hi = sum(x[1] for x in [cfg.backtest["reported_stockout_loss_lakh"], cfg.backtest["reported_writeoff_loss_lakh"]])
    rows = [
        ("Stockout loss (₹ lakh / quarter)", base["stockout_loss_lakh_per_qtr"], rules["stockout_loss_lakh_per_qtr"]),
        ("Write-off loss (₹ lakh / quarter)", base["writeoff_loss_lakh_per_qtr"], rules["writeoff_loss_lakh_per_qtr"]),
        ("SKU-weeks with a stockout", base["stockout_sku_weeks"], rules["stockout_sku_weeks"]),
        ("Average inventory (₹ crore)", base["avg_inventory_crore"], rules["avg_inventory_crore"]),
        ("Purchase orders placed", base["orders_placed"], rules["orders_placed"]),
    ]
    table = "\n".join(f"| {name} | {b:,} | {n:,} | {pct(n, b)} |" for name, b, n in rows)
    return f"""# Backtest: Raghav's rules vs today's monthly cycle

**Period:** {r['period']} (52 weeks, 400 SKUs, synthetic data). Rules only, no AI.

## Headline

- **Early warning:** the rules flagged **{so['warned_1_to_6_weeks_ahead']} of {so['episodes']} stockouts** 1-6 weeks
  before they happened ({so['warned_in_time_to_act']} early enough to reorder within the supplier lead time),
  and **{wo['warned_1_to_6_weeks_ahead']} of {wo['episodes']} write-offs** in advance.
- **The rules are cautious:** only {fl['followed_by_stockout_pct']}% of STOCKOUT_RISK flags were followed by an
  actual stockout within 6 weeks under the old process. Most flags are precautionary; acting on
  them early is what prevents the rest.
- **Simulated impact:** stockout losses {pct(rules['stockout_loss_lakh_per_qtr'], base['stockout_loss_lakh_per_qtr'])},
  write-off losses {pct(rules['writeoff_loss_lakh_per_qtr'], base['writeoff_loss_lakh_per_qtr'])},
  with average inventory {pct(rules['avg_inventory_crore'], base['avg_inventory_crore'])}.
- **Applied to Freshroots' reported losses** (₹{reported_lo}-{reported_hi} lakh per quarter):
  an estimated saving of **₹{lo}-{hi} lakh per quarter** (₹{lo * 4:.0f}-{hi * 4:.0f} lakh a year).

| Measure | Monthly cycle (today) | Rules, twice a week | Change |
|---|---:|---:|---:|
{table}

## Why it works

Today's forecast uses sales that are about 3 weeks old and is refreshed once a month, so festival
ramp-ups and sudden drops are seen late. The rules look at last week's sales on every run and count
stock already on order, so reorders go out weeks earlier.

## Discussion point for the case

Write-offs on perishables go **up** under the rules. Perishable orders are rounded up to the
standard order quantity (about 2 weeks of sales), which can push stock past a 4-week shelf life.
This is a rule Raghav may want to change (e.g. no rounding up for perishables), and it is the kind
of judgment call the exception queue exists for.

## Assumptions and limits

- Synthetic data, calibrated to look like Freshroots; not real sales.
- Twice-weekly runs are approximated as weekly (sales data is weekly).
- Upper bound: every recommended order is assumed approved the same week; Track B exceptions are
  assumed approved at the rule quantity (no credit is taken for the AI or Raghav's judgment);
  conflicting flags get no order; 5% of supplier deliveries arrive two weeks late under both policies.
- A missed sale costs {cfg.backtest['lost_margin_pct_of_cost']}% of unit cost (lost margin); a write-off costs
  {cfg.backtest['writeoff_pct_of_cost']}% of unit cost. Both are in config.yaml.
- Simulated write-offs (₹{base['writeoff_loss_lakh_per_qtr']} lakh/quarter) are well below the reported ₹10-12 lakh, so
  the write-off % change rests on a small base: treat it as directional.
- Simulated ₹ figures differ from the reported ones, so the headline saving applies the simulated
  % change to Freshroots' reported losses instead of quoting simulated rupees directly.
"""


if __name__ == "__main__":
    cfg = load_config()
    if not (cfg.data.folder / "stock_history_weekly.csv").exists():
        sys.exit("The backtest needs the synthetic dataset (3 years with stock history). "
                 "Run it with DATASET=synthetic.")
    result = run_backtest(cfg)
    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    (out / "backtest_summary.md").write_text(markdown(result, cfg))
    for name in ["weekly", "episodes", "skus"]:
        result[name].to_csv(out / f"backtest_{name}.csv", index=False)
    print((out / "backtest_summary.md").read_text())
