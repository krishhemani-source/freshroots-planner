"""Builds synthetic Freshroots data shaped like SAP and Zoho exports.

Everything comes from one random seed, so the same seed always gives the
same files. History is simulated week by week under today's process (one
monthly Excel forecast on 3-week-old data), which naturally produces
stockouts and perishable write-offs for the backtest to find.
"""

import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from planner.config import Config
from planner.stock_sim import baseline_policy, simulate_sku

IST = timezone(timedelta(hours=5, minutes=30))

# category -> perishability, products, pack sizes, unit cost range (INR)
CATEGORIES = {
    "Namkeen & Chips": ("medium", ["Bhujia", "Farsan Mix", "Chakli", "Banana Chips", "Potato Wafers",
                                   "Masala Peanuts", "Chivda"], ["150 g", "200 g", "400 g"], (18, 90)),
    "Biscuits & Cookies": ("medium", ["Nankhatai", "Jeera Cookies", "Coconut Biscuits", "Butter Cookies",
                                      "Shrewsbury Biscuits"], ["100 g", "200 g", "500 g"], (15, 120)),
    "Ready to Cook": ("medium", ["Poha", "Upma Mix", "Thalipeeth Bhajani", "Sabudana", "Misal Mix"],
                      ["200 g", "500 g", "1 kg"], (25, 140)),
    "Bakery & Rusk": ("high", ["Toast Rusk", "Milk Rusk", "Khari", "Fruit Cake", "Butter Toast"],
                      ["200 g", "300 g", "500 g"], (20, 110)),
    "Sweets & Chikki": ("high", ["Peanut Chikki", "Kaju Katli", "Besan Ladoo", "Til Chikki", "Shrikhand"],
                        ["100 g", "250 g", "500 g"], (30, 260)),
    "Fresh Snacks": ("high", ["Bhakarwadi", "Shankarpali", "Karanji", "Nylon Sev", "Kachori"],
                     ["150 g", "250 g", "400 g"], (20, 120)),
    "Atta & Flours": ("low", ["Chakki Atta", "Besan", "Rice Flour", "Jowar Atta", "Bajra Atta"],
                      ["1 kg", "5 kg", "10 kg"], (45, 420)),
    "Rice & Pulses": ("low", ["Kolam Rice", "Indrayani Rice", "Toor Dal", "Moong Dal", "Chana Dal",
                              "Masoor Dal"], ["1 kg", "5 kg"], (60, 480)),
    "Spices & Masala": ("low", ["Goda Masala", "Kanda Lasun Masala", "Malvani Masala", "Turmeric Powder",
                                "Chilli Powder", "Garam Masala"], ["50 g", "100 g", "200 g"], (15, 95)),
}
VARIANTS = ["Classic", "Masala", "Pudina", "Lite", "Premium", "Kolhapuri", "Goan", "Homestyle", "Family"]

# city -> (state, region, weight)
CITIES = {
    "Mumbai": ("Maharashtra", "Mumbai Metro", 16), "Thane": ("Maharashtra", "Mumbai Metro", 8),
    "Navi Mumbai": ("Maharashtra", "Mumbai Metro", 5), "Pune": ("Maharashtra", "Western Maharashtra", 13),
    "Kolhapur": ("Maharashtra", "Western Maharashtra", 4), "Sangli": ("Maharashtra", "Western Maharashtra", 2.5),
    "Satara": ("Maharashtra", "Western Maharashtra", 2), "Solapur": ("Maharashtra", "Western Maharashtra", 4),
    "Nashik": ("Maharashtra", "North Maharashtra", 6), "Jalgaon": ("Maharashtra", "North Maharashtra", 2.5),
    "Ahilyanagar": ("Maharashtra", "North Maharashtra", 2),
    "Chhatrapati Sambhajinagar": ("Maharashtra", "Marathwada", 5), "Nanded": ("Maharashtra", "Marathwada", 2.5),
    "Latur": ("Maharashtra", "Marathwada", 2), "Nagpur": ("Maharashtra", "Vidarbha", 7),
    "Amravati": ("Maharashtra", "Vidarbha", 3), "Akola": ("Maharashtra", "Vidarbha", 2),
    "Ratnagiri": ("Maharashtra", "Konkan", 1.5), "Panaji": ("Goa", "Goa", 3), "Margao": ("Goa", "Goa", 3),
    "Vasco da Gama": ("Goa", "Goa", 1.5), "Mapusa": ("Goa", "Goa", 1.5), "Ponda": ("Goa", "Goa", 1),
}
NAME_PREFIXES = ["Shree", "Om Sai", "Jai Bhavani", "Mauli", "Siddhivinayak", "Patil", "Deshmukh", "Kulkarni",
                 "Shinde", "Jadhav", "Pawar", "Gaikwad", "Naik", "Kamat", "Dessai", "Sawant", "Mahalaxmi",
                 "Swami Samarth", "Vitthal", "Ganesh"]
