"""Build the TapHouse tables from schema.sql and load seed.sql.

    python init_db.py           create tables, load sample data if empty
    python init_db.py --reset   DROP the tables first (deletes your data!)
"""
import os
import sys

from db import BASE_DIR, connect
from supply_chain import DEFAULT_SERVICE_LEVEL, recommend

# Children before parents: sales_orders references products,
# products references suppliers.
TABLES_IN_DROP_ORDER = ["sales_orders", "products", "suppliers", "users"]
SEED_TABLES = ["suppliers", "products", "sales_orders", "users"]


def read_statements(filename):
    """Return the SQL statements in a .sql file, without -- comment lines."""
    with open(os.path.join(BASE_DIR, filename), encoding="utf-8") as f:
        lines = [ln for ln in f.read().splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def recalculate_inventory_policy(cur, service_level):
    """Use the Step 2 model to set safety_stock and reorder_point for every product."""
    cur.execute("""
        SELECT p.id, p.avg_daily_demand, p.std_dev_demand, p.annual_demand,
               p.order_cost, p.holding_cost, s.lead_time_days, s.std_dev_lead_time
        FROM products p JOIN suppliers s ON s.id = p.supplier_id
    """)
    rows = cur.fetchall()
    for r in rows:
        rec = recommend(r["avg_daily_demand"], float(r["std_dev_demand"]), r["lead_time_days"],
                        float(r["std_dev_lead_time"]), r["annual_demand"], float(r["order_cost"]),
                        float(r["holding_cost"]), service_level)
        cur.execute("UPDATE products SET safety_stock = %s, reorder_point = %s WHERE id = %s",
                    (rec["safety_stock"], rec["reorder_point"], r["id"]))
    return len(rows)


def main(reset=False, database=None):
    """Build the tables in `database` (default: DB_NAME from .env)."""
    try:
        conn = connect(database)
    except Exception as exc:
        print(f"Could not connect to MySQL: {exc}")
        print("Check the values in .env and ensure your MySQL server is running.")
        sys.exit(1)

    with conn:
        with conn.cursor() as cur:
            if reset:
                print("Dropping existing tables...")
                for table in TABLES_IN_DROP_ORDER:
                    cur.execute(f"DROP TABLE IF EXISTS {table}")

            print("Creating tables from schema.sql ...")
            for stmt in read_statements("schema.sql"):
                cur.execute(stmt)

            cur.execute("SELECT COUNT(*) AS n FROM products")
            if cur.fetchone()["n"] == 0:
                print("Loading sample data from seed.sql ...")
                for stmt in read_statements("seed.sql"):
                    cur.execute(stmt)
                for table in SEED_TABLES:
                    cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
                    print(f"  {table:<14}{cur.fetchone()['n']} rows")
                level = float(os.getenv("SERVICE_LEVEL", DEFAULT_SERVICE_LEVEL))
                n = recalculate_inventory_policy(cur, level)
                print(f"Calculated safety stock and reorder point for {n} products "
                      f"at a {level:.0%} service level (supply_chain.py)")
            else:
                print("Products already exist - sample data NOT loaded (use --reset to rebuild)")
        conn.commit()

    print("Done. Now run:  python app.py")


if __name__ == "__main__":
    main(reset="--reset" in sys.argv)
