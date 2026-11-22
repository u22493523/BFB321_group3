"""Back up and restore the TapHouse database (Step 6).

Works with local MySQL and with Aiven, using only PyMySQL, so mysqldump does
not have to be installed. A backup is one .sql file in backups/ (ignored by
git) holding every table's structure and rows. It can be restored with this
script or with the MySQL client (mysql taphouse_didss < file.sql).

    python backup.py                     back up DB_NAME to backups/<db>-<date>-<time>.sql
    python backup.py list                show the backups, newest first
    python backup.py restore FILE        replace the tables with the ones in FILE
    python backup.py restore latest      restore the newest backup

migrate.py makes a backup automatically before it changes the database.
"""
import datetime
import glob
import os
import sys

from db import BASE_DIR, connect

BACKUP_DIR = os.path.join(BASE_DIR, "backups")


def _tables(cur):
    cur.execute("SHOW FULL TABLES WHERE Table_type = 'BASE TABLE'")
    return sorted(list(row.values())[0] for row in cur.fetchall())


def create_backup(database=None, folder=BACKUP_DIR, label=""):
    """Write every table (structure and rows) to one .sql file. Returns its path.

    Each statement is written on a single line ending with ';' so restore()
    can read it back safely, even when product names contain ';'."""
    conn = connect(database)
    with conn, conn.cursor() as cur:
        cur.execute("SELECT DATABASE() AS db")
        name = cur.fetchone()["db"]
        os.makedirs(folder, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(folder, f"{name}-{stamp}{('-' + label) if label else ''}.sql")
        tables = _tables(cur)
        rows_written = 0
        with open(path, "w", encoding="utf-8") as out:
            out.write(f"-- TapHouse DIDSS backup of `{name}` taken {datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n")
            out.write(f"-- Tables: {', '.join(tables)}\n")
            out.write("SET FOREIGN_KEY_CHECKS = 0;\n")
            for table in tables:
                cur.execute(f"SHOW CREATE TABLE `{table}`")
                create = cur.fetchone()["Create Table"]
                out.write(f"DROP TABLE IF EXISTS `{table}`;\n")
                out.write(" ".join(create.split()) + ";\n")       # one line
                cur.execute(f"SELECT * FROM `{table}`")
                for row in cur.fetchall():
                    columns = ", ".join(f"`{c}`" for c in row)
                    values = cur.mogrify(", ".join(["%s"] * len(row)), list(row.values()))
                    out.write(f"INSERT INTO `{table}` ({columns}) VALUES ({values});\n")
                    rows_written += 1
            out.write("SET FOREIGN_KEY_CHECKS = 1;\n")
    print(f"Backed up {len(tables)} tables ({rows_written} rows) to {os.path.relpath(path, BASE_DIR)}")
    return path


def list_backups(folder=BACKUP_DIR):
    return sorted(glob.glob(os.path.join(folder, "*.sql")), key=os.path.getmtime, reverse=True)


def restore(path, database=None):
    """Make `database` exactly like the backup: tables that were created after
    the backup (for example by a later migration) are dropped, and every table
    in the file is rebuilt with its rows. If a statement fails the restore
    stops with the MySQL error; nothing is lost, because the backup file is
    unchanged and can be restored again."""
    if path == "latest":
        backups = list_backups()
        if not backups:
            raise SystemExit("No backups found in backups/.")
        path = backups[0]
    if not os.path.exists(path):
        raise SystemExit(f"Backup file not found: {path}")
    count = 0
    conn = connect(database)
    with conn, conn.cursor() as cur, open(path, encoding="utf-8") as backup_file:
        lines = backup_file.read().splitlines()
        header = next((ln for ln in lines[:3] if ln.startswith("-- Tables:")), None)
        if header is None:
            raise SystemExit(f"{os.path.basename(path)} is not a backup made by backup.py.")
        in_backup = {t.strip() for t in header[len("-- Tables:"):].split(",") if t.strip()}
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        for table in set(_tables(cur)) - in_backup:
            cur.execute(f"DROP TABLE `{table}`")          # created after this backup was taken
        for line in lines:
            line = line.strip()
            if not line or line.startswith("--"):
                continue
            cur.execute(line.rstrip(";"))
            count += 1
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")
        conn.commit()
    print(f"Restored {os.path.basename(path)} ({count} statements).")
    return path


def main(argv):
    command = argv[0] if argv else "backup"
    if command == "backup":
        create_backup()
    elif command == "list":
        backups = list_backups()
        if not backups:
            print("No backups yet. Run: python backup.py")
        for path in backups:
            print(f"{os.path.basename(path)}  {os.path.getsize(path) / 1024:.1f} KB")
    elif command == "restore" and len(argv) == 2:
        answer = input("This replaces every table with the backup. Type YES to continue: ")
        if answer.strip() == "YES":
            restore(argv[1])
        else:
            print("Cancelled.")
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
