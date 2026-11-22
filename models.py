"""SQL queries and supply chain business rules. app.py and api.py call these
functions; they never contain raw SQL themselves, and nothing in here knows
about HTML. Every query uses %s placeholders, never string joining."""
import math
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from db import execute, query_all, query_one, transaction
from errors import ConflictError, NotFoundError, ValidationError
# Step 2 formulas live in supply_chain.py (no database code in there)
from supply_chain import DEFAULT_SERVICE_LEVEL, calculate_eoq, recommend


# =====================================================================
# Products & Stock Status
# =====================================================================
def stock_status(current_stock, safety_stock, reorder_point):
    """Return (label, Bootstrap badge class, needs_order) calculated from live stock.

    URGENT  stock at or below safety stock (the buffer is already being used)
    REORDER stock at or below the reorder point
    OK      otherwise
    """
    if current_stock <= safety_stock:
        return "URGENT", "bg-danger", True
    if current_stock <= reorder_point:
        return "REORDER", "bg-warning text-dark", True
    return "OK", "bg-success", False


def _enrich_products(rows):
    """Attach live status badges and Step 2 EOQ recommended order quantities."""
    for row in rows:
        status, badge, needs_order = stock_status(
            row["current_stock"], row["safety_stock"], row["reorder_point"])
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
           p.std_dev_demand, p.safety_stock, p.reorder_point, p.image_filename
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
    """Return products at or below their reorder point, most urgent first (live data only)."""
    sql = PRODUCT_SELECT + """
        WHERE p.current_stock <= p.reorder_point
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
    # Elevated demand: last 7 days of sales vs the expected 7 x average daily demand.
    # The 7 days end at the latest sale in the database, so the demo data keeps working.
    recent = query_all("""
        SELECT p.name, p.avg_daily_demand, COALESCE(SUM(o.quantity_sold), 0) AS qty_7d
        FROM products p
        LEFT JOIN sales_orders o
               ON o.product_id = p.id
              AND o.sale_date > (SELECT MAX(sale_date) FROM sales_orders) - INTERVAL 7 DAY
        GROUP BY p.id, p.name, p.avg_daily_demand
    """)
    for r in recent:
        expected = 7 * r["avg_daily_demand"]
        if expected > 0 and r["qty_7d"] > 1.2 * expected:
            uplift = round((r["qty_7d"] / expected - 1) * 100)
            alerts.append({
                "type": "info",
                "prefix": "Info:",
                "message": (f"Demand elevated: {r['name']} sold {int(r['qty_7d'])} units in the last "
                            f"7 days, {uplift}% above the expected {expected}."),
            })

    return {
        "service_level": service_level,
        "stockout_events": stockout_events,
        "avg_inventory_value": f"{avg_inventory_value:,.2f}",
        "emergency_orders": emergency_orders,
        "forecast_7day": forecast_7day,
        "alerts": alerts,
    }


# =====================================================================
# Step 4: validation rules for API input
# =====================================================================
SKU_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9-]{2,19}$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_MONEY = Decimal("99999999.99")      # DECIMAL(10,2)
MAX_STD_DEV = Decimal("9999.99")        # DECIMAL(6,2)

# field -> (kind, options). kind is "int", "money", "decimal", "text" or "email".
PRODUCT_RULES = {
    "sku": ("text", {"max_len": 20, "upper": True, "pattern": SKU_PATTERN,
                     "pattern_msg": "Use 3-20 capital letters, digits or dashes, e.g. BEV-013."}),
    "name": ("text", {"max_len": 120}),
    "category": ("text", {"max_len": 60}),
    "supplier_id": ("int", {"minimum": 1}),
    "current_stock": ("int", {"minimum": 0, "maximum": 1_000_000}),
    "unit_cost": ("money", {}),
    "annual_demand": ("int", {"minimum": 0, "maximum": 10_000_000}),
    "order_cost": ("money", {}),
    "holding_cost": ("money", {}),
    "avg_daily_demand": ("int", {"minimum": 0, "maximum": 100_000}),
    "std_dev_demand": ("decimal", {"maximum": MAX_STD_DEV}),
    "reorder_point": ("int", {"minimum": 0, "maximum": 1_000_000}),   # manual override (PATCH)
    "image_filename": ("text", {"max_len": 120, "nullable": True}),
}
PRODUCT_REQUIRED = {"sku", "name", "category", "supplier_id", "unit_cost"}
PRODUCT_DEFAULTS = {"current_stock": 0, "annual_demand": 0, "order_cost": Decimal("0"),
                    "holding_cost": Decimal("0"), "avg_daily_demand": 0,
                    "std_dev_demand": Decimal("0"), "image_filename": None}
# Changing any of these changes the model's answer, so SS and ROP are recalculated
DEMAND_FIELDS = {"avg_daily_demand", "std_dev_demand", "supplier_id"}

SUPPLIER_RULES = {
    "name": ("text", {"max_len": 100}),
    "lead_time_days": ("int", {"minimum": 1, "maximum": 365}),
    "std_dev_lead_time": ("decimal", {"maximum": MAX_STD_DEV}),
    "contact_email": ("email", {"max_len": 160}),
}
SUPPLIER_REQUIRED = {"name", "lead_time_days", "contact_email"}
LEAD_TIME_FIELDS = {"lead_time_days", "std_dev_lead_time"}

SALE_RULES = {
    "product_id": ("int", {"minimum": 1}),
    "quantity_sold": ("int", {"minimum": 1, "maximum": 100_000}),
    "unit_price": ("money", {}),
    "sale_date": ("date", {}),
}
SALE_REQUIRED = {"product_id", "quantity_sold"}


def _to_int(value):
    """'12', 12 and 12.0 are whole numbers; True, 12.5 and 'abc' are not."""
    if isinstance(value, bool):
        raise ValueError
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError
        return int(value)
    return int(str(value).strip())


def _to_decimal(value):
    if isinstance(value, bool):
        raise ValueError
    number = Decimal(str(value).strip())
    if not number.is_finite():
        raise ValueError
    return number


def _to_date(value):
    """Accept 2026-11-21 or 2026-11-21T18:30:00; dates in the future are refused."""
    text = str(value).strip()
    now = datetime.now()
    if "T" in text or " " in text:
        parsed = datetime.fromisoformat(text)
    else:
        # a date on its own is stored at midday (or "now" for today's sales logged before midday)
        day = date.fromisoformat(text)
        parsed = datetime.combine(day, datetime.min.time().replace(hour=12))
        if day == now.date():
            parsed = min(parsed, now)
    if parsed > now:
        raise ValueError("future")
    return parsed


def _check_field(name, kind, options, value):
    """Return (clean value, error message or None) for one field."""
    if value is None or (isinstance(value, str) and not value.strip()):
        if options.get("nullable"):
            return None, None
        return None, "This field is required."
    try:
        if kind == "int":
            number = _to_int(value)
            if number < options.get("minimum", 0):
                return None, f"Must be {options.get('minimum', 0)} or more."
            if "maximum" in options and number > options["maximum"]:
                return None, f"Must be {options['maximum']} or less."
            return number, None
        if kind in ("money", "decimal"):
            number = _to_decimal(value)
            limit = options.get("maximum", MAX_MONEY)
            if number < 0:
                return None, "Cannot be negative."
            if number > limit:
                return None, f"Must be {limit} or less."
            if number.as_tuple().exponent < -2:
                return None, "Use at most 2 decimal places."
            return number, None
        if kind == "date":
            return _to_date(value), None
        if not isinstance(value, str):
            return None, "Must be text."
        text = value.strip().upper() if options.get("upper") else value.strip()
        if len(text) > options.get("max_len", 255):
            return None, f"Use {options['max_len']} characters or fewer."
        if kind == "email" and not EMAIL_PATTERN.match(text):
            return None, "Enter a valid email address, e.g. orders@sab.co.za."
        if options.get("pattern") and not options["pattern"].match(text):
            return None, options.get("pattern_msg", "Wrong format.")
        return text, None
    except (ValueError, TypeError, InvalidOperation) as exc:
        if kind == "date":
            if str(exc) == "future":
                return None, "The date cannot be in the future."
            return None, "Use the format YYYY-MM-DD."
        return None, "Must be a whole number." if kind == "int" else "Must be a number."


def validate(data, rules, required=(), partial=False, read_only=()):
    """Check a JSON body against `rules`. Returns clean values or raises a 400
    ValidationError listing every problem at once (not just the first)."""
    if not isinstance(data, dict):
        raise ValidationError({"body": "Send a JSON object, e.g. {\"name\": \"...\"}."})
    errors, values = {}, {}
    for name in data:
        if name in read_only:
            errors[name] = "This value is calculated by the system and cannot be set."
        elif name not in rules:
            errors[name] = "Unknown field."
    for name, (kind, options) in rules.items():
        if name in read_only:
            continue
        if name not in data:
            if not partial and name in required:
                errors[name] = "This field is required."
            continue
        value, error = _check_field(name, kind, options, data[name])
        if error:
            errors[name] = error
        else:
            values[name] = value
    if partial and not data:
        errors["body"] = "Send at least one field to change."
    if errors:
        raise ValidationError(errors)
    return values


def to_json(row):
    """Make a database row safe for jsonify: Decimal -> float, dates -> ISO text."""
    out = {}
    for key, value in row.items():
        if key == "badge":                       # HTML class, pages only
            continue
        if isinstance(value, Decimal):
            value = float(value)
        elif isinstance(value, (datetime, date)):
            value = value.isoformat()
        elif key == "order_qty":
            key, value = "recommended_order_qty", (value if isinstance(value, int) else 0)
        out[key] = value
    return out


def _page_meta(page, size, total):
    return {"page": page, "size": size, "total": total, "pages": math.ceil(total / size) if size else 0}


# =====================================================================
# Step 4: products (create, read one/many, update, model recommendation)
# =====================================================================
# Same rule as stock_status(), written in SQL so the database can filter by it
STATUS_SQL = """CASE WHEN p.current_stock <= p.safety_stock THEN 'URGENT'
                     WHEN p.current_stock <= p.reorder_point THEN 'REORDER'
                     ELSE 'OK' END"""
PRODUCT_SORTS = {
    "sku": "p.sku", "-sku": "p.sku DESC", "name": "p.name", "-name": "p.name DESC",
    "stock": "p.current_stock", "-stock": "p.current_stock DESC",
    "status": f"FIELD({STATUS_SQL}, 'URGENT', 'REORDER', 'OK'), p.sku",
}


def list_products_page(page=1, size=10, search="", category=None, status=None,
                       supplier_id=None, sort="sku"):
    """One page of products plus paging details, filtered in SQL."""
    where, params = ["1=1"], []
    if search:
        where.append("(p.name LIKE %s OR p.sku LIKE %s)")
        params += [f"%{search}%", f"%{search}%"]
    if category:
        where.append("p.category = %s")
        params.append(category)
    if supplier_id:
        where.append("p.supplier_id = %s")
        params.append(supplier_id)
    if status:
        where.append(f"{STATUS_SQL} = %s")
        params.append(status)
    clause = " WHERE " + " AND ".join(where)
    total = query_one("SELECT COUNT(*) AS n FROM products p JOIN suppliers s ON s.id = p.supplier_id"
                      + clause, params)["n"]
    rows = query_all(PRODUCT_SELECT + clause + f" ORDER BY {PRODUCT_SORTS[sort]} LIMIT %s OFFSET %s",
                     params + [size, (page - 1) * size])
    return {"items": [to_json(r) for r in _enrich_products(rows)], "meta": _page_meta(page, size, total)}


def require_product(product_id):
    product = get_product(product_id)
    if product is None:
        raise NotFoundError(f"Product {product_id} not found.")
    return product


def _supplier_or_error(supplier_id):
    supplier = query_one("SELECT id, lead_time_days, std_dev_lead_time FROM suppliers WHERE id = %s",
                         (supplier_id,))
    if supplier is None:
        raise ValidationError({"supplier_id": f"Supplier {supplier_id} does not exist."})
    return supplier


def _model_output(product, supplier, service_level):
    """Run the Step 2 model on one product's demand data and its supplier's lead time."""
    return recommend(
        avg_demand=product["avg_daily_demand"], std_dev_demand=float(product["std_dev_demand"]),
        avg_lead_time=supplier["lead_time_days"], std_dev_lead_time=float(supplier["std_dev_lead_time"]),
        annual_demand=product["annual_demand"], order_cost=float(product["order_cost"]),
        holding_cost=float(product["holding_cost"]), service_level=service_level)


def create_product(data, service_level=DEFAULT_SERVICE_LEVEL):
    """POST /api/products: validate, insert, and let the model set SS and ROP."""
    values = validate(data, PRODUCT_RULES, PRODUCT_REQUIRED,
                      read_only={"safety_stock", "reorder_point", "id"})
    values = {**PRODUCT_DEFAULTS, **values}
    if query_one("SELECT id FROM products WHERE sku = %s", (values["sku"],)):
        raise ConflictError("A product with this SKU already exists.",
                            {"sku": f"{values['sku']} is already used."})
    supplier = _supplier_or_error(values["supplier_id"])
    model = _model_output(values, supplier, service_level)
    columns = list(values) + ["safety_stock", "reorder_point"]
    with transaction() as cur:
        cur.execute(f"INSERT INTO products ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})",
                    [values[c] for c in values] + [model["safety_stock"], model["reorder_point"]])
        new_id = cur.lastrowid
    return to_json(get_product(new_id))


def update_product(product_id, data, service_level=DEFAULT_SERVICE_LEVEL):
    """PATCH /api/products/<id>: change only the fields sent. If demand data or
    the supplier changes, the model recalculates SS and ROP (unless a reorder
    point is set by hand in the same request)."""
    current = require_product(product_id)
    values = validate(data, PRODUCT_RULES, partial=True, read_only={"safety_stock", "id"})
    if "sku" in values and query_one("SELECT id FROM products WHERE sku = %s AND id <> %s",
                                     (values["sku"], product_id)):
        raise ConflictError("A product with this SKU already exists.",
                            {"sku": f"{values['sku']} is already used."})
    if "supplier_id" in values:
        _supplier_or_error(values["supplier_id"])
    if DEMAND_FIELDS & set(values) and "reorder_point" not in values:
        merged = {**current, **values}
        model = _model_output(merged, _supplier_or_error(merged["supplier_id"]), service_level)
        values["safety_stock"], values["reorder_point"] = model["safety_stock"], model["reorder_point"]
    assignments = ", ".join(f"{column} = %s" for column in values)
    execute(f"UPDATE products SET {assignments} WHERE id = %s", list(values.values()) + [product_id])
    return to_json(get_product(product_id))


def product_recommendation(product_id, service_level=DEFAULT_SERVICE_LEVEL, demand_change=0,
                           lead_time_days=None):
    """GET /api/products/<id>/recommendation: what the model advises, without saving.
    demand_change (percent) and lead_time_days let the manager test a scenario."""
    product = dict(require_product(product_id))
    if demand_change:
        factor = 1 + demand_change / 100
        product["avg_daily_demand"] = math.ceil(product["avg_daily_demand"] * factor)
        product["std_dev_demand"] = Decimal(str(round(float(product["std_dev_demand"]) * factor, 2)))
        product["annual_demand"] = math.ceil(product["annual_demand"] * factor)
    if lead_time_days:
        product["lead_time_days"] = lead_time_days
    model = _model_output(product, product, service_level)
    status, _badge, needs_order = stock_status(product["current_stock"], model["safety_stock"],
                                               model["reorder_point"])
    order_qty = 0
    if needs_order:
        order_qty = model["eoq"] or max(model["reorder_point"] - product["current_stock"], 0)
    return {
        "product_id": product["id"], "sku": product["sku"], "name": product["name"],
        "inputs": {
            "avg_daily_demand": product["avg_daily_demand"],
            "std_dev_demand": float(product["std_dev_demand"]),
            "lead_time_days": product["lead_time_days"],
            "std_dev_lead_time": float(product["std_dev_lead_time"]),
            "annual_demand": product["annual_demand"],
            "order_cost": float(product["order_cost"]),
            "holding_cost": float(product["holding_cost"]),
            "current_stock": product["current_stock"],
        },
        "scenario": {"demand_change_pct": demand_change, "lead_time_days": product["lead_time_days"]},
        **model,
        "status": status,
        "recommended_order_qty": order_qty,
        "saved_safety_stock": product["safety_stock"],
        "saved_reorder_point": product["reorder_point"],
    }


def recalculate_product(product_id, service_level=DEFAULT_SERVICE_LEVEL):
    """POST /api/products/<id>/recalculate: save the model's SS and ROP."""
    rec = product_recommendation(product_id, service_level)
    execute("UPDATE products SET safety_stock = %s, reorder_point = %s WHERE id = %s",
            (rec["safety_stock"], rec["reorder_point"], product_id))
    return to_json(get_product(product_id))


