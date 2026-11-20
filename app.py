import os

from flask import Flask, render_template, request

import db
import models

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-only-change-me")
db.init_app(app)


# ---------------------------------------------------------------------
# Main Dashboard & Integrated Views (Step 3 Live MySQL Connection)
# ---------------------------------------------------------------------
@app.route("/")
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
    )


if __name__ == "__main__":
    app.run(debug=True)
