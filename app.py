from flask import Flask, render_template, request

import db
import models
from api import api
from auth import auth, current_user, login_required
from config import get_config
from errors import configure_logging, register_error_handlers

app = Flask(__name__)
app.config.from_object(get_config())   # development / testing / production from APP_ENV
app.json.sort_keys = False             # keep JSON fields in database order
db.init_app(app)
configure_logging(app)
register_error_handlers(app)
app.register_blueprint(api)            # all /api/... JSON endpoints live in api.py
app.register_blueprint(auth)           # /login, /logout and /api/auth/... (auth.py)


# ---------------------------------------------------------------------
# Main Dashboard & Integrated Views (Step 3 Live MySQL Connection)
# ---------------------------------------------------------------------
@app.route("/")
@login_required
def index():
    search = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip() or None

    kpis = models.dashboard_kpis()
    reorder_products = models.get_reorder_products()
    products = models.list_products(search=search, category=category)
    suppliers = models.list_suppliers()

    return render_template(
        "index.html",
        kpis=kpis,
        reorder_products=reorder_products,
        products=products,
        suppliers=suppliers,
        user=current_user(),
    )


if __name__ == "__main__":
    app.run(debug=app.config["DEBUG"])
