"""Database migrations (Step 6): upgrade an existing database without losing data.

Each change to the tables is a numbered pair of files in migrations/:

    001_inventory_policy_columns.up.sql     apply the change
    001_inventory_policy_columns.down.sql   undo it (rollback)

The table schema_migrations remembers which ones this database already has.

    python migrate.py status       which migrations are applied / pending
    python migrate.py up           back up, then apply every pending migration in order
    python migrate.py down         back up, then roll back the most recent migration
    python migrate.py baseline     mark all as applied (database built by init_db.py from schema.sql)

MySQL commits table changes (ALTER/CREATE/DROP) immediately, so a failed
migration cannot be rolled back automatically. That is why `up` and `down`
take a backup first (backups/), and why every migration has a .down.sql.
"""
import glob
import os
import re
import sys

from db import BASE_DIR, connect

MIGRATIONS_DIR = os.path.join(BASE_DIR, "migrations")
FILE_PATTERN = re.compile(r"^(\d{3})_([a-z0-9_]+)\.up\.sql$")

CREATE_TRACKING_TABLE = """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version CHAR(3) PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
"""


def available():
    """[(version, name, up_path, down_path)] for every migration, oldest first."""
    found = []
    for path in sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.up.sql"))):
        match = FILE_PATTERN.match(os.path.basename(path))
        if not match:
            raise SystemExit(f"Badly named migration: {os.path.basename(path)} (use 003_short_name.up.sql)")
        down = path.replace(".up.sql", ".down.sql")
        if not os.path.exists(down):
            raise SystemExit(f"{os.path.basename(path)} has no matching .down.sql (every change needs a rollback)")
        found.append((match.group(1), match.group(2), path, down))
    return found


def read_sql(path):
    with open(path, encoding="utf-8") as f:
        lines = [ln for ln in f.read().splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def applied_versions(cur):
    cur.execute(CREATE_TRACKING_TABLE)
    cur.execute("SELECT version FROM schema_migrations ORDER BY version")
    return [row["version"] for row in cur.fetchall()]


def status(database=None):
    """{"applied": [...], "pending": [...]} (versions with names)."""
    conn = connect(database)
    with conn, conn.cursor() as cur:
        done = set(applied_versions(cur))
        conn.commit()
    items = [f"{v}_{name}" for v, name, _, _ in available()]
    return {"applied": [i for i in items if i[:3] in done],
            "pending": [i for i in items if i[:3] not in done]}


def baseline(cur):
    """A database just built from schema.sql already has every change: record them all."""
    cur.execute(CREATE_TRACKING_TABLE)
    for version, name, _, _ in available():
        cur.execute("INSERT IGNORE INTO schema_migrations (version, name) VALUES (%s, %s)", (version, name))


def _backup_first(database, label):
    import backup
    return backup.create_backup(database, label=label)


def up(database=None, make_backup=True):
    """Apply every pending migration, oldest first. Returns the versions applied."""
    conn = connect(database)
    with conn, conn.cursor() as cur:
        done = set(applied_versions(cur))
        conn.commit()
        pending = [m for m in available() if m[0] not in done]
        if not pending:
            print("Database is up to date. Nothing to apply.")
            return []
        if make_backup:
            _backup_first(database, "before-migrate")
        for version, name, up_path, _ in pending:
            print(f"Applying {version}_{name} ...")
            try:
                for statement in read_sql(up_path):
                    cur.execute(statement)
                cur.execute("INSERT INTO schema_migrations (version, name) VALUES (%s, %s)", (version, name))
                conn.commit()
            except Exception as exc:
                conn.rollback()
                print(f"FAILED on {version}_{name}: {exc}")
                print("Nothing after this migration was applied. Fix the .up.sql, or restore with:\n"
                      "  python backup.py restore latest")
                raise SystemExit(1)
        return [m[0] for m in pending]


def down(database=None, make_backup=True):
    """Roll back the most recent migration. Returns its version, or None."""
    conn = connect(database)
    with conn, conn.cursor() as cur:
        done = applied_versions(cur)
        conn.commit()
        if not done:
            print("No migrations applied. Nothing to roll back.")
            return None
        latest = done[-1]
        migration = next((m for m in available() if m[0] == latest), None)
        if migration is None:
            raise SystemExit(f"Migration {latest} is recorded but its files are missing from migrations/.")
        if make_backup:
            _backup_first(database, f"before-rollback-{latest}")
        version, name, _, down_path = migration
        print(f"Rolling back {version}_{name} ...")
        for statement in read_sql(down_path):
            cur.execute(statement)
        cur.execute("DELETE FROM schema_migrations WHERE version = %s", (version,))
        conn.commit()
        return version


def main(argv):
    command = argv[0] if argv else "status"
    if command == "status":
        result = status()
        for item in result["applied"]:
            print(f"  [applied]  {item}")
        for item in result["pending"]:
            print(f"  [pending]  {item}")
        print(f"{len(result['applied'])} applied, {len(result['pending'])} pending.")
    elif command == "up":
        up()
    elif command == "down":
        down()
    elif command == "baseline":
        conn = connect()
        with conn, conn.cursor() as cur:
            baseline(cur)
            conn.commit()
        print("Marked every migration as applied.")
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
