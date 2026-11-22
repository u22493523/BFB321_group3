"""Scalability check (Step 7): does the app stay fast with far more data than
TapHouse has today (12 products)? Builds a separate database, fills it with
synthetic products and a year of sales, and times the endpoints the
dashboard uses most. Your real database is not touched.

    python scale_test.py                 12, 1,000 and 5,000 products
    python scale_test.py 20000           a custom size as well

Results are printed and saved to docs/impact/scalability.csv.
"""
import csv
import os
import random
import statistics
import sys
import time
from contextlib import redirect_stdout
from io import StringIO

os.environ.setdefault("APP_ENV", "testing")

import init_db  # noqa: E402
from app import app  # noqa: E402
from db import BASE_DIR, connect  # noqa: E402

SCALE_DB = os.getenv("SCALE_DB_NAME", "taphouse_didss_scale")
SALES_PER_PRODUCT = 60          # about one sale every 6 days for a year
SUPPLIERS = 50
REPEATS = 15
MANAGER = {"email": "manager@taphouse.co.za", "password": "TapHouse2026!"}


def build(products):
    """Fresh database with the seed data plus synthetic products and sales."""
    admin = connect(None)
    with admin, admin.cursor() as cur:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{SCALE_DB}`")
    with redirect_stdout(StringIO()):
        init_db.main(reset=True, database=SCALE_DB)
    extra = products - 12
    if extra <= 0:
        return
    rng = random.Random(1)
    conn = connect(SCALE_DB)
    with conn, conn.cursor() as cur:
        cur.executemany("INSERT INTO suppliers (name, lead_time_days, std_dev_lead_time, contact_email) "
                        "VALUES (%s, %s, %s, %s)",
                        [(f"Supplier {i}", rng.randint(2, 10), 1.0, f"orders{i}@example.co.za")
                         for i in range(SUPPLIERS)])
        cur.execute("SELECT id FROM suppliers")
        supplier_ids = [r["id"] for r in cur.fetchall()]
        rows = []
        for i in range(extra):
            d = rng.randint(1, 30)
            rows.append((f"SKU-{i:06d}", f"Synthetic product {i}", rng.choice(["Beer", "Wine", "Cider", "Mixer", "Spirits"]),
                         rng.choice(supplier_ids), rng.randint(0, 40 * d), round(rng.uniform(5, 300), 2),
                         d * 365, 120, 3.0, d, round(d * 0.3, 2), int(d * 0.8), d * 5))
        cur.executemany("""INSERT INTO products (sku, name, category, supplier_id, current_stock, unit_cost,
                           annual_demand, order_cost, holding_cost, avg_daily_demand, std_dev_demand,
                           safety_stock, reorder_point) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", rows)
        cur.execute("SELECT id FROM products")
        product_ids = [r["id"] for r in cur.fetchall()]
        batch = []
        for pid in product_ids:
            for _ in range(SALES_PER_PRODUCT):
                batch.append((pid, rng.randint(1, 12), 20.0,
                              f"2026-{rng.randint(1, 11):02d}-{rng.randint(1, 28):02d} 20:00:00"))
            if len(batch) >= 20_000:
                cur.executemany("INSERT INTO sales_orders (product_id, quantity_sold, unit_price, sale_date) "
                                "VALUES (%s, %s, %s, %s)", batch)
                batch = []
        if batch:
            cur.executemany("INSERT INTO sales_orders (product_id, quantity_sold, unit_price, sale_date) "
                            "VALUES (%s, %s, %s, %s)", batch)
        conn.commit()


def timed(client, method, url, body=None):
    """Median response time in milliseconds over REPEATS calls."""
    times = []
    for _ in range(REPEATS):
        start = time.perf_counter()
        res = client.open(url, method=method, json=body)
        times.append((time.perf_counter() - start) * 1000)
        assert res.status_code < 400, (url, res.status_code, res.get_data(as_text=True)[:200])
    return statistics.median(times)


def measure(products):
    build(products)
    app.config["DB_NAME"] = SCALE_DB
    client = app.test_client()
    client.post("/api/auth/login", json=MANAGER)
    conn = connect(SCALE_DB)
    with conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM sales_orders")
        sales = cur.fetchone()["n"]
        cur.execute("EXPLAIN SELECT * FROM sales_orders WHERE product_id = 1 "
                    "AND sale_date BETWEEN '2026-11-01' AND '2026-11-30'")
        index_used = cur.fetchone()["key"]
    return {
        "products": products,
        "sales": sales,
        "GET /api/products?status=REORDER": timed(client, "GET", "/api/products?status=REORDER&size=50"),
        "GET /api/products?q=": timed(client, "GET", "/api/products?q=lager&size=50"),
        "GET /api/sales (one product, one month)": timed(client, "GET", "/api/sales?product_id=1&from=2026-11-01&to=2026-11-30"),
        "POST /api/sales": timed(client, "POST", "/api/sales", {"product_id": 2, "quantity_sold": 1}),
        "GET /api/dashboard": timed(client, "GET", "/api/dashboard"),
        "index used for sales lookup": index_used,
    }


def main(argv):
    sizes = [12, 1000, 5000] + [int(a) for a in argv if a.isdigit()]
    results = []
    for n in sizes:
        print(f"Building {n:,} products ...", flush=True)
        results.append(measure(n))
    keys = [k for k in results[0] if k not in ("products", "sales")]
    print("\n| Endpoint (median ms) | " + " | ".join(f"{r['products']:,} products / {r['sales']:,} sales"
                                                     for r in results) + " |")
    print("|---|" + "---|" * len(results))
    for k in keys:
        cells = [f"{r[k]:.0f}" if isinstance(r[k], float) else str(r[k]) for r in results]
        print(f"| {k} | " + " | ".join(cells) + " |")
    folder = os.path.join(BASE_DIR, "docs", "impact")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "scalability.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0]))
        w.writeheader()
        for r in results:
            w.writerow({k: (round(v, 1) if isinstance(v, float) else v) for k, v in r.items()})
    admin = connect(None)
    with admin, admin.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS `{SCALE_DB}`")      # clean up
    print("\nSaved docs/impact/scalability.csv")


if __name__ == "__main__":
    main(sys.argv[1:])