# =====================================================================
# Step 4: suppliers (create, read one/many, update)
# =====================================================================
SUPPLIER_SELECT = """
    SELECT s.id, s.name, s.lead_time_days, s.std_dev_lead_time, s.contact_email,
           s.created_at, s.updated_at, COUNT(p.id) AS product_count
    FROM suppliers s
    LEFT JOIN products p ON p.supplier_id = s.id
"""


def list_suppliers_api(search=""):
    sql, params = SUPPLIER_SELECT, []
    if search:
        sql += " WHERE s.name LIKE %s"
        params.append(f"%{search}%")
    rows = query_all(sql + " GROUP BY s.id ORDER BY s.name", params)
    return [to_json(r) for r in rows]


def get_supplier(supplier_id):
    row = query_one(SUPPLIER_SELECT + " WHERE s.id = %s GROUP BY s.id", (supplier_id,))
    if row is None:
        raise NotFoundError(f"Supplier {supplier_id} not found.")
    return to_json(row)


def create_supplier(data):
    values = validate(data, SUPPLIER_RULES, SUPPLIER_REQUIRED, read_only={"id"})
    values.setdefault("std_dev_lead_time", Decimal("0"))
    if query_one("SELECT id FROM suppliers WHERE name = %s", (values["name"],)):
        raise ConflictError("A supplier with this name already exists.",
                            {"name": f"{values['name']} is already used."})
    new_id = execute(f"INSERT INTO suppliers ({', '.join(values)}) VALUES ({', '.join(['%s'] * len(values))})",
                     list(values.values()))
    return get_supplier(new_id)


