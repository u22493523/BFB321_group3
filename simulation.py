"""Impact simulation (Step 7): TapHouse's manual ordering vs the DIDSS policy.

Both policies face exactly the same 52 weeks of simulated demand and the same
supplier delays (same random numbers), built from each product's demand and
lead-time figures in the database. Only the ordering rule differs, so any
difference in the results comes from the policy.

    Manual (before)  The manager counts stock every Monday. If a product has less
                     than one week of average demand on hand or on order, he orders
                     enough to cover two weeks. No safety stock for variable demand
                     or late deliveries.
    DIDSS (after)    Stock is checked every day (sales are logged in the app). When
                     stock on hand plus on order falls to the reorder point, order
                     the EOQ. Safety stock, ROP and EOQ come from supply_chain.py.
    DIDSS capped     The same, but no order is larger than 30 days of average demand
                     (cold-room space and cash flow). A sensitivity test, not in the app.

    All              A product that runs out triggers an emergency order (one week
                     of demand, delivered next day, at 3x the normal order cost).
                     Unmet demand is lost (the customer buys something else).

    Demand           Normal(average x weekday factor, std dev) per day, never below 0.
                     Weekday factors Mon-Thu 0.8, Fri 1.2, Sat 1.6, Sun 1.0 (mean 1.0),
                     and +38% on a home rugby Saturday every second week.
    Lead time        Normal(supplier lead time, std dev), rounded, at least 1 day.

These are SIMULATED results from the seeded TapHouse data, not measurements
in the bar. The random seed is fixed so the numbers can be reproduced.

    python simulation.py                  95% service level, results in docs/impact/
    python simulation.py --seed 7         another year of random demand
"""
import csv
import math
import os
import random
import sys

from db import BASE_DIR, connect
from supply_chain import recommend

DAYS = 364                          # 52 full weeks; day 0 is a Monday
WEEKDAY_FACTOR = [0.8, 0.8, 0.8, 0.8, 1.2, 1.6, 1.0]
MATCH_UPLIFT = 0.38                 # proposal: home rugby match lifts demand by about 38%
EMERGENCY_COST_FACTOR = 3           # courier / premium on a same-week emergency order
OUT_DIR = os.path.join(BASE_DIR, "docs", "impact")

MAX_COVER_DAYS = 30                 # "DIDSS capped": largest order = 30 days of demand

POLICIES = {
    "manual": "Manual (before)",
    "didss": "DIDSS (after)",
    "capped": "DIDSS, orders capped at 30 days",
}


def load_products(database=None):
    """Every product with its supplier's lead time, from MySQL."""
    conn = connect(database)
    with conn, conn.cursor() as cur:
        cur.execute("""
            SELECT p.sku, p.name, p.current_stock, p.unit_cost, p.annual_demand, p.order_cost,
                   p.holding_cost, p.avg_daily_demand, p.std_dev_demand,
                   s.lead_time_days, s.std_dev_lead_time
            FROM products p JOIN suppliers s ON s.id = p.supplier_id ORDER BY p.sku
        """)
        rows = cur.fetchall()
    for r in rows:
        for key in ("unit_cost", "order_cost", "holding_cost", "std_dev_demand", "std_dev_lead_time"):
            r[key] = float(r[key])
    return rows


def is_match_day(day):
    return day % 14 == 5                                  # every second Saturday


def demand_path(product, rng):
    d, sd = product["avg_daily_demand"], product["std_dev_demand"]
    path = []
    for day in range(DAYS):
        mean = d * WEEKDAY_FACTOR[day % 7] * (1 + MATCH_UPLIFT if is_match_day(day) else 1)
        path.append(max(0, round(rng.gauss(mean, sd))))
    return path


def lead_times(product, rng, count=400):
    return [max(1, round(rng.gauss(product["lead_time_days"], product["std_dev_lead_time"])))
            for _ in range(count)]


