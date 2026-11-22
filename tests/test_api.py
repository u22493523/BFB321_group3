"""API tests (Step 4): call every endpoint through Flask's test client and
check the result in MySQL.

They use a separate database (TEST_DB_NAME in .env, default
taphouse_didss_test) that is wiped and rebuilt from schema.sql and seed.sql
before every test, so your real data is never touched. If MySQL is not
running, these tests are skipped and the unit tests still run.

    python -m unittest discover -s tests -p "test_*.py"
"""
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from decimal import Decimal
from unittest import mock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["APP_ENV"] = "testing"          # must be set before app is imported

import pymysql  # noqa: E402

import init_db  # noqa: E402
from app import app  # noqa: E402
from db import connect  # noqa: E402

TEST_DB = app.config["DB_NAME"]
NEW_PRODUCT = {"sku": "BEV-013", "name": "Craft Stout 440ml", "category": "Beer", "supplier_id": 1,
               "unit_cost": 24.50, "current_stock": 30, "annual_demand": 1825, "order_cost": 120,
               "holding_cost": 4.90, "avg_daily_demand": 5, "std_dev_demand": 1.8}


def setUpModule():
    """Create the empty test database once; skip everything if MySQL is down."""
    try:
        conn = pymysql.connect(host=app.config["DB_HOST"], port=app.config["DB_PORT"],
                               user=app.config["DB_USER"], password=app.config["DB_PASSWORD"])
    except pymysql.err.OperationalError as exc:
        raise unittest.SkipTest(f"MySQL not reachable, API tests skipped: {exc}")
    with conn, conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{TEST_DB}`")


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        with redirect_stdout(io.StringIO()):          # keep init_db's printout out of test output
            init_db.main(reset=True, database=TEST_DB)
        self.client = app.test_client()

    # --- helpers -----------------------------------------------------------
    def sql_one(self, sql, params=()):
        """Read straight from MySQL to prove a change really persisted."""
        conn = connect(TEST_DB)
        with conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def post(self, url, body):
        return self.client.post(url, json=body)

    def patch(self, url, body):
        return self.client.patch(url, json=body)


class TestIndexAndErrors(ApiTestCase):
    def test_index_lists_at_least_five_endpoints(self):
        res = self.client.get("/api")
        self.assertEqual(res.status_code, 200)
        self.assertGreaterEqual(len(res.get_json()["data"]), 5)

    def test_unknown_url_and_wrong_method_return_json(self):
        self.assertEqual(self.client.get("/api/nothing-here").get_json()["error"]["code"], 404)
        self.assertEqual(self.client.delete("/api/products/1").get_json()["error"]["code"], 405)

    def test_server_error_is_clean_json_and_logged(self):
        with mock.patch("models.dashboard_api", side_effect=RuntimeError("boom")), \
                self.assertLogs("taphouse", level="ERROR") as logs:
            res = self.client.get("/api/dashboard")
        self.assertEqual(res.status_code, 500)
        self.assertEqual(res.get_json()["error"]["message"], "Something went wrong on the server.")
        self.assertNotIn("Traceback", res.get_data(as_text=True))       # no stack trace to the user
        self.assertIn("boom", "\n".join(logs.output))                   # but it is in the log


class TestProducts(ApiTestCase):
    def test_list_is_paged(self):
        body = self.client.get("/api/products?page=2&size=5").get_json()
        self.assertEqual(body["meta"], {"page": 2, "size": 5, "total": 12, "pages": 3})
        self.assertEqual(len(body["data"]), 5)

    def test_list_filters(self):
        wine = self.client.get("/api/products?category=Wine").get_json()["data"]
        self.assertEqual({p["category"] for p in wine}, {"Wine"})
        reorder = self.client.get("/api/products?status=reorder").get_json()["data"]
        self.assertTrue(reorder and all(p["status"] == "REORDER" for p in reorder))
        self.assertEqual([p["sku"] for p in self.client.get("/api/products?q=tonic").get_json()["data"]],
                         ["BEV-004"])

    def test_bad_query_parameters_return_400(self):
        res = self.client.get("/api/products?page=0&size=500&status=MAYBE&sort=price")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(set(res.get_json()["error"]["details"]), {"page", "size", "status", "sort"})

    def test_get_one_and_404(self):
        self.assertEqual(self.client.get("/api/products/1").get_json()["data"]["sku"], "BEV-001")
        self.assertEqual(self.client.get("/api/products/999").status_code, 404)

    def test_create_persists_and_model_sets_reorder_point(self):
        res = self.post("/api/products", NEW_PRODUCT)
        self.assertEqual(res.status_code, 201)
        self.assertTrue(res.headers["Location"].endswith(f"/api/products/{res.get_json()['data']['id']}"))
        row = self.sql_one("SELECT safety_stock, reorder_point FROM products WHERE sku = 'BEV-013'")
        # SS = 1.645 * sqrt(4 * 1.8^2 + 5^2 * 1^2) = 1.645 * 6.11 = 10.05 -> 11 ; ROP = 5*4 + 11 = 31
        self.assertEqual(row, {"safety_stock": 11, "reorder_point": 31})

    def test_duplicate_sku_is_409_not_a_crash(self):
        res = self.post("/api/products", {**NEW_PRODUCT, "sku": "BEV-001"})
        self.assertEqual(res.status_code, 409)
        self.assertIn("sku", res.get_json()["error"]["details"])

    def test_validation_lists_every_bad_field(self):
        res = self.post("/api/products", {"sku": "x", "supplier_id": "two", "unit_cost": -1, "colour": "red"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(set(res.get_json()["error"]["details"]),
                         {"sku", "name", "category", "supplier_id", "unit_cost", "colour"})
        self.assertEqual(self.post("/api/products", {**NEW_PRODUCT, "supplier_id": 99}).status_code, 400)
        self.assertEqual(self.client.post("/api/products", data="{not json",
                                          content_type="application/json").status_code, 400)

    def test_update_main_entity_persists(self):
        res = self.patch("/api/products/1", {"current_stock": 5, "unit_cost": 16.25})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["data"]["status"], "URGENT")       # 5 <= safety stock 20
        self.assertEqual(self.sql_one("SELECT current_stock, unit_cost FROM products WHERE id = 1"),
                         {"current_stock": 5, "unit_cost": Decimal("16.25")})

    def test_update_demand_recalculates_and_manual_reorder_point(self):
        recalculated = self.patch("/api/products/1", {"avg_daily_demand": 20}).get_json()["data"]
        self.assertGreater(recalculated["reorder_point"], 60)
        manual = self.patch("/api/products/1", {"reorder_point": 90}).get_json()["data"]
        self.assertEqual(manual["reorder_point"], 90)

    def test_update_rejects_empty_and_calculated_fields(self):
        self.assertEqual(self.patch("/api/products/1", {}).status_code, 400)
        self.assertEqual(self.patch("/api/products/1", {"safety_stock": 1}).status_code, 400)
        self.assertEqual(self.patch("/api/products/999", {"current_stock": 1}).status_code, 404)

    def test_recommendation_and_recalculate_use_the_model(self):
        at_95 = self.client.get("/api/products/2/recommendation").get_json()["data"]
        at_99 = self.client.get("/api/products/2/recommendation?service_level=0.99").get_json()["data"]
        self.assertEqual(at_95["safety_stock"], 19)                         # matches init_db at 95%
        self.assertGreater(at_99["safety_stock"], at_95["safety_stock"])
        self.assertEqual(self.client.get("/api/products/2/recommendation?service_level=0.5").status_code, 400)
        saved = self.client.post("/api/products/2/recalculate?service_level=0.99").get_json()["data"]
        self.assertEqual(saved["safety_stock"], at_99["safety_stock"])
        self.assertEqual(self.sql_one("SELECT safety_stock FROM products WHERE id = 2")["safety_stock"],
                         at_99["safety_stock"])


class TestSales(ApiTestCase):
    def test_sale_reduces_stock_in_mysql(self):
        res = self.post("/api/sales", {"product_id": 3, "quantity_sold": 4, "sale_date": "2026-11-20"})
        self.assertEqual(res.status_code, 201)
        data = res.get_json()["data"]
        self.assertEqual(data["product"]["current_stock"], 20)              # 24 - 4
        self.assertEqual(data["sale"]["unit_price"], 135.0)                  # latest selling price
        self.assertEqual(self.sql_one("SELECT current_stock FROM products WHERE id = 3")["current_stock"], 20)

    def test_overselling_is_refused_and_nothing_changes(self):
        res = self.post("/api/sales", {"product_id": 3, "quantity_sold": 500})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(self.sql_one("SELECT current_stock FROM products WHERE id = 3")["current_stock"], 24)
        self.assertEqual(self.sql_one("SELECT COUNT(*) AS n FROM sales_orders")["n"], 36)

    def test_failed_write_is_rolled_back(self):
        """If reducing stock fails after the sale row is inserted, the sale must not be saved."""
        real_execute = pymysql.cursors.Cursor.execute

        def fail_on_stock_update(cursor, query, args=None):
            if query.startswith("UPDATE products SET current_stock"):
                raise RuntimeError("simulated failure")
            return real_execute(cursor, query, args)

        with mock.patch.object(pymysql.cursors.Cursor, "execute", fail_on_stock_update), \
                self.assertLogs("taphouse", level="ERROR"):
            res = self.post("/api/sales", {"product_id": 3, "quantity_sold": 2})
        self.assertEqual(res.status_code, 500)
        self.assertEqual(self.sql_one("SELECT COUNT(*) AS n FROM sales_orders")["n"], 36)
        self.assertEqual(self.sql_one("SELECT current_stock FROM products WHERE id = 3")["current_stock"], 24)

    def test_sale_validation(self):
        self.assertEqual(self.post("/api/sales", {"product_id": 3}).status_code, 400)
        self.assertEqual(self.post("/api/sales", {"product_id": 3, "quantity_sold": 0}).status_code, 400)
        self.assertEqual(self.post("/api/sales", {"product_id": 999, "quantity_sold": 1}).status_code, 400)
        future = self.post("/api/sales", {"product_id": 3, "quantity_sold": 1, "sale_date": "2099-01-01"})
        self.assertEqual(future.get_json()["error"]["details"]["sale_date"], "The date cannot be in the future.")

    def test_sales_list_filters_and_pages(self):
        body = self.client.get("/api/sales?product_id=1&from=2026-11-01&to=2026-11-30&size=2").get_json()
        self.assertEqual(body["meta"]["total"], 4)        # Lager sold on 4, 7, 12 and 14 November
        self.assertEqual(len(body["data"]), 2)
        self.assertTrue(all(s["product_id"] == 1 for s in body["data"]))
        self.assertEqual(self.client.get("/api/sales?from=2026-12-01&to=2026-11-01").status_code, 400)
        self.assertEqual(self.client.get("/api/sales?from=yesterday").status_code, 400)

    def test_get_sale_and_form_post(self):
        self.assertEqual(self.client.get("/api/sales/1").get_json()["data"]["product_id"], 1)
        self.assertEqual(self.client.get("/api/sales/999").status_code, 404)
        form = self.client.post("/api/sales", data={"product_id": "2", "quantity_sold": "3"})
        self.assertEqual(form.status_code, 201)


class TestSuppliersAndDashboard(ApiTestCase):
    def test_create_supplier_and_duplicates(self):
        res = self.post("/api/suppliers", {"name": "Craft Co", "lead_time_days": 6, "contact_email": "a@craft.co.za"})
        self.assertEqual(res.status_code, 201)
        self.assertEqual(self.post("/api/suppliers", {"name": "SAB", "lead_time_days": 3,
                                                      "contact_email": "x@y.co"}).status_code, 409)
        bad = self.post("/api/suppliers", {"name": "New", "lead_time_days": 0, "contact_email": "nope"})
        self.assertEqual(set(bad.get_json()["error"]["details"]), {"lead_time_days", "contact_email"})

    def test_new_lead_time_recalculates_that_suppliers_products(self):
        before = self.sql_one("SELECT reorder_point FROM products WHERE id = 4")["reorder_point"]
        res = self.patch("/api/suppliers/4", {"lead_time_days": 7})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["data"]["lead_time_days"], 7)
        after = self.sql_one("SELECT reorder_point FROM products WHERE id = 4")["reorder_point"]
        self.assertGreater(after, before)

    def test_dashboard_kpis_respond_to_changes(self):
        before = self.client.get("/api/dashboard").get_json()["data"]
        self.post("/api/sales", {"product_id": 2, "quantity_sold": 100})    # Cider 112 -> 12
        after = self.client.get("/api/dashboard").get_json()["data"]
        self.assertLess(after["inventory_value"], before["inventory_value"])
        self.assertEqual(after["status_counts"]["URGENT"], before["status_counts"]["URGENT"] + 1)
        self.assertEqual(after["counts"]["sales"], before["counts"]["sales"] + 1)


if __name__ == "__main__":
    unittest.main()