NAME_SUFFIXES = ["Traders", "Distributors", "Agencies", "Enterprises", "Marketing", "Sales Corp", "Wholesale"]
TIERS = {"A": (0.08, 8.0), "B": (0.27, 3.0), "C": (0.65, 1.0)}   # tier -> (share of distributors, size)
STANDARD_ORDER_WEEKS = {"low": 4, "medium": 4, "high": 2}        # standard order ~ this many weeks of demand
VENDORS = {"low": "Freshroots Plant - Bhiwandi", "medium": "Freshroots Plant - Chakan",
           "high": "Annapurna Foods (co-packer)"}


def last_complete_week_start(as_of: date) -> date:
    """Monday of the last full Monday-Sunday week before as_of."""
    this_monday = as_of - timedelta(days=as_of.weekday())
    return this_monday - timedelta(days=7)


def seasonality_curve(weeks: list[date], monthly: dict[int, float]) -> np.ndarray:
    """Smooth seasonal multiplier: monthly values placed mid-month and interpolated."""
    mid_days = [15 + 30.4 * (m - 1) for m in range(1, 13)]
    values = [monthly[m] for m in range(1, 13)]
    xs = [mid_days[-1] - 365] + mid_days + [mid_days[0] + 365]   # wrap around the year
    ys = [values[-1]] + values + [values[0]]
    return np.interp([w.timetuple().tm_yday for w in weeks], xs, ys)


