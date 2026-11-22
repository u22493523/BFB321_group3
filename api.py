"""REST API (Step 4): a Flask Blueprint mounted at /api.

Each route does three things only: read the request, call one function in
models.py, and return JSON. All SQL and business rules live in models.py,
and all errors are turned into JSON by errors.py.

Reading (GET) is open; creating or changing data (POST, PATCH) needs the
manager to be signed in (POST /api/auth/login), otherwise 401.

Response shape
    success, one record   {"data": {...}}
    success, a list       {"data": [...], "meta": {"page": 1, "size": 10, "total": 12, "pages": 2}}
    error                 {"error": {"code": 400, "message": "...", "details": {"field": "..."}}}
"""
from datetime import date

from flask import Blueprint, current_app, jsonify, request, url_for

import models
from auth import api_login_required
from errors import ValidationError

api = Blueprint("api", __name__, url_prefix="/api")

STATUSES = ("URGENT", "REORDER", "OK")


# ---------------------------------------------------------------------
# Reading query strings and request bodies
# ---------------------------------------------------------------------
def _body():
    """The JSON body (or a normal HTML form post) as a dict."""
    data = request.get_json(silent=True)
    if data is None and request.form:
        data = request.form.to_dict()
    return data if data is not None else []          # [] fails validation with a clear message


def _int_arg(name, default=None, minimum=1, maximum=None, errors=None):
    raw = request.args.get(name, "").strip()
    if raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        errors[name] = "Must be a whole number."
        return default
    if value < minimum or (maximum is not None and value > maximum):
        errors[name] = (f"Must be between {minimum} and {maximum}." if maximum is not None
                        else f"Must be {minimum} or more.")
    return value


def _page_args(errors):
    """?page=1&size=10 (size at most MAX_PAGE_SIZE)."""
    page = _int_arg("page", 1, errors=errors)
    size = _int_arg("size", current_app.config["DEFAULT_PAGE_SIZE"], maximum=current_app.config["MAX_PAGE_SIZE"],
                    errors=errors)
    return page, size