def simulate_product(product, policy, demand, delays):
    """One product, one policy, one year. Returns totals and the daily stock level."""
    d = product["avg_daily_demand"]
    model = recommend(d, product["std_dev_demand"], product["lead_time_days"],
                      product["std_dev_lead_time"], product["annual_demand"], product["order_cost"],
                      product["holding_cost"], 0.95)
    eoq = model["eoq"] or math.ceil(14 * d)
    if policy == "capped":
        eoq = min(eoq, math.ceil(MAX_COVER_DAYS * d))

    on_hand = product["current_stock"]
    pipeline = []                                         # [(arrival_day, quantity)]
    delay_iter = iter(delays)
    t = dict(demand=0, sold=0, lost=0, stockout_days=0, orders=0, emergency_orders=0,
             holding_cost=0.0, value_days=0.0)
    trace = []

    for day in range(DAYS):
        on_hand += sum(q for a, q in pipeline if a <= day)                 # deliveries arrive
        pipeline = [(a, q) for a, q in pipeline if a > day]

        want = demand[day]
        sold = min(on_hand, want)
        on_hand -= sold
        t["demand"] += want
        t["sold"] += sold
        if want > sold:
            t["lost"] += want - sold
            t["stockout_days"] += 1

        position = on_hand + sum(q for _, q in pipeline)
        if want > sold and not any(a == day + 1 for a, _ in pipeline):       # ran out today
            qty = math.ceil(7 * d)                                         # emergency: next day
            pipeline.append((day + 1, qty))
            t["emergency_orders"] += 1
            position += qty
        if policy == "manual":
            if day % 7 == 0 and position < 7 * d:                          # Monday stock count
                pipeline.append((day + next(delay_iter), math.ceil(14 * d - position)))
                t["orders"] += 1
        elif position <= model["reorder_point"]:                           # DIDSS: daily check
            pipeline.append((day + next(delay_iter), eoq))
            t["orders"] += 1

        t["holding_cost"] += on_hand * product["holding_cost"] / 365
        t["value_days"] += on_hand * product["unit_cost"]
        trace.append(on_hand)

    t["ordering_cost"] = (t["orders"] + EMERGENCY_COST_FACTOR * t["emergency_orders"]) * product["order_cost"]
    t["avg_inventory_value"] = t["value_days"] / DAYS
    t["safety_stock"], t["reorder_point"] = model["safety_stock"], model["reorder_point"]
    return t, trace


def run(products, seed=42):
    """Simulate every product under every policy with the same random demand."""
    by_policy = {p: [] for p in POLICIES}
    traces = {}
    for index, product in enumerate(products):
        rng = random.Random(seed * 1000 + index)
        demand = demand_path(product, rng)
        delays = lead_times(product, rng)
        for policy in POLICIES:
            totals, trace = simulate_product(product, policy, demand, delays)
            by_policy[policy].append({"sku": product["sku"], "name": product["name"], **totals})
            traces[(product["sku"], policy)] = trace
    return by_policy, traces


def summarise(rows):
    s = {k: sum(r[k] for r in rows) for k in ("demand", "sold", "lost", "stockout_days", "orders",
                                               "emergency_orders", "holding_cost", "ordering_cost",
                                               "avg_inventory_value")}
    s["service_level_pct"] = 100 * s["sold"] / s["demand"] if s["demand"] else 100.0
    s["total_cost"] = s["holding_cost"] + s["ordering_cost"]
    return s


KPI_ROWS = [  # (label, key, format, better)
    ("Service level (fill rate)", "service_level_pct", "{:.1f}%", "higher"),
    ("Stockout days (product-days)", "stockout_days", "{:,.0f}", "lower"),
    ("Units of demand lost", "lost", "{:,.0f}", "lower"),
    ("Emergency orders", "emergency_orders", "{:,.0f}", "lower"),
    ("Normal orders placed", "orders", "{:,.0f}", "-"),
    ("Average inventory value", "avg_inventory_value", "R {:,.0f}", "lower"),
    ("Ordering + emergency cost", "ordering_cost", "R {:,.0f}", "lower"),
    ("Holding cost", "holding_cost", "R {:,.0f}", "lower"),
    ("Total inventory cost", "total_cost", "R {:,.0f}", "lower"),
]


def change(before, after, key=""):
    if key == "service_level_pct":
        return f"{after - before:+.1f} points"
    if before == 0:
        return "-" if after == 0 else "new"
    return f"{(after - before) / before:+.0%}"


def markdown_table(summary):
    m, a, b = summary["manual"], summary["didss"], summary["capped"]
    lines = ["| KPI | Manual (before) | DIDSS (after) | Change | DIDSS, orders capped at 30 days | Change |",
             "|---|---|---|---|---|---|"]
    for label, key, fmt, _ in KPI_ROWS:
        lines.append(f"| {label} | {fmt.format(m[key])} | {fmt.format(a[key])} | "
                     f"{change(m[key], a[key], key)} | {fmt.format(b[key])} | {change(m[key], b[key], key)} |")
    return "\n".join(lines)


def write_csv(summary, by_policy, folder):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "impact_summary.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["kpi"] + [POLICIES[p] for p in POLICIES])
        for label, key, _, _ in KPI_ROWS:
            w.writerow([label] + [round(summary[p][key], 2) for p in POLICIES])
    with open(os.path.join(folder, "impact_by_product.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["sku", "name", "policy", "service_level_pct", "stockout_days", "emergency_orders",
                    "orders", "avg_inventory_value", "total_cost"])
        for policy, rows in by_policy.items():
            for r in rows:
                w.writerow([r["sku"], r["name"], POLICIES[policy], round(100 * r["sold"] / r["demand"], 1),
                            r["stockout_days"], r["emergency_orders"], r["orders"],
                            round(r["avg_inventory_value"], 2),
                            round(r["holding_cost"] + r["ordering_cost"], 2)])