def update_supplier(supplier_id, data, service_level=DEFAULT_SERVICE_LEVEL):
    """PATCH /api/suppliers/<id>. A new lead time changes the safety stock and
    reorder point of every product from this supplier, so they are
    recalculated in the same transaction."""
    get_supplier(supplier_id)                                      # 404 if missing
    values = validate(data, SUPPLIER_RULES, partial=True, read_only={"id"})
    if "name" in values and query_one("SELECT id FROM suppliers WHERE name = %s AND id <> %s",
                                      (values["name"], supplier_id)):
        raise ConflictError("A supplier with this name already exists.",
                            {"name": f"{values['name']} is already used."})
    with transaction() as cur:
        assignments = ", ".join(f"{column} = %s" for column in values)
        cur.execute(f"UPDATE suppliers SET {assignments} WHERE id = %s", list(values.values()) + [supplier_id])
        if LEAD_TIME_FIELDS & set(values):
            cur.execute("SELECT id, lead_time_days, std_dev_lead_time FROM suppliers WHERE id = %s",
                        (supplier_id,))
            supplier = cur.fetchone()
            cur.execute("""SELECT id, avg_daily_demand, std_dev_demand, annual_demand, order_cost,
                                  holding_cost FROM products WHERE supplier_id = %s""", (supplier_id,))
            for product in cur.fetchall():
                model = _model_output(product, supplier, service_level)
                cur.execute("UPDATE products SET safety_stock = %s, reorder_point = %s WHERE id = %s",
                            (model["safety_stock"], model["reorder_point"], product["id"]))
    return get_supplier(supplier_id)


