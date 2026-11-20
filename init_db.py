"""Build the TapHouse tables from schema.sql and load seed.sql.

    python init_db.py           create tables, load sample data if empty
    python init_db.py --reset   DROP the tables first (deletes your data!)
"""
import os
import sys

from db import BASE_DIR, connect

# Children before parents: sales_orders references products,
# products references suppliers.
TABLES_IN_DROP_ORDER = ["sales_orders", "products", "suppliers", "users"]
SEED_TABLES = ["suppliers", "products", "sales_orders", "users"]


def read_statements(filename):
    """Return the SQL statements in a .sql file, without -- comment lines."""
    with open(os.path.join(BASE_DIR, filename), encoding="utf-8") as f:
        lines = [ln for ln in f.read().splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def main(reset=False):
    try:
        conn = connect()
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
            else:
                print("Products already exist - sample data NOT loaded (use --reset to rebuild)")
        conn.commit()

    print("Done. Now run:  python app.py")


if __name__ == "__main__":
    main(reset="--reset" in sys.argv)
