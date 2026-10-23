# Backtest: Raghav's rules vs today's monthly cycle

**Period:** 2025-10-20 to 2026-10-12 (52 weeks, 400 SKUs, synthetic data). Rules only, no AI.

## Headline

- **Early warning:** the rules flagged **108 of 108 stockouts** 1-6 weeks
  before they happened (107 early enough to reorder within the supplier lead time),
  and **24 of 24 write-offs** in advance.
- **The rules are cautious:** only 10.7% of STOCKOUT_RISK flags were followed by an
  actual stockout within 6 weeks under the old process. They trade some extra ordering for far
  fewer stockouts, which is why inventory still needs watching on the scorecard.
- **Simulated impact:** stockout losses -89%,
  write-off losses +71%,
  with average inventory -2%.
- **Applied to Freshroots' reported losses** (₹28-34 lakh per quarter):
  an estimated saving of **₹8.9-11.0 lakh per quarter** (₹36-44 lakh a year).

| Measure | Monthly cycle (today) | Rules, twice a week | Change |
|---|---:|---:|---:|
| Stockout loss (₹ lakh / quarter) | 52.2 | 5.8 | -89% |
| Write-off loss (₹ lakh / quarter) | 2.1 | 3.6 | +71% |
| SKU-weeks with a stockout | 528 | 97 | -82% |
| Average inventory (₹ crore) | 46.58 | 45.44 | -2% |
| Purchase orders placed | 5,901 | 5,002 | -15% |

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
- A missed sale costs 40% of unit cost (lost margin); a write-off costs
  100% of unit cost. Both are in config.yaml.
- Simulated write-offs (₹2.1 lakh/quarter) are well below the reported ₹10-12 lakh, so
  the write-off % change rests on a small base: treat it as directional.
- Simulated ₹ figures differ from the reported ones, so the headline saving applies the simulated
  % change to Freshroots' reported losses instead of quoting simulated rupees directly.