# =====================================================================
# Step 4: sales (create, read one/many). Logging a sale reduces stock.
# =====================================================================
SALE_SELECT = """
    SELECT o.id, o.product_id, p.sku, p.name AS product_name, o.quantity_sold, o.unit_price,
           o.quantity_sold * o.unit_price AS line_total, o.sale_date, o.created_at
    FROM sales_orders o
    JOIN products p ON p.id = o.product_id
"""


def list_sales_page(page=1, size=10, product_id=None, date_from=None, date_to=None):
    where, params = ["1=1"], []
    if product_id:
        where.append("o.product_id = %s")
        params.append(product_id)
    if date_from:
        where.append("o.sale_date >= %s")
        params.append(date_from)
    if date_to:
        where.append("o.sale_date < %s + INTERVAL 1 DAY")
        params.append(date_to)
    clause = " WHERE " + " AND ".join(where)
    total = query_one("SELECT COUNT(*) AS n FROM sales_orders o" + clause, params)["n"]
    rows = query_all(SALE_SELECT + clause + " ORDER BY o.sale_date DESC, o.id DESC LIMIT %s OFFSET %s",
                     params + [size, (page - 1) * size])
    return {"items": [to_json(r) for r in rows], "meta": _page_meta(page, size, total)}


def get_sale(sale_id):
    row = query_one(SALE_SELECT + " WHERE o.id = %s", (sale_id,))
    if row is None:
        raise NotFoundError(f"Sale {sale_id} not found.")
    return to_json(row)


