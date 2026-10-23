"""Backtest: replay the rule engine week by week over the last 52 weeks. Never calls the AI.

Part 1, "would the rules have warned us?"
    Run the rules in shadow mode on the actual (monthly-process) history. For every
    stockout episode and every write-off episode, check whether STOCKOUT_RISK /
    OVERSTOCK was raised 1-6 weeks before it started, and whether it was raised early
    enough to act (at least the supplier lead time ahead).

Part 2, "what would it have saved?"
    Re-simulate the 52 weeks twice from the same starting stock and the same true
    demand: once with today's monthly Excel cycle, once with the rules run every week
    on fresh data (Track B SKUs assumed approved at the rule quantity; conflicts get
    no order). Compare lost sales and write-offs in ₹.

Twice-weekly runs are approximated as weekly, because sales data is weekly.
"""

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from planner.config import Config
from planner.metrics import sku_metrics
from planner.rules import OVERSTOCK, STOCKOUT_RISK, apply_rules
from planner.stock_sim import baseline_policy, simulate_sku

HISTORY_COLS = ["opening_units", "ordered_units", "demand_units", "sold_units", "written_off_units",
                "closing_units", "on_order_units"]


def episode_starts(mask: np.ndarray, start: int) -> list[int]:
    """Weeks (from `start` on) where a run of True values begins."""
    return [t for t in range(start, len(mask)) if mask[t] and (t == 0 or not mask[t - 1])]


def warned(flags: np.ndarray, t: int, window: int, min_ahead: int = 1) -> bool:
    return bool(flags[max(0, t - window): max(0, t - min_ahead + 1)].any())


def run_backtest(cfg: Config, folder: Path | None = None) -> dict:
    folder = Path(folder or cfg.data.folder)
    bt, g = cfg.backtest, cfg.generator
    master = pd.read_csv(folder / cfg.data.files["sku_master"]).set_index("sku_id")
    hist = pd.read_csv(folder / "stock_history_weekly.csv")
    weeks = sorted(hist["week_start"].unique())
    W, T0, window = len(weeks), len(weeks) - bt["weeks"], bt["warning_window_weeks"]
    months = np.array([date.fromisoformat(w).month for w in weeks])
    festival = np.isin(months, cfg.rules["festival_months"])
    arr = {c: hist.pivot(index="sku_id", columns="week_start", values=c)
               .reindex(index=master.index, columns=weeks).fillna(0).to_numpy() for c in HISTORY_COLS}

    episodes, sku_rows = [], []
    alarms = {"flagged_sku_weeks": 0, "followed_by_stockout": 0}
    weekly = {k: np.zeros(W) for k in ["base_lost_inr", "base_writeoff_inr", "rules_lost_inr", "rules_writeoff_inr"]}

    for i, (sku_id, sku) in enumerate(master.iterrows()):
        perish, std, cost = sku["perishability"], int(sku["standard_order_qty"]), float(sku["unit_cost_inr"])
        lead, shelf = g.lead_time_weeks[perish], g.shelf_life_weeks[perish]
        demand, sold = arr["demand_units"][i], arr["sold_units"][i]
        live = np.nonzero(demand + arr["opening_units"][i])[0]
        if len(live) == 0:
            continue
        launch = int(live[0])

        # ---- Part 1: shadow-mode flags on the actual history
        so_flag, os_flag = np.zeros(W, bool), np.zeros(W, bool)
        for t in range(max(launch + 1, T0 - window), W):
            m = sku_metrics(sold[:t], t - launch, arr["closing_units"][i][t - 1],
                            arr["on_order_units"][i][t - 1], int(months[t]), cfg)
            flags = apply_rules(m, perish, std, cfg)["flags"]
            so_flag[t], os_flag[t] = STOCKOUT_RISK in flags, OVERSTOCK in flags
        lost = demand - sold
        for t in range(max(T0, launch + 1), W - window):   # how often a flag was followed by a real stockout
            if so_flag[t]:
                alarms["flagged_sku_weeks"] += 1
                alarms["followed_by_stockout"] += bool((lost[t: t + window] > 0.5).any())
        for t in episode_starts(lost > 0.5, max(T0, launch + 1)):
            episodes.append({"sku_id": sku_id, "type": "stockout", "week": weeks[t],
                             "warned": warned(so_flag, t, window), "warned_in_time": warned(so_flag, t, window, lead)})
        for t in episode_starts(arr["written_off_units"][i] > 0, max(T0, launch + 1)):
            episodes.append({"sku_id": sku_id, "type": "write_off", "week": weeks[t],
                             "warned": warned(os_flag, t, window), "warned_in_time": warned(os_flag, t, window)})

        # ---- Part 2: counterfactual, same start and same demand for both policies
        start = max(T0, launch)
        initial = arr["opening_units"][i][start] + (arr["on_order_units"][i][start - 1] if start > 0 else 0)
        plan_rate = 0.8 * demand[launch: launch + 4].mean()
        baseline = baseline_policy(cfg, perish, start, i % g.baseline_review_weeks, std, plan_rate, festival)

        def rules_policy(t, sold_so_far, on_hand, on_order, perish=perish, std=std, launch=launch):
            m = sku_metrics(sold_so_far[:t], t - launch, on_hand, on_order, int(months[t]), cfg)
            return apply_rules(m, perish, std, cfg)["recommended_qty"] or 0

        result = {}
        for name, policy in [("base", baseline), ("rules", rules_policy)]:
            sim, _ = simulate_sku(demand, start, policy, lead, shelf, initial,
                                  np.random.default_rng(g.seed + i), sold_before=sold)
            lost_units = (demand - sim["sold"])[start:].clip(min=0)
            weekly[f"{name}_lost_inr"][start:] += lost_units * cost * bt["lost_margin_pct_of_cost"] / 100
            weekly[f"{name}_writeoff_inr"][start:] += sim["written_off"][start:] * cost * bt["writeoff_pct_of_cost"] / 100
            result[name] = {"lost_units": lost_units.sum(), "written_off": sim["written_off"][start:].sum(),
                            "stockout_weeks": int((lost_units > 0.5).sum()),
                            "avg_stock_inr": sim["closing"][start:].mean() * cost,
                            "orders": int((sim["ordered"][start:] > 0).sum())}
        sku_rows.append({"sku_id": sku_id, "perishability": perish, "unit_cost_inr": cost,
                         **{f"{p}_{k}": v for p, r in result.items() for k, v in r.items()}})

    return summarise(cfg, alarms, pd.DataFrame(episodes), pd.DataFrame(sku_rows),
                     pd.DataFrame({"week_start": weeks, **weekly}).iloc[T0:], weeks[T0], weeks[-1])