def make_sku_master(cfg: Config, rng, weeks: list[date]) -> pd.DataFrame:
    g = cfg.generator
    n_high = round(g.n_skus * g.share_high_perishability)
    high_cats = [c for c, v in CATEGORIES.items() if v[0] == "high"]
    other_cats = [c for c, v in CATEGORIES.items() if v[0] != "high"]
    cats = list(rng.choice(high_cats, n_high)) + list(rng.choice(other_cats, g.n_skus - n_high))
    rng.shuffle(cats)

    first_week, last_week = weeks[0], weeks[-1]
    new_launch_idx = set(rng.choice(g.n_skus, g.n_new_launches, replace=False))
    rows = []
    for i, cat in enumerate(cats):
        perish, products, packs, (cmin, cmax) = CATEGORIES[cat]
        pack = rng.choice(packs)
        base = rng.lognormal(math.log(g.median_weekly_units_per_sku), 0.7)
        if i in new_launch_idx:   # launched in the last 1-11 weeks
            launch = last_week - timedelta(weeks=int(rng.integers(0, 11)))
        elif rng.random() < 0.10:  # launched during the 3-year history window
            launch = first_week + timedelta(weeks=int(rng.integers(len(weeks) // 8, len(weeks) * 3 // 4)))
        else:
            launch = first_week - timedelta(days=int(rng.integers(60, 3000)))
        std_qty = max(48, int(round(base * STANDARD_ORDER_WEEKS[perish] / 48)) * 48)
        rows.append({
            "sku_id": f"FR{10001 + i}",
            "name": f"Freshroots {rng.choice(VARIANTS)} {rng.choice(products)} {pack}",
            "category": cat,
            "pack_size": pack,
            "perishability": perish,
            "standard_order_qty": std_qty,
            "unit_cost_inr": round(float(rng.uniform(cmin, cmax)), 2),
            "launch_date": launch.isoformat(),
            "_base_weekly_units": base,
            "_is_new_launch": i in new_launch_idx,
        })
    return pd.DataFrame(rows)


def make_distributors(cfg: Config, rng) -> pd.DataFrame:
    n = cfg.generator.n_distributors
    cities = list(CITIES)
    weights = np.array([CITIES[c][2] for c in cities], dtype=float)
    tier_names = list(TIERS)
    tier_p = [TIERS[t][0] for t in tier_names]
    rows = []
    for i in range(n):
        city = rng.choice(cities, p=weights / weights.sum())
        state, region, _ = CITIES[city]
        tier = rng.choice(tier_names, p=tier_p)
        rows.append({
            "distributor_id": f"C{200001 + i}",
            "name": f"{rng.choice(NAME_PREFIXES)} {rng.choice(NAME_SUFFIXES)}",
            "city": city,
            "state": state,
            "region": region,
            "tier": tier,
            "_size": TIERS[tier][1] * rng.lognormal(0, 0.3),
        })
    return pd.DataFrame(rows)


def assign_distributors(cfg: Config, rng, skus: pd.DataFrame, dists: pd.DataFrame) -> pd.DataFrame:
    """Which distributors stock which SKU, and each one's share of that SKU's demand."""
    lo, hi = cfg.generator.distributors_per_sku
    size = dists["_size"].to_numpy()
    p = size / size.sum()
    pairs = []
    for s in range(len(skus)):
        k = int(rng.integers(lo, hi + 1))
        chosen = rng.choice(len(dists), k, replace=False, p=p)
        weights = size[chosen] * rng.gamma(2.0, 1.0, k)
        for d, share in zip(chosen, weights / weights.sum()):
            pairs.append((s, d, share))
    # every distributor buys at least one SKU
    covered = {d for _, d, _ in pairs}
    for d in set(range(len(dists))) - covered:
        pairs.append((int(rng.integers(len(skus))), d, 0.01))
    return pd.DataFrame(pairs, columns=["sku_idx", "dist_idx", "share"])


def generate(cfg: Config, out_dir: Path | None = None, as_of: date | None = None,
             extracted_at: datetime | None = None) -> dict:
    """Write all synthetic files to out_dir and return a short summary."""
    g = cfg.generator
    rng = np.random.default_rng(g.seed)
    out_dir = Path(out_dir or cfg.data.folder)
    out_dir.mkdir(parents=True, exist_ok=True)
    as_of = as_of or date.today()
    extracted_at = extracted_at or datetime.now(IST)

    last_week = last_complete_week_start(as_of)
    weeks = [last_week - timedelta(weeks=g.weeks_history - 1 - i) for i in range(g.weeks_history)]
    W = len(weeks)
    week_idx = {w: i for i, w in enumerate(weeks)}

    skus = make_sku_master(cfg, rng, weeks)
    dists = make_distributors(cfg, rng)
    pairs = assign_distributors(cfg, rng, skus, dists)

    # --- true weekly demand per SKU: base x trend x season x noise, starting at launch
    season = seasonality_curve(weeks, g.seasonality)
    festival = np.array([w.month in cfg.rules["festival_months"] for w in weeks])
    years = np.arange(W) / 52.0
    launch_idx = np.array([week_idx.get(date.fromisoformat(d) - timedelta(days=date.fromisoformat(d).weekday()), 0)
                           if date.fromisoformat(d) >= weeks[0] else 0 for d in skus["launch_date"]])
    sku_demand = np.zeros((len(skus), W))
    for s, row in skus.iterrows():
        growth = rng.normal(0.05, 0.08)
        curve = row["_base_weekly_units"] * (1 + growth) ** years * season * rng.normal(1, 0.04, W)
        weeks_live = np.arange(W) - launch_idx[s]
        ramp = np.clip((weeks_live + 1) / 6, 0, 1)   # new SKUs ramp up over ~6 weeks
        sku_demand[s] = np.where(weeks_live >= 0, curve * ramp, 0)

    # --- split into distributor demand, with week-to-week noise per distributor
    pair_demand = (sku_demand[pairs["sku_idx"].to_numpy()] * pairs["share"].to_numpy()[:, None]
                   * rng.gamma(400, 1 / 400, (len(pairs), W)))

    # competitor scheme: a few distributors in one city lose volume over the last 2 weeks
    city_counts = dists[dists["tier"] != "C"].groupby("city").size()
    scheme_city = rng.choice(city_counts[city_counts >= g.competitor_scheme_distributors * 2].index)
    candidates = dists.index[(dists["city"] == scheme_city) & (dists["tier"] != "C")]
    scheme_dists = rng.choice(candidates, g.competitor_scheme_distributors, replace=False)
    for d in scheme_dists:
        drop = rng.uniform(*g.competitor_scheme_drop)
        rows = pairs.index[pairs["dist_idx"] == d]
        pair_demand[rows, W - 2:] *= 1 - drop
    # one-off events on established SKUs over the last 4 weeks
    established = [s for s in range(len(skus)) if launch_idx[s] == 0]
    events = {}
    picks = iter(rng.choice(established, sum(g.one_off_events.values()), replace=False))
    for event, n in g.one_off_events.items():
        events[event] = [int(next(picks)) for _ in range(n)]
    multiplier = {"promo_spike": (2.3, 2.6), "demand_collapse": (0.2, 0.3), "duplicate_bulk_po": (1.3, 1.4)}
    for event, sku_list in events.items():
        for s in sku_list:
            pair_demand[pairs.index[pairs["sku_idx"] == s], W - 4:] *= rng.uniform(*multiplier[event])
    pair_demand = rng.poisson(pair_demand).astype(float)

    # --- stock simulation per SKU, then cap distributor sales on stockout weeks
    true_demand = np.zeros((len(skus), W))
    np.add.at(true_demand, pairs["sku_idx"].to_numpy(), pair_demand)
    history_rows, po_rows = [], []
    sold_ratio = np.ones((len(skus), W))
    for s, row in skus.iterrows():
        plan_rate = row["_base_weekly_units"] * 0.8
        perish, std_qty, start = row["perishability"], int(row["standard_order_qty"]), int(launch_idx[s])
        lead = g.lead_time_weeks[perish]
        policy = baseline_policy(cfg, perish, start, s % g.baseline_review_weeks, std_qty, plan_rate, festival)
        cols, open_orders = simulate_sku(true_demand[s], start, policy, lead, g.shelf_life_weeks[perish],
                                         math.ceil(plan_rate * (lead + 2)), rng)
        with np.errstate(divide="ignore", invalid="ignore"):
            sold_ratio[s] = np.where(true_demand[s] > 0, cols["sold"] / true_demand[s], 1.0)
        for t in range(int(launch_idx[s]), W):
            history_rows.append((weeks[t].isoformat(), row["sku_id"], *(int(cols[k][t]) for k in
                                 ["opening", "received", "ordered"]), int(true_demand[s, t]),
                                 *(int(cols[k][t]) for k in ["sold", "written_off", "closing", "on_order"])))
        for order_week, arrival, qty in open_orders:
            po_rows.append({
                "sku_id": row["sku_id"],
                "vendor_name": VENDORS[row["perishability"]],
                "qty_ordered": int(qty),
                "order_date": (weeks[order_week] + timedelta(days=int(rng.integers(0, 3)))).isoformat(),
                "expected_arrival_date": (as_of + timedelta(days=7 * (arrival - W) + int(rng.integers(1, 7)))).isoformat(),
                "status": "Issued",
            })
        if s in events["duplicate_bulk_po"]:   # a large PO raised twice by mistake
            po_rows.append({
                "sku_id": row["sku_id"], "vendor_name": VENDORS[perish], "qty_ordered": 3 * std_qty,
                "order_date": weeks[-1].isoformat(),
                "expected_arrival_date": (as_of + timedelta(days=10)).isoformat(), "status": "Issued",
            })
    sold_pairs = np.floor(pair_demand * sold_ratio[pairs["sku_idx"].to_numpy()])

    # --- sales_weekly.csv: only rows with sales, like an SAP billing extract
    p_idx, w_idx = np.nonzero(sold_pairs)
    sales = pd.DataFrame({
        "week_start": np.array([w.isoformat() for w in weeks])[w_idx],
        "sku_id": skus["sku_id"].to_numpy()[pairs["sku_idx"].to_numpy()[p_idx]],
        "distributor_id": dists["distributor_id"].to_numpy()[pairs["dist_idx"].to_numpy()[p_idx]],
        "units_sold": sold_pairs[p_idx, w_idx].astype(int),
    }).sort_values(["week_start", "sku_id", "distributor_id"])

    history = pd.DataFrame(history_rows, columns=["week_start", "sku_id", "opening_units", "received_units",
                                                  "ordered_units", "demand_units", "sold_units",
                                                  "written_off_units", "closing_units", "on_order_units"])
    last = history[history["week_start"] == weeks[-1].isoformat()]
    stock = pd.DataFrame({"sku_id": last["sku_id"], "warehouse": "Bhiwandi DC",
                          "on_hand_units": last["closing_units"], "as_of_date": as_of.isoformat()})
    pos = pd.DataFrame(po_rows)
    pos.insert(0, "po_number", [f"PO-{as_of.year}-{i:05d}" for i in range(1, len(pos) + 1)])

    files = {
        "sku_master": skus.drop(columns=[c for c in skus if c.startswith("_")]),
        "distributors": dists.drop(columns=["_size"]),
        "sales_weekly": sales,
        "stock_on_hand": stock,
        "zoho_open_pos": pos,
    }
    for name, df in files.items():
        df.to_csv(out_dir / cfg.data.files[name], index=False)
    history.to_csv(out_dir / "stock_history_weekly.csv", index=False)   # backtest only, not an SAP export

    write_manifest(cfg, out_dir, {n: len(df) for n, df in files.items()}, extracted_at)

    return {
        "as_of": as_of.isoformat(),
        "weeks": f"{weeks[0]} to {weeks[-1]}",
        "skus": len(skus),
        "high_perishability_pct": round(100 * (skus["perishability"] == "high").mean(), 1),
        "new_launches_last_12w": int(skus["_is_new_launch"].sum()),
        "distributors": len(dists),
        "active_sku_distributor_pairs": len(pairs),
        "sales_rows": len(sales),
        "open_pos": len(pos),
        "stockout_weeks": int((history["sold_units"] < history["demand_units"]).sum()),
        "competitor_scheme": f"{len(scheme_dists)} distributors in {scheme_city}, last 2 weeks",
        **{f"event_{e}": ", ".join(skus.loc[s_list, "sku_id"]) for e, s_list in events.items()},
    }


def write_manifest(cfg: Config, out_dir: Path, row_counts: dict, extracted_at: datetime,
                   dataset: str = "Synthetic data (400 SKUs)") -> None:
    """Control file an SAP/Zoho export job writes next to its data files."""
    source = {"zoho_open_pos": "Zoho Inventory"}
    pd.DataFrame([{"file_name": cfg.data.files[n], "source_system": source.get(n, "SAP"),
                   "extracted_at": extracted_at.isoformat(timespec="seconds"), "row_count": c, "dataset": dataset}
                  for n, c in row_counts.items()]).to_csv(out_dir / cfg.data.manifest_file, index=False)