def create_sale(data):
    """POST /api/sales: record the sale AND reduce stock in one transaction.
    A sale larger than the stock on hand is refused and nothing changes."""
    values = validate(data, SALE_RULES, SALE_REQUIRED, read_only={"id"})
    with transaction() as cur:
        # FOR UPDATE locks the product row so two sales at once can't oversell it
        cur.execute("SELECT id, name, current_stock, unit_cost FROM products WHERE id = %s FOR UPDATE",
                    (values["product_id"],))
        product = cur.fetchone()
        if product is None:
            raise ValidationError({"product_id": f"Product {values['product_id']} does not exist."})
        if values["quantity_sold"] > product["current_stock"]:
            raise ConflictError(
                f"Only {product['current_stock']} units of {product['name']} are in stock.",
                {"quantity_sold": f"Must be {product['current_stock']} or less."})
        if "unit_price" not in values:
            # no price given: use this product's latest selling price, or its cost
            cur.execute("""SELECT unit_price FROM sales_orders WHERE product_id = %s
                           ORDER BY sale_date DESC, id DESC LIMIT 1""", (product["id"],))
            last = cur.fetchone()
            values["unit_price"] = last["unit_price"] if last else product["unit_cost"]
        cur.execute("""INSERT INTO sales_orders (product_id, quantity_sold, unit_price, sale_date)
                       VALUES (%s, %s, %s, COALESCE(%s, NOW()))""",
                    (product["id"], values["quantity_sold"], values["unit_price"], values.get("sale_date")))
        sale_id = cur.lastrowid
        cur.execute("UPDATE products SET current_stock = current_stock - %s WHERE id = %s",
                    (values["quantity_sold"], product["id"]))
    return {"sale": get_sale(sale_id), "product": to_json(get_product(product["id"]))}