def summarise(cfg: Config, alarms: dict, episodes: pd.DataFrame, skus: pd.DataFrame, weekly: pd.DataFrame,
              first_week: str, last_week: str) -> dict:
    bt = cfg.backtest
    lost_pct = cfg.backtest["lost_margin_pct_of_cost"] / 100
    writeoff_pct = cfg.backtest["writeoff_pct_of_cost"] / 100

    def per_quarter_lakh(total_inr):
        return round(total_inr / 4 / 1e5, 1)    # 52 weeks = 4 quarters; 1 lakh = 100,000

    def policy_totals(p):
        return {
            "stockout_loss_lakh_per_qtr": per_quarter_lakh((skus[f"{p}_lost_units"] * skus.unit_cost_inr).sum() * lost_pct),
            "writeoff_loss_lakh_per_qtr": per_quarter_lakh((skus[f"{p}_written_off"] * skus.unit_cost_inr).sum() * writeoff_pct),
            "stockout_sku_weeks": int(skus[f"{p}_stockout_weeks"].sum()),
            "avg_inventory_crore": round(skus[f"{p}_avg_stock_inr"].sum() / 1e7, 2),
            "orders_placed": int(skus[f"{p}_orders"].sum()),
        }

    base, rules = policy_totals("base"), policy_totals("rules")

    def reduction(key):
        return round(100 * (1 - rules[key] / base[key]), 1) if base[key] else 0.0

    so_cut, wo_cut = reduction("stockout_loss_lakh_per_qtr"), reduction("writeoff_loss_lakh_per_qtr")
    so_lo, so_hi = bt["reported_stockout_loss_lakh"]
    wo_lo, wo_hi = bt["reported_writeoff_loss_lakh"]

    def detection(kind):
        e = episodes[episodes["type"] == kind] if len(episodes) else episodes
        return {"episodes": len(e),
                "warned_1_to_6_weeks_ahead": int(e["warned"].sum()) if len(e) else 0,
                "warned_in_time_to_act": int(e["warned_in_time"].sum()) if len(e) else 0}

    return {
        "period": f"{first_week} to {last_week}",
        "stockouts": detection("stockout"),
        "write_offs": detection("write_off"),
        "stockout_flags": {**alarms, "followed_by_stockout_pct": round(
            100 * alarms["followed_by_stockout"] / alarms["flagged_sku_weeks"], 1) if alarms["flagged_sku_weeks"] else 0.0},
        "monthly_cycle": base,
        "rules_twice_weekly": rules,
        "stockout_loss_reduction_pct": so_cut,
        "writeoff_loss_reduction_pct": wo_cut,
        "scaled_saving_lakh_per_qtr": [round(so_lo * so_cut / 100 + wo_lo * wo_cut / 100, 1),
                                       round(so_hi * so_cut / 100 + wo_hi * wo_cut / 100, 1)],
        "weekly": weekly,
        "episodes": episodes,
        "skus": skus,
    }