def _date_arg(name, errors):
    raw = request.args.get(name, "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        errors[name] = "Use the format YYYY-MM-DD."
        return None


def _service_level(errors):
    """?service_level=0.95 (proposal: at least 95%); defaults to SERVICE_LEVEL in config."""
    raw = request.args.get("service_level", "").strip()
    if not raw:
        return current_app.config["SERVICE_LEVEL"]
    try:
        level = float(raw)
    except ValueError:
        errors["service_level"] = "Must be a number such as 0.95."
        return None
    if not 0.95 <= level <= 0.999:
        errors["service_level"] = "Must be between 0.95 (proposal minimum) and 0.999."
    return level


def _raise_if(errors):
    if errors:
        raise ValidationError(errors, "Some query parameters are invalid.")


def _created(data, endpoint, **ids):
    """201 Created with a Location header pointing at the new record."""
    response = jsonify(data=data)
    response.status_code = 201
    response.headers["Location"] = url_for(endpoint, **ids)
    return response


# ---------------------------------------------------------------------
# Index: what this API offers
# ---------------------------------------------------------------------
@api.get("")
def index():
    """List every endpoint (method and path) so the API is easy to explore."""
    routes = sorted(
        {(rule.rule, ", ".join(sorted(rule.methods - {"HEAD", "OPTIONS"})))
         for rule in current_app.url_map.iter_rules() if rule.rule.startswith("/api")})
    return jsonify(data=[{"path": path, "methods": methods} for path, methods in routes])


# ---------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------
@api.get("/products")
def list_products():
    """GET /api/products?page=&size=&q=&category=&status=&supplier_id=&sort="""
    errors = {}
    page, size = _page_args(errors)
    supplier_id = _int_arg("supplier_id", errors=errors)
    status = request.args.get("status", "").strip().upper() or None
    if status and status not in STATUSES:
        errors["status"] = "Use URGENT, REORDER or OK."
    sort = request.args.get("sort", "sku").strip()
    if sort not in models.PRODUCT_SORTS:
        errors["sort"] = f"Use one of: {', '.join(models.PRODUCT_SORTS)}."
    _raise_if(errors)
    result = models.list_products_page(page, size, search=request.args.get("q", "").strip(),
                                       category=request.args.get("category", "").strip() or None,
                                       status=status, supplier_id=supplier_id, sort=sort)
    return jsonify(data=result["items"], meta=result["meta"])


@api.post("/products")
@api_login_required
def create_product():
    """POST /api/products: add a product; the model sets its safety stock and ROP."""
    product = models.create_product(_body(), current_app.config["SERVICE_LEVEL"])
    return _created(product, "api.get_product", product_id=product["id"])


@api.get("/products/<int:product_id>")
def get_product(product_id):
    """GET /api/products/<id>: one product with its status and order quantity."""
    return jsonify(data=models.to_json(models.require_product(product_id)))


@api.patch("/products/<int:product_id>")
@api_login_required
def update_product(product_id):
    """PATCH /api/products/<id>: change stock, costs, demand data or the reorder point."""
    return jsonify(data=models.update_product(product_id, _body(), current_app.config["SERVICE_LEVEL"]))


@api.get("/products/<int:product_id>/recommendation")
def product_recommendation(product_id):
    """GET /api/products/<id>/recommendation?service_level=0.95: the Step 2 model's advice.

    Scenario evaluation (Step 5): add ?demand_change=38 (a home rugby match, +38%
    demand) and/or ?lead_time_days=7 (a late supplier) to see what the model would
    advise. Nothing is saved; the saved values are returned alongside for comparison."""
    errors = {}
    level = _service_level(errors)
    demand_change = _int_arg("demand_change", 0, minimum=-90, maximum=300, errors=errors)
    lead_time = _int_arg("lead_time_days", None, minimum=1, maximum=365, errors=errors)
    _raise_if(errors)
    return jsonify(data=models.product_recommendation(product_id, level, demand_change, lead_time))


@api.post("/products/<int:product_id>/recalculate")
@api_login_required
def recalculate_product(product_id):
    """POST /api/products/<id>/recalculate?service_level=: save the model's SS and ROP."""
    errors = {}
    level = _service_level(errors)
    _raise_if(errors)
    return jsonify(data=models.recalculate_product(product_id, level))


@api.post("/products/<int:product_id>/receive")
@api_login_required
def receive_stock(product_id):
    """POST /api/products/<id>/receive {"quantity": 532}: a delivery arrived, add it to stock."""
    return jsonify(data=models.receive_stock(product_id, _body()))


# ---------------------------------------------------------------------
# Suppliers
# ---------------------------------------------------------------------
@api.get("/suppliers")
def list_suppliers():
    """GET /api/suppliers?q=: every supplier with its product count."""
    suppliers = models.list_suppliers_api(search=request.args.get("q", "").strip())
    return jsonify(data=suppliers, meta={"total": len(suppliers)})


@api.post("/suppliers")
@api_login_required
def create_supplier():
    """POST /api/suppliers: add a supplier."""
    supplier = models.create_supplier(_body())
    return _created(supplier, "api.get_supplier", supplier_id=supplier["id"])


@api.get("/suppliers/<int:supplier_id>")
def get_supplier(supplier_id):
    """GET /api/suppliers/<id>"""
    return jsonify(data=models.get_supplier(supplier_id))


@api.patch("/suppliers/<int:supplier_id>")
@api_login_required
def update_supplier(supplier_id):
    """PATCH /api/suppliers/<id>: e.g. a new lead time; its products are recalculated."""
    return jsonify(data=models.update_supplier(supplier_id, _body(), current_app.config["SERVICE_LEVEL"]))


# ---------------------------------------------------------------------
# Sales
# ---------------------------------------------------------------------
@api.get("/sales")
def list_sales():
    """GET /api/sales?page=&size=&product_id=&from=YYYY-MM-DD&to=YYYY-MM-DD"""
    errors = {}
    page, size = _page_args(errors)
    product_id = _int_arg("product_id", errors=errors)
    date_from, date_to = _date_arg("from", errors), _date_arg("to", errors)
    if date_from and date_to and date_from > date_to:
        errors["to"] = "Must be on or after 'from'."
    _raise_if(errors)
    result = models.list_sales_page(page, size, product_id, date_from, date_to)
    return jsonify(data=result["items"], meta=result["meta"])


@api.post("/sales")
@api_login_required
def create_sale():
    """POST /api/sales: log a sale; stock goes down in the same transaction."""
    result = models.create_sale(_body())
    return _created(result, "api.get_sale", sale_id=result["sale"]["id"])


@api.post("/sales/import")
@api_login_required
def import_sales():
    """POST /api/sales/import: upload the till's CSV (form field "file").
    All rows are saved, or none are (every bad row is listed with a 400)."""
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        raise ValidationError({"file": "Choose a .csv file to upload."})
    if not upload.filename.lower().endswith(".csv"):
        raise ValidationError({"file": "Only .csv files can be imported."})
    try:
        text = upload.read().decode("utf-8")
    except UnicodeDecodeError:
        raise ValidationError({"file": "Save the file as CSV UTF-8 and try again."})
    result = models.import_sales(text)
    response = jsonify(data=result)
    response.status_code = 201
    return response


@api.get("/sales/<int:sale_id>")
def get_sale(sale_id):
    """GET /api/sales/<id>"""
    return jsonify(data=models.get_sale(sale_id))


# ---------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------
@api.get("/dashboard")
def dashboard():
    """GET /api/dashboard: KPI cards, stock status counts, weekly demand and alerts."""
    return jsonify(data=models.dashboard_api())
