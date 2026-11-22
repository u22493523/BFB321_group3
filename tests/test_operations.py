"""Operations tests (Step 6): migrations up and down, backup and restore,
the health check, the delivery audit trail, failed-login logging and the
production settings. Uses the same throw-away test database as test_api.py.

    python -m unittest discover -s tests -p "test_*.py"
"""
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["APP_ENV"] = "testing"          # must be set before app is imported

import pymysql  # noqa: E402

import backup  # noqa: E402
import config  # noqa: E402
import init_db  # noqa: E402
import migrate  # noqa: E402
from app import app  # noqa: E402
from db import connect  # noqa: E402

TEST_DB = app.config["DB_NAME"]
MANAGER = {"email": "manager@taphouse.co.za", "password": "TapHouse2026!"}


def setUpModule():
    try:
        conn = pymysql.connect(host=app.config["DB_HOST"], port=app.config["DB_PORT"],
                               user=app.config["DB_USER"], password=app.config["DB_PASSWORD"])
    except pymysql.err.OperationalError as exc:
        raise unittest.SkipTest(f"MySQL not reachable, operations tests skipped: {exc}")
    with conn, conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{TEST_DB}`")


class OperationsTestCase(unittest.TestCase):
    def setUp(self):
        self.quiet = redirect_stdout(io.StringIO())     # keep the scripts' printouts out of the test output
        self.quiet.__enter__()
        init_db.main(reset=True, database=TEST_DB)
        self.folder = tempfile.mkdtemp()
        self.client = app.test_client()

    def tearDown(self):
        self.quiet.__exit__(None, None, None)
        shutil.rmtree(self.folder, ignore_errors=True)

    def sql(self, query, params=()):
        conn = connect(TEST_DB)
        with conn, conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()

    def tables(self):
        return {list(r.values())[0] for r in self.sql("SHOW TABLES")}

    def columns(self, table):
        return {r["Field"] for r in self.sql(f"SHOW COLUMNS FROM {table}")}

    def roll_back_to_before(self, version):
        """Undo migrations, newest first, until `version` is undone too. Works however
        many migrations are added later. Returns the versions rolled back."""
        undone = []
        while version not in undone:
            undone.append(migrate.down(TEST_DB, make_backup=False))
        return undone

    @staticmethod
    def all_versions():
        return [v for v, _, _, _ in migrate.available()]


class TestMigrations(OperationsTestCase):
    def test_new_database_has_every_migration_recorded(self):
        result = migrate.status(TEST_DB)
        self.assertEqual(result["pending"], [])
        self.assertEqual(len(result["applied"]), len(migrate.available()))

    def test_down_then_up_restores_the_table_and_keeps_data(self):
        undone = self.roll_back_to_before("002")
        self.assertEqual(undone[-1], "002")
        self.assertNotIn("stock_receipts", self.tables())
        self.assertIn("002_stock_receipts", migrate.status(TEST_DB)["pending"])
        self.assertEqual(migrate.up(TEST_DB, make_backup=False), sorted(undone))
        self.assertIn("stock_receipts", self.tables())
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM sales_orders")[0]["n"], 36)   # data kept

    def test_rolling_back_001_removes_the_columns_and_up_adds_them(self):
        self.roll_back_to_before("001")
        self.assertNotIn("safety_stock", self.columns("products"))
        self.assertNotIn("updated_at", self.columns("users"))
        self.assertEqual(migrate.up(TEST_DB, make_backup=False), self.all_versions())
        self.assertIn("safety_stock", self.columns("products"))
        self.assertEqual(migrate.up(TEST_DB, make_backup=False), [])                    # nothing left

    def test_every_migration_has_a_rollback(self):
        for version, name, up_path, down_path in migrate.available():
            self.assertTrue(os.path.exists(down_path), f"{version}_{name} has no .down.sql")
            self.assertTrue(migrate.read_sql(down_path))


class TestBackupAndRestore(OperationsTestCase):
    def test_restore_brings_back_deleted_rows(self):
        path = backup.create_backup(TEST_DB, folder=self.folder)
        conn = connect(TEST_DB)                         # an accident: every sale deleted
        with conn, conn.cursor() as cur:
            cur.execute("DELETE FROM sales_orders")
            conn.commit()
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM sales_orders")[0]["n"], 0)
        backup.restore(path, TEST_DB)
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM sales_orders")[0]["n"], 36)
        self.assertEqual(self.sql("SELECT current_stock FROM products WHERE id = 1")[0]["current_stock"], 48)

    def test_restore_undoes_a_migration_made_after_the_backup(self):
        self.roll_back_to_before("002")                   # state: only 001
        path = backup.create_backup(TEST_DB, folder=self.folder)
        migrate.up(TEST_DB, make_backup=False)            # 002 adds stock_receipts
        backup.restore(path, TEST_DB)
        self.assertNotIn("stock_receipts", self.tables())
        self.assertIn("002_stock_receipts", migrate.status(TEST_DB)["pending"])

    def test_text_with_quotes_and_semicolons_survives(self):
        conn = connect(TEST_DB)
        with conn, conn.cursor() as cur:
            cur.execute("UPDATE products SET name = %s WHERE id = 1", ("Lager; O'Brien's \"500ml\"",))
            conn.commit()
        path = backup.create_backup(TEST_DB, folder=self.folder)
        backup.restore(path, TEST_DB)
        self.assertEqual(self.sql("SELECT name FROM products WHERE id = 1")[0]["name"], "Lager; O'Brien's \"500ml\"")

    def test_only_backup_files_can_be_restored(self):
        other = os.path.join(self.folder, "notes.sql")
        with open(other, "w") as f:
            f.write("DROP TABLE products;\n")
        with self.assertRaises(SystemExit):
            backup.restore(other, TEST_DB)
        self.assertIn("products", self.tables())


class TestHealthAndAudit(OperationsTestCase):
    def test_health_reports_ok(self):
        res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        body = res.get_json()["data"]
        self.assertEqual((body["status"], body["database"], body["environment"]), ("ok", "ok", "testing"))
        self.assertEqual(body["migrations"]["pending"], [])

    def test_health_shows_pending_migrations(self):
        latest = migrate.down(TEST_DB, make_backup=False)
        body = self.client.get("/api/health").get_json()["data"]
        self.assertEqual(body["status"], "degraded")
        self.assertEqual([m[:3] for m in body["migrations"]["pending"]], [latest])

    def test_health_is_503_when_the_database_is_down(self):
        with mock.patch("db.connect", side_effect=pymysql.err.OperationalError(2003, "down")), \
                self.assertLogs("taphouse", level="ERROR"):
            res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.get_json()["data"]["database"], "unreachable")

    def test_receiving_stock_is_recorded_with_who_and_how_many(self):
        self.client.post("/api/auth/login", json=MANAGER)
        self.client.post("/api/products/1/receive", json={"quantity": 532})
        receipts = self.client.get("/api/products/1/receipts").get_json()["data"]
        self.assertEqual(len(receipts), 1)
        self.assertEqual((receipts[0]["quantity"], receipts[0]["received_by"]), (532, "TapHouse Manager"))

    def test_failed_sign_in_is_logged_without_the_password(self):
        with self.assertLogs("taphouse", level="WARNING") as logs:
            self.client.post("/api/auth/login", json={**MANAGER, "password": "Secret-Guess-1"})
        text = "\n".join(logs.output)
        self.assertIn("Failed sign-in", text)
        self.assertNotIn("Secret-Guess-1", text)


class TestProductionSettings(unittest.TestCase):
    def test_production_refuses_a_placeholder_secret_key(self):
        with mock.patch.object(config.ProductionConfig, "SECRET_KEY", "change-me-to-a-long-random-string"):
            with self.assertRaises(RuntimeError):
                config.get_config("production")

    def test_production_settings(self):
        with mock.patch.object(config.ProductionConfig, "SECRET_KEY", "x" * 64):
            settings = config.get_config("production")
        self.assertFalse(settings.DEBUG)
        self.assertTrue(settings.SESSION_COOKIE_SECURE)
        self.assertEqual(settings.LOG_LEVEL, os.getenv("LOG_LEVEL", "WARNING"))


if __name__ == "__main__":
    unittest.main()
