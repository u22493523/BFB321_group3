"""SQL queries and supply chain business rules. app.py calls these functions;
it never contains raw SQL itself, and nothing in here knows about HTML."""
from db import query_all, query_one
# Step 2 formulas live in supply_chain.py (no database code in there)
from supply_chain import calculate_eoq


# =====================================================================
# Products & Stock Status
# =====================================================================
def stock_status(current_stock, reorder_point):
    """Return (label, Bootstrap badge class, needs_order) calculated from live stock."""
    if current_stock == 0 or current_stock <= (reorder_point * 0.6):
        return "URGENT", "bg-danger", True
    if current_stock <= reorder_point:
        return "REORDER", "bg-warning text-dark", True
    return "OK", "bg-success", False


def _enrich_products(rows):
    """Attach live status badges and Step 2 EOQ recommended order quantities."""
    for row in rows:
        status, badge, needs_order = stock_status(row["current_stock"], row["reorder_point"])
        row["status"] = status
        row["badge"] = badge
        if needs_order and float(row["holding_cost"]) > 0 and row["annual_demand"] > 0:
            eoq = calculate_eoq(
                row["annual_demand"],
                float(row["order_cost"]),
                float(row["holding_cost"]),
            )
            row["order_qty"] = eoq if eoq > 0 else row["reorder_point"]
        elif needs_order:
            row["order_qty"] = row["reorder_point"]
        else:
            row["order_qty"] = "-"
    return rows


PRODUCT_SELECT = """
    SELECT p.id, p.sku, p.name, p.category, p.supplier_id,
           s.name AS supplier_name, s.lead_time_days, s.std_dev_lead_time,
           p.current_stock, p.unit_cost, p.annual_demand,
           p.order_cost, p.holding_cost, p.avg_daily_demand,
           p.std_dev_demand, p.reorder_point, p.image_filename
    FROM products p
    JOIN suppliers s ON s.id = p.supplier_id
"""


def list_products(search="", category=None):
    """Return all beverage products joined with their supplier details."""
    sql = PRODUCT_SELECT + " WHERE 1=1"
    params = []
    if search:
        sql += " AND (p.name LIKE %s OR p.sku LIKE %s)"
        params.extend([f"%{search}%", f"%{search}%"])
    if category:
        sql += " AND p.category = %s"
        params.append(category)
    sql += " ORDER BY p.sku ASC"
    return _enrich_products(query_all(sql, params))


def get_product(product_id):
    """Return one product by primary key id."""
    row = query_one(PRODUCT_SELECT + " WHERE p.id = %s", (product_id,))
    return _enrich_products([row])[0] if row else None


def get_reorder_products():
    """Return products nearing/below ROP (plus key tracked items) for the dashboard table."""
    sql = PRODUCT_SELECT + """
        WHERE p.current_stock <= p.reorder_point
           OR p.sku IN ('BEV-001', 'BEV-002', 'BEV-004')
        ORDER BY (p.current_stock - p.reorder_point) ASC, p.sku ASC
    """
    return _enrich_products(query_all(sql))


# =====================================================================
# Suppliers
# =====================================================================
def list_suppliers():
    """Return all suppliers ordered alphabetically."""
    return query_all("""
        SELECT id, name, lead_time_days, std_dev_lead_time, contact_email
        FROM suppliers
        ORDER BY name ASC
    """)


# =====================================================================
# Dashboard KPIs, 7-Day Forecast & Alerts
# =====================================================================
def dashboard_kpis():
    """Compute live dashboard KPI cards, 7-day demand chart, and dynamic alerts from MySQL."""
    products = list_products()
    total_products = len(products)

    inv_row = query_one(
        "SELECT COALESCE(SUM(current_stock * unit_cost), 0) AS total_val FROM products"
    )
    avg_inventory_value = float(inv_row["total_val"]) if inv_row else 0.0

    stockout_row = query_one(
        "SELECT COUNT(*) AS n FROM products WHERE current_stock = 0"
    )
    stockout_events = int(stockout_row["n"]) if stockout_row else 0

    urgent_items = [p for p in products if p["status"] == "URGENT"]
    reorder_items = [p for p in products if p["status"] == "REORDER"]
    emergency_orders = len(urgent_items)

    if total_products > 0:
        healthy_count = total_products - len(urgent_items)
        service_level = round((healthy_count / total_products) * 100)
    else:
        service_level = 100

    # 7-day demand distribution pulled from historical sales_orders in MySQL
    sales_by_dow = query_all("""
        SELECT DAYOFWEEK(sale_date) AS dow, COALESCE(SUM(quantity_sold), 0) AS total_qty
        FROM sales_orders
        GROUP BY DAYOFWEEK(sale_date)
    """)
    # MySQL DAYOFWEEK: 1=Sun, 2=Mon, 3=Tue, 4=Wed, 5=Thu, 6=Fri, 7=Sat
    dow_map = {int(r["dow"]): int(r["total_qty"]) for r in sales_by_dow}
    ordered_dows = [2, 3, 4, 5, 6, 7, 1]
    forecast_7day = [dow_map.get(d, 0) for d in ordered_dows]

    # Dynamic alerts generated from live database stock & lead times
    alerts = []
    for item in urgent_items:
        alerts.append({
            "type": "danger",
            "prefix": "Urgent:",
            "message": (
                f"{item['name']} below safety stock ({item['current_stock']} units left) - "
                f"recommended order: {item['order_qty']} units "
                f"(supplier {item['supplier_name']} lead time {item['lead_time_days']} days)."
            ),
        })
    for item in reorder_items:
        alerts.append({
            "type": "warning",
            "prefix": "Warning:",
            "message": (
                f"{item['name']} ({item['current_stock']} units) is at or below ROP "
                f"({item['reorder_point']} units) before next {item['supplier_name']} delivery."
            ),
        })
    alerts.append({
        "type": "info",
        "prefix": "Info:",
        "message": "Demand forecast elevated: home rugby match this Saturday (+38% vs. baseline).",
    })

    return {
        "service_level": service_level,
        "stockout_events": stockout_events,
        "avg_inventory_value": f"{avg_inventory_value:,.2f}",
        "emergency_orders": emergency_orders,
        "forecast_7day": forecast_7day,
        "alerts": alerts,
    }
