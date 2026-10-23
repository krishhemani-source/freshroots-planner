# Freshroots Demand Planning

A working demo of a twice-weekly demand planning system for Freshroots (packaged snacks and staples,
400 SKUs, 1,200 distributors across Maharashtra and Goa), built for a business-school case.

**The idea:** Raghav's documented rules handle the clear cases automatically (Track A). Only the
exceptions (new launches, unusual distributor behaviour, one-off events) go to an AI for a suggested
range and then to Raghav, who decides with a written reason (Track B). A human approves every order.

```
Mon & Thu 06:00 IST, or "Run now"
   │
   ▼
Kill switch ──PAUSED──▶ stop
   │
   ▼
Load SAP + Zoho exports ──▶ Data check gate ──bad data──▶ stop + alert
                                   │
                                   ▼
                        Rule engine (no AI): metrics, flags, quantity
                         │                                 │
               Track A: rule-clear                Track B: exceptions
                         │                                 │
                         │                  Context pack ─▶ Gemini (range, cause, confidence)
                         │                                 │
                         │                  Raghav: approve / edit / reject + reason
                         ▼                                 ▼
                       Draft POs ──▶ Zoho (DRAFT only) ──▶ procurement approves
                                   │
                                   ▼
                        Dashboard + Scorecard (with kill criteria)
```

Everything runs on **synthetic data** generated with a fixed seed. No real Freshroots data is used.

---

## Quick start (laptop)

