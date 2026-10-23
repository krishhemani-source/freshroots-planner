"""Week-by-week stock simulation for one SKU, used by the data generator and the backtest.

Each week: (1) the ordering policy may place an order, which arrives after the lead
time (some orders arrive two weeks late), (2) orders due this week are received,
(3) sales are filled from stock oldest-first and capped by what is on hand,
(4) stock older than its shelf life is written off.
"""

import math
from collections import deque
from typing import Callable

import numpy as np

# policy(t, sold_so_far, on_hand, on_order) -> units to order at the start of week t
Policy = Callable[[int, np.ndarray, float, float], float]
COLUMNS = ["opening", "received", "ordered", "sold", "written_off", "closing", "on_order"]


def simulate_sku(demand: np.ndarray, start_t: int, policy: Policy, lead: int, shelf: int,
                 initial_units: float, rng, late_prob: float = 0.05,
                 sold_before: np.ndarray | None = None) -> tuple[dict, list]:
    """sold_before: actual sales before start_t, so policies can look back from week one."""
    W = len(demand)
    cols = {k: np.zeros(W) for k in COLUMNS}
    if sold_before is not None:
        cols["sold"][:start_t] = sold_before[:start_t]
    lots = deque([[start_t, initial_units]] if initial_units > 0 else [])   # [week received, units]
    pipeline: dict[int, list] = {}                                          # arrival week -> [(order week, qty)]

    for t in range(start_t, W):
        on_hand = sum(u for _, u in lots)
        on_order = sum(q for orders in pipeline.values() for _, q in orders)
        cols["opening"][t] = on_hand

        qty = policy(t, cols["sold"], on_hand, on_order)
        if qty > 0:
            arrival = t + lead + (2 if rng.random() < late_prob else 0)
            pipeline.setdefault(arrival, []).append((t, qty))
            cols["ordered"][t] = qty

        received = sum(q for _, q in pipeline.pop(t, []))
        if received:
            lots.append([t, received])
        cols["received"][t] = received

        to_sell = min(demand[t], sum(u for _, u in lots))
        cols["sold"][t] = to_sell
        while to_sell > 0:
            take = min(to_sell, lots[0][1])
            lots[0][1] -= take
            to_sell -= take
            if lots[0][1] <= 0:
                lots.popleft()
        while lots and t - lots[0][0] >= shelf:
            cols["written_off"][t] += lots.popleft()[1]

        cols["closing"][t] = sum(u for _, u in lots)
        cols["on_order"][t] = sum(q for orders in pipeline.values() for _, q in orders)

    open_orders = [(order_week, arrival, qty) for arrival, orders in pipeline.items() for order_week, qty in orders]
    return cols, open_orders


def monthly_excel_policy(start_t: int, review_offset: int, review_weeks: int, lag_weeks: int,
                         order_every_weeks: int, cover_weeks: float, std_qty: int, plan_rate: float,
                         festival: np.ndarray, uplift: float) -> Policy:
    """Today's process: the forecast is refreshed once a month from sales that are
    `lag_weeks` old. Orders are placed every `order_every_weeks` (monthly for most SKUs,
    weekly for perishables) up to `cover_weeks` of that frozen forecast, in
    standard-order multiples."""
    forecast = {"rate": plan_rate}

    def policy(t, sold, on_hand, on_order):
        weeks_in = t - start_t + review_offset
        if weeks_in % review_weeks == 0:
            window = sold[max(start_t, t - lag_weeks - 8): max(start_t, t - lag_weeks)]
            forecast["rate"] = window.mean() if len(window) >= 2 else plan_rate
        if weeks_in % order_every_weeks != 0:
            return 0
        rate = forecast["rate"] * (uplift if festival[t] else 1)
        gap = rate * cover_weeks - on_hand - on_order
        return math.ceil(gap / std_qty) * std_qty if gap > 0 else 0
    return policy


def baseline_policy(cfg, perishability: str, start_t: int, review_offset: int, std_qty: int,
                    plan_rate: float, festival: np.ndarray) -> Policy:
    """Today's monthly Excel process for one SKU, with the settings from config.yaml."""
    g = cfg.generator
    every = g.baseline_order_every_weeks[perishability]
    cover = every + g.lead_time_weeks[perishability] + g.baseline_safety_weeks[perishability]
    return monthly_excel_policy(start_t, review_offset, g.baseline_review_weeks, g.baseline_data_lag_weeks,
                                every, cover, std_qty, plan_rate, festival, cfg.rules["festival_uplift"])