# =====================================================================
# Step 4: dashboard figures as JSON
# =====================================================================
def dashboard_api():
    """GET /api/dashboard: the KPI cards, counts, 7-day demand and alerts."""
    kpis = dashboard_kpis()
    counts = query_one("""
        SELECT (SELECT COUNT(*) FROM products) AS products,
               (SELECT COUNT(*) FROM suppliers) AS suppliers,
               (SELECT COUNT(*) FROM sales_orders) AS sales,
               (SELECT COALESCE(SUM(current_stock * unit_cost), 0) FROM products) AS inventory_value
    """)
    status_rows = query_all(f"SELECT {STATUS_SQL} AS status, COUNT(*) AS n FROM products p GROUP BY status")
    by_status = {"URGENT": 0, "REORDER": 0, "OK": 0}
    by_status.update({r["status"]: r["n"] for r in status_rows})
    return {
        "service_level_pct": kpis["service_level"],
        "stockout_events": kpis["stockout_events"],
        "inventory_value": float(counts["inventory_value"]),
        "emergency_orders": kpis["emergency_orders"],
        "counts": {"products": counts["products"], "suppliers": counts["suppliers"],
                   "sales": counts["sales"]},
        "status_counts": by_status,
        "demand_by_weekday": dict(zip(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
                                      kpis["forecast_7day"])),
        "alerts": kpis["alerts"],
    }