def draw_charts(summary, traces, products, folder):
    """Two PNGs for the README and report. Needs matplotlib (pip install matplotlib)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker  # noqa: F401  (used for the thousands separator)

    ink, muted, grid, surface = "#0b0b0b", "#52514e", "#e1e0d9", "#fcfcfb"
    colors = {"manual": "#eb6834", "didss": "#2a78d6"}      # validated palette slots 2 and 1
    plt.rcParams.update({"font.size": 10, "axes.edgecolor": grid, "axes.labelcolor": muted,
                         "xtick.color": muted, "ytick.color": muted, "text.color": ink,
                         "axes.spines.top": False, "axes.spines.right": False})

    # 1. Before / after: four KPIs as small multiples (each with its own scale, no dual axis)
    panels = [("Stockout days in the year", "stockout_days", "{:,.0f}"),
              ("Emergency orders in the year", "emergency_orders", "{:,.0f}"),
              ("Total inventory cost (R / year)", "total_cost", "R {:,.0f}"),
              ("Average inventory value (R)", "avg_inventory_value", "R {:,.0f}")]
    fig, axes = plt.subplots(1, 4, figsize=(13, 3.6), facecolor=surface)
    for ax, (title, key, fmt) in zip(axes, panels):
        values = [summary["manual"][key], summary["didss"][key]]
        bars = ax.bar(["Manual\n(before)", "DIDSS\n(after)"], values, width=0.55,
                      color=[colors["manual"], colors["didss"]], edgecolor=surface, linewidth=2)
        for bar, v in zip(bars, values):
            ax.annotate(fmt.format(v), (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                        ha="center", va="bottom", xytext=(0, 3), textcoords="offset points", color=ink)
        ax.set_title(title, fontsize=10, color=ink, loc="left")
        ax.set_facecolor(surface)
        ax.yaxis.grid(True, color=grid, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.set_ylim(0, max(values) * 1.18 or 1)                          # bars always start at 0
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    fig.suptitle(f"TapHouse: one simulated year, same demand, manual ordering vs DIDSS (12 products). "
                 f"Service level {summary['manual']['service_level_pct']:.1f}% -> "
                 f"{summary['didss']['service_level_pct']:.1f}%",
                 x=0.01, ha="left", fontsize=11, color=ink)
    fig.tight_layout()
    fig.savefig(os.path.join(folder, "impact_kpis.png"), dpi=150, facecolor=surface)
    plt.close(fig)

    # 2. Lager stock over the first 16 weeks under both policies
    sku = products[0]["sku"]
    name = products[0]["name"]
    days = 112
    fig, ax = plt.subplots(figsize=(11, 3.8), facecolor=surface)
    ax.set_facecolor(surface)
    for policy, label in (("manual", "Manual (before)"), ("didss", "DIDSS (after)")):
        ax.plot(range(days), traces[(sku, policy)][:days], color=colors[policy], linewidth=2, label=label)
    rop = next(r for r in summary["_rows"]["didss"] if r["sku"] == sku)
    ax.axhline(rop["reorder_point"], color=muted, linewidth=1, linestyle="--",
               label=f"DIDSS reorder point ({rop['reorder_point']})")
    for day in range(days):
        if is_match_day(day):
            ax.axvline(day, color=grid, linewidth=4, zorder=0)
    ax.set_xlabel("Day (grey bars: home rugby Saturdays)")
    ax.set_ylabel(f"{name} on hand")
    ax.set_ylim(bottom=0)
    ax.yaxis.grid(True, color=grid, linewidth=0.8)
    ax.legend(frameon=False, loc="lower left", bbox_to_anchor=(0, 1.0), ncol=3)
    fig.suptitle(f"{name}: stock on hand, first 16 weeks (simulated)", x=0.01, ha="left", fontsize=11, color=ink)
    fig.tight_layout()
    fig.savefig(os.path.join(folder, "impact_lager_stock.png"), dpi=150, facecolor=surface)
    plt.close(fig)


def main(argv):
    seed = int(argv[argv.index("--seed") + 1]) if "--seed" in argv else 42
    products = load_products()
    by_policy, traces = run(products, seed)
    summary = {p: summarise(rows) for p, rows in by_policy.items()}
    print(f"Simulated {DAYS} days for {len(products)} products (seed {seed}).\n")
    print(markdown_table(summary))
    write_csv(summary, by_policy, OUT_DIR)
    summary["_rows"] = by_policy
    try:
        draw_charts(summary, traces, products, OUT_DIR)
        print(f"\nCharts and CSV files saved in {os.path.relpath(OUT_DIR, BASE_DIR)}/")
    except ImportError:
        print("\nCSV files saved. Install matplotlib for the charts: pip install matplotlib")


if __name__ == "__main__":
    main(sys.argv[1:])
