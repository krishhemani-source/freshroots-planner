# 5-minute demo script

Normal run → exceptions review → draft POs → scorecard → corrupted-data run showing the gate.

## Before you present (10 minutes ahead)

1. Fresh state, with data dated today:

   ```bash
   .venv/bin/python scripts/reset_demo.py
   ```

2. **To show real Gemini answers**, warm the cache now (about 7 requests, 1-3 minutes):

   ```bash
   AI_MODE=live .venv/bin/python scripts/run_planning.py
   ```

   Re-running on the same data later reuses these answers instantly and spends no quota. They show as
   "AI suggestion (cached)".

3. Start the app **in the same AI mode** and open http://localhost:3000. Keep a terminal open in the
   project folder.

   ```bash
   AI_MODE=live npm run dev
   ```

   The app's Run now uses the mode it was started with. Plain `npm run dev` uses `.env` (mock), which
   would show mock suggestions instead of Gemini's. For a mock-only demo, skip step 2 and use plain
   `npm run dev`. The header chip "Next run AI: …" tells you which mode is on.

4. Optional: on the Exceptions page, type your name once. It's remembered.

---

## 0:00–0:30 · The problem

> "Raghav forecasts 400 SKUs for 1,200 distributors once a month in Excel. By the time procurement
> acts, the data is 3–4 weeks old, and planning errors cost ₹28–34 lakh a quarter. This system runs
> twice a week, automates the clear cases with his own rules, and sends only the judgment calls to
> AI and to him."

## 0:30–1:15 · Run page: a normal run

1. Click **Run now**.
2. Point at the **data check gate: passed**: files present, fresh, no nulls, SKU counts match.
3. Read the tiles:
   - **400 SKUs**
   - **336 rule-clear (Track A)**
   - **64 exceptions (Track B, 16%)**
   - **120 draft POs** (about ₹13 crore at cost)

> "It's October, so the festival uplift is on: demand is planned 25% higher. That's why so many SKUs
> show stockout risk."

## 1:15–2:30 · Exceptions: Raghav's queue

1. Search **FR10365** (promotion spike, stock at zero).
   > "The rule says order 20,640. Gemini suggests 8–12 thousand, and cites how Raghav split large orders
   > before. That's his own history coming back to him."
2. Point at the 12-week chart spike and the distributor table.
3. Edit the quantity, type a reason, and click **Save edited qty**.
4. Search **FR10249** (demand collapse).
   > "Six distributors are down 60–70% and there's 13 weeks of stock. The AI says no order."

   **Approve** with a reason.
5. Optional: **FR10148** (a duplicate PO). The AI suggests no order but blames festival stocking.
   > "It can't see the duplicate PO, only total units on order. That's exactly why a human approves
   > every order."

## 2:30–3:15 · Draft POs

1. Show the two sources: **Rule (A)** lines with the templated reason, and **Raghav (B)** lines with his
   reason.
2. Click **Send N drafts to Zoho**.
   > "They're created as DRAFTS in Zoho. Procurement still approves every one."

## 3:15–4:00 · Scorecard

1. Point at the **kill criteria** and their thresholds, which come from config.yaml.
   > "If Raghav overrides the AI more than 30% of the time, it turns red. That's our signal the AI
   > isn't earning its place."
2. Point at the **Gemini quota** (requests used today out of 200) and the **kill switch** counts.
3. Backtest headline, from `reports/backtest_summary.md`:
   > "Replayed over 52 weeks, the rules flagged all 108 stockouts in advance. On the reported losses
   > that's roughly ₹9–11 lakh a quarter. Perishable write-offs went *up*, which tells us one rule needs
   > Raghav's judgment."

## 4:00–5:00 · Bad data: the gate stops the run

1. In the terminal:

   ```bash
   .venv/bin/python scripts/corrupt_data.py missing
   ```

   (This deletes the stock file. You can use `stale`, `nulls` or `sku-mismatch` instead.)
2. Click **Run now** on the Run page.
   > "Data check gate FAILED: the stock file is missing. Nothing was forecast, and today's good
   > results are untouched. Never forecast on bad data."
3. Optional, kill switch: type a name and reason and click **⏸ Pause**. A red banner appears on every
   page and Run now is blocked. Then **Resume**.
4. Put the data back:

   ```bash
   .venv/bin/python scripts/corrupt_data.py restore
   ```

---

### If something goes wrong

- **The gate fails on "freshness" at the start:** the data is more than 48 hours old. Run
  `scripts/reset_demo.py`.
- **"Run refused: POs already sent":** today's run is locked after sending. Run `scripts/reset_demo.py`.
- **Gemini is slow or overloaded:** the run still completes, and affected exceptions show
  "AI unavailable". For a calm demo, warm the cache beforehand (step 2) or demo in mock mode.