You need Python 3.12 (installed with [uv](https://docs.astral.sh/uv/)) and Node.js 20+.

```bash
cd ~/Documents/freshroots-planner
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt
npm install
cp .env.example .env                      # defaults: mock AI, mock Zoho, local SQLite
.venv/bin/python scripts/reset_demo.py    # synthetic data dated today + clean database
npm run dev                               # web app on http://localhost:3000, API on :8000
```

Open http://localhost:3000 and press **Run now**.

| Task | Command |
|---|---|
| Fresh demo state (data dated today, empty queue) | `.venv/bin/python scripts/reset_demo.py` |
| Use the professor's case data instead | `.venv/bin/python scripts/import_case_data.py`, then `DATASET=case` (see below) |
| One planning run from the terminal | `.venv/bin/python scripts/run_planning.py` |
| Same, with real Gemini | `AI_MODE=live .venv/bin/python scripts/run_planning.py` |
| Check the Gemini key and model (1 request) | `.venv/bin/python scripts/check_gemini.py` |
| Only the data check gate | `.venv/bin/python scripts/check_data.py` |
| Break the data (gate demo) | `.venv/bin/python scripts/corrupt_data.py missing\|stale\|nulls\|sku-mismatch` |
| Put clean data back | `.venv/bin/python scripts/corrupt_data.py restore` |
| Kill switch | `.venv/bin/python -m planner.killswitch status\|pause\|ai-off\|resume --by NAME --reason "..."` |
| Backtest (no AI) | `.venv/bin/python scripts/backtest.py` → `reports/backtest_summary.md` |
| Scheduler (Mon & Thu 06:00 IST, keep running) | `.venv/bin/python scripts/scheduler.py` |
| Tests (no API key needed) | `.venv/bin/python -m pytest` |

The **5-minute demo script** is in [DEMO_SCRIPT.md](DEMO_SCRIPT.md).

---

## How a run works

| Step | What happens | Code |
|---|---|---|
| 1. Trigger | Mon & Thu 06:00 IST (APScheduler on the laptop, Vercel Cron when hosted) or **Run now** | `planner/scheduler.py` |
| Kill switch | PAUSED stops the run before anything else | `planner/killswitch.py` |
| 2. Input | SAP exports (sales, stock, SKU master) + open POs from Zoho | `data/`, `planner/datastore.py` |
| 3. Data check gate | Stops the run if a file is missing, older than 48 h, has > 2% nulls in key columns, or its SKU count is > 5% off the master | `planner/data_checks.py` |
| 4. Rule engine | Raghav's rules; every decision stores the rule that fired and the numbers | `planner/metrics.py`, `planner/rules.py` |
| 5. Context builder | A compact JSON pack per exception: the only data the AI sees | `planner/context_builder.py` |
| 6. AI analysis | Gemini suggests a cause, a quantity **range** and a confidence; it never sets the quantity | `planner/ai/` |
| 7. Review queue | Raghav approves, edits or rejects, always with a reason; decisions feed future packs | Exceptions page |
| 8. Output | Draft POs to Zoho in DRAFT status only | `planner/po_sender.py`, `planner/zoho/` |
| 9. Monitoring | Dashboard and weekly scorecard with kill criteria | Dashboard and Scorecard pages |

Runs are **idempotent**: re-running a date replaces that date's results. Once a run's POs have been
sent to Zoho, it can no longer be replaced.

## Raghav's rules

All thresholds live in [config.yaml](config.yaml); nothing is hard-coded.

- `avg_12w`, `avg_4w` = mean weekly units over the last 12 / 4 weeks (new SKUs: over the weeks they have sold).
- `demand_rate` = `avg_12w` × 1.25 in October, November and March (festival uplift, applied before any check).
- `effective_stock` = on hand + open Zoho POs (switchable: `include_on_order`).
- `weeks_of_cover` = `effective_stock` / `demand_rate`.

| Flag | When | Quantity |
|---|---|---|
| HIGH_DEMAND | `avg_4w` > 1.2 × `avg_12w` (raw, not uplifted) | 1.5 × standard order |
| STOCKOUT_RISK | cover < 6 weeks (perishable: 3) | enough to reach the midpoint (10 / 3.5 weeks), rounded up to standard-order multiples; the larger quantity if HIGH_DEMAND also fires |
| OVERSTOCK | cover > 14 weeks (perishable: 4) | 0 (pause next order) |
| OK | none of the above | 0 |

A SKU goes to **Track B** if any of these apply: fewer than 12 weeks of history; conflicting flags
(HIGH_DEMAND + OVERSTOCK); a top-20 distributor moved more than ±30% vs its own 12-week average
(distributors under 50 units/week are ignored as noise); `avg_4w` differs from `avg_12w` by more than
50%; or the quantity exceeds 3 × the standard order.

## The AI step (Gemini, free tier)

- **Model:** `gemini-3.6-flash` by default (`ai.model` in config.yaml, or `GEMINI_MODEL` in `.env`).
  Checked on 22 Oct 2026: `gemini-2.5-flash` is no longer open to new keys, and `gemini-3.8-flash` was
  overloaded on test day. Run `scripts/check_gemini.py` to see what your key can use.
- **Mock by default** (`AI_MODE=mock`): deterministic answers, no quota. Use `live` only for real demo runs.
- **Within the free-tier limits:** 10 exceptions per request (about 7 requests for 64 exceptions), one
  request at a time at 5 per minute, and a daily cap of 200. Answers are cached by a hash of the context
  pack, so the same data never spends quota twice.
- **Failures never stop a run.**
  - 429 errors wait the delay the API asks for (up to 3 retries).
  - 500/503/504 errors retry with growing waits.
  - Invalid output is retried once.
  - Anything still failing is marked "AI unavailable" and goes to Raghav without a suggestion.
- **Structured output** (`response_mime_type: application/json` + JSON schema), validated with
  Pydantic, and every SKU in the batch must be present. Ranges are clamped to [0, 4 × standard order]
  and any clamp is flagged.

> **Data privacy:** on the Gemini free tier, Google may use prompts to improve its models. That is why
> only synthetic data is ever sent. Real Freshroots data would require a paid tier.

## Kill switch

One system-wide mode, stored in the database and changed from the Run page or the CLI. A name and a
reason are always required, and every change is written to an audit log.

- **ACTIVE:** normal.
- **AI_OFF:** runs continue without Gemini; exceptions go to Raghav marked "AI off".
- **PAUSED:** no runs (scheduled or manual) and no sending to Zoho. Pages stay viewable.

**Automatic trips (to AI_OFF only):**
- The daily Gemini cap is reached. This resets to ACTIVE the next day.
- More than 50% of a run's AI batches fail. This stays off until a person resumes it.

The system never pauses itself, and only a person can resume from PAUSED.

## Zoho

`ZOHO_MODE=mock` (default) writes draft POs to the local database. `ZOHO_MODE=live` uses the Zoho
Inventory API with an OAuth refresh token (`ZOHO_*` in `.env`) and creates **DRAFT** purchase orders
only; it never issues them. The live adapter is written but **not tested against a real Zoho account**.

## Backtest

`scripts/backtest.py` replays the rules week by week over the last 52 weeks (no AI) and compares them
with today's monthly cycle. Headline from the current data: the rules flagged all 108 simulated
stockouts 1-6 weeks ahead, cut simulated stockout losses by about 89%, and are estimated to save
₹9-11 lakh per quarter on Freshroots' reported losses. Write-offs on perishables go **up**, which is a
discussion point for the case. Details and caveats: [reports/backtest_summary.md](reports/backtest_summary.md).

## Professor's case data

The professor's seed files (15 SKUs, 4 distributors, 16 weeks of sales, current inventory and a festival
calendar) can be used instead of the synthetic data. Put them in `prof_data/` (kept out of git), then:

```bash
.venv/bin/python scripts/import_case_data.py          # prof_data/ -> data_case/, in our export format
DATASET=case .venv/bin/python scripts/reset_demo.py   # clean state on the case data
DATASET=case npm run dev                              # or set DATASET=case in .env
```

The header shows which dataset the latest run used. How the gaps are filled (all in `planner/case_data.py`):

- ISO weeks (`2026-W27`) become the Monday of that week.
- Sales are per SKU only, so they are booked to the SKU's one distributor.
- **There is no unit cost.** Every ₹ figure on this dataset uses an *assumed* cost per category
  (`case_data.assumed_unit_cost_inr` in config.yaml: Beverage ₹30, Snack ₹40, Staple ₹60, Dairy Alt ₹90,
  Condiment ₹120).
- No open POs are given, so on-order stock is 0.
- Distributors have no city or tier; the region is used as the city.
- The import time counts as the extraction time. The inventory's own As_Of_Date (19 Oct) is kept in the file.
- The calendar's festival months are checked against config.yaml. They match: Oct, Nov and Mar.

The backtest needs the synthetic dataset, because 16 weeks with no stock history is too short to replay.

On the case data, the rules clear 12 of 15 SKUs. Three go to Raghav because the quantities are large:
- Mango Drink: up about 35% since week 39, HIGH_DEMAND + STOCKOUT_RISK
- Brown Rice: stockout risk
- Peanut Butter: stockout risk

---

## Hosting (Vercel + Neon)

The same code runs on Vercel: the Next.js app, plus the Python API as a Vercel Function
(`api/index.py`, see `vercel.json`). Neon Postgres replaces SQLite. Because Vercel has no writable
disk, the export files are uploaded to the database from the laptop.

1. Push the repository to GitHub and import it in Vercel. The framework is detected as Next.js.
2. In the Vercel project, open **Storage → Neon** to create a database. This sets `DATABASE_URL`.
3. Add these environment variables in Vercel:

   | Variable | Value |
   |---|---|
   | `DATA_SOURCE` | `db` |
   | `APP_PASSCODE` | a team passcode for the site |
   | `CRON_SECRET` | a long random string |
   | `AI_MODE` | `mock` (or `live`) |
   | `GEMINI_API_KEY` | your key |
   | `ZOHO_MODE` | `mock` |

4. On the laptop, put Neon's connection string in `.env` as `DEPLOY_DATABASE_URL`, then run:

   ```bash
   .venv/bin/python scripts/reset_demo.py --hosted
   ```

   This resets Neon and uploads fresh exports. For the professor's data online instead, run
   `DATASET=case .venv/bin/python scripts/reset_demo.py --hosted`.
5. Open the site, sign in with the passcode, and press **Run now**.

Vercel Cron calls `/api/cron/scheduled-run` on Mon and Thu at 00:30 UTC (06:00 IST). The run uses
whatever exports were last uploaded. If they are older than 48 hours, the data check gate correctly
stops the run, just as it would with a stale SAP feed.

**Limits on Vercel's free (Hobby) plan:**
- A run must finish within 300 seconds. A live AI run usually takes 1-3 minutes.
- Cron times can drift within the hour.

## Project structure

```
config.yaml            all thresholds, festival months, AI limits, kill criteria
planner/               the Python system
  synthetic_data.py      fake SAP / Zoho exports (fixed seed)
  data_checks.py         data check gate
  metrics.py, rules.py   Raghav's rules and routing
  engine.py, pipeline.py one run end to end
  context_builder.py     what the AI sees
  ai/                    mock + Gemini clients, batching, retries, cache
  killswitch.py          modes, audit log, circuit breakers, CLI
  po_sender.py, zoho/    draft POs and Zoho adapters
  service.py, api.py     what each page shows (FastAPI)
  backtest.py            52-week replay
app/, components/, lib/  Next.js web app (five pages)
api/index.py           Python API entry point (Vercel Function / uvicorn)
scripts/               command-line tools (see table above)
tests/                 pytest suite (no API key needed)
reports/               backtest output
```

## Assumptions and limits

- **Synthetic data, calibrated for a realistic demo.** About 16% of SKUs land in Track B. A few one-off
  events are planted so every exception type appears:
  - promotion spikes: FR10365, FR10267
  - demand collapses: FR10249, FR10350
  - duplicate bulk POs: FR10148, FR10387
  - a competitor scheme at 3 Ahilyanagar distributors
- **The backtest is an upper bound.** It assumes every recommended order is approved the same week.
  Twice-weekly runs are simulated weekly, because sales data is weekly.
- **Override rate:** the share of Raghav's decisions whose final quantity falls outside the AI's range.
- **Overstock value:** stock above the cover maximum × demand rate, at unit cost.
- The live Zoho adapter is untested. Vercel Services (still in beta) was not used: the Python API runs as
  a classic `/api` function.