# =====================================================================
# Step 5: receiving a delivery and importing a day's sales from CSV
# =====================================================================
RECEIVE_RULES = {"quantity": ("int", {"minimum": 1, "maximum": 100_000})}
CSV_COLUMNS = ("sale_date", "sku", "quantity_sold")        # unit_price is optional
MAX_IMPORT_ROWS = 500


def receive_stock(product_id, data):
    """POST /api/products/<id>/receive: a supplier delivery arrived, add it to stock.
    Used by the "Receive order" button next to products that need reordering."""
    require_product(product_id)
    values = validate(data, RECEIVE_RULES, {"quantity"})
    execute("UPDATE products SET current_stock = current_stock + %s WHERE id = %s",
            (values["quantity"], product_id))
    return to_json(get_product(product_id))


def import_sales(csv_text):
    """POST /api/sales/import: log many sales from the till's CSV export.

    Expected header: sale_date,sku,quantity_sold[,unit_price]
    Every row is checked first. If any row is wrong (bad date, unknown SKU,
    more sold than in stock) nothing is saved and every problem is reported
    by row number, so the manager can fix the file and upload it again.
    """
    import csv
    import io

    reader = csv.DictReader(io.StringIO(csv_text.lstrip("﻿")))   # ﻿: Excel's hidden BOM
    header = [h.strip().lower() for h in (reader.fieldnames or [])]
    missing = [c for c in CSV_COLUMNS if c not in header]
    if missing:
        raise ValidationError({"file": f"Missing column(s): {', '.join(missing)}. "
                                       f"The first line must be: sale_date,sku,quantity_sold,unit_price"},
                              "The CSV file has the wrong columns.")
    reader.fieldnames = header

    errors, rows = {}, []
    for line_no, raw in enumerate(reader, start=2):              # line 1 is the header
        if not any((v or "").strip() for v in raw.values()):
            continue                                             # skip blank lines
        if len(rows) >= MAX_IMPORT_ROWS:
            errors["file"] = f"Upload at most {MAX_IMPORT_ROWS} rows at a time."
            break
        sku = (raw.get("sku") or "").strip().upper()
        try:
            values = validate({k: raw.get(k) for k in ("quantity_sold", "unit_price", "sale_date")
                               if (raw.get(k) or "").strip()},
                              SALE_RULES, {"quantity_sold", "sale_date"})
        except ValidationError as exc:
            errors[f"row {line_no}"] = "; ".join(f"{k}: {v}" for k, v in exc.details.items())
            continue
        rows.append((line_no, sku, values))
    if not rows and not errors:
        raise ValidationError({"file": "The file has a header but no sales."}, "The CSV file is empty.")

    with transaction() as cur:
        products = {}
        for sku in sorted({sku for _, sku, _ in rows}):
            cur.execute("SELECT id, sku, name, current_stock, unit_cost FROM products WHERE sku = %s FOR UPDATE",
                        (sku,))
            row = cur.fetchone()
            if row:
                products[sku] = {**row, "remaining": row["current_stock"]}
        for line_no, sku, values in rows:
            product = products.get(sku)
            if product is None:
                errors[f"row {line_no}"] = f"sku: {sku or '(blank)'} is not a product."
                continue
            if values["quantity_sold"] > product["remaining"]:
                errors[f"row {line_no}"] = (f"quantity_sold: only {product['remaining']} units of "
                                            f"{product['name']} left in stock.")
                continue
            product["remaining"] -= values["quantity_sold"]
        if errors:
            # nothing has been written yet; raising here also rolls back the row locks
            raise ValidationError(dict(sorted(errors.items(), key=lambda e: (len(e[0]), e[0]))),
                                  "Nothing was imported. Fix these rows and upload the file again.")
        for _, sku, values in rows:
            product = products[sku]
            cur.execute("""INSERT INTO sales_orders (product_id, quantity_sold, unit_price, sale_date)
                           VALUES (%s, %s, %s, %s)""",
                        (product["id"], values["quantity_sold"],
                         values.get("unit_price", product["unit_cost"]), values["sale_date"]))
            cur.execute("UPDATE products SET current_stock = current_stock - %s WHERE id = %s",
                        (values["quantity_sold"], product["id"]))
    return {"imported": len(rows),
            "products": [{"sku": p["sku"], "name": p["name"], "current_stock": p["remaining"]}
                         for p in products.values()]}
