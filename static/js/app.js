/* DIDSS TapHouse dashboard (Step 5: integration)
 *
 * Flask draws the first version of the page. After that, everything the
 * manager does goes through the REST API in api.py with fetch(), and the
 * KPI cards, tables, alerts and chart are redrawn from the API's answer,
 * so the page never has to reload.
 *
 *   Log Sales            POST  /api/sales
 *   Update Supplier      PATCH /api/suppliers/<id>
 *   Upload CSV           POST  /api/sales/import
 *   Receive order        POST  /api/products/<id>/receive
 *   Add / Edit product   POST  /api/products, PATCH /api/products/<id>
 *   Run Simulation       GET   /api/products/<id>/recommendation
 *   Refresh everything   GET   /api/dashboard, /api/products, /api/suppliers
 */
(function () {
    "use strict";

    const BADGES = { URGENT: "bg-danger", REORDER: "bg-warning text-dark", OK: "bg-success" };
    const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    const state = { products: [], suppliers: [] };

    // ------------------------------------------------------------------
    // Small helpers
    // ------------------------------------------------------------------
    const $ = (selector, root) => (root || document).querySelector(selector);
    const $$ = (selector, root) => Array.from((root || document).querySelectorAll(selector));

    /** Escape text before putting it in HTML, so a product called <b> stays text. */
    function esc(value) {
        return String(value == null ? "" : value).replace(/[&<>"']/g, ch => (
            { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
    }

    // R 17,856.80 (same format as the first page Flask draws)
    const rand = value => "R " + Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

    /** Call the API. Resolves with the JSON body, rejects with {message, details}. */
    async function api(method, url, body) {
        const options = { method, headers: { Accept: "application/json" }, credentials: "same-origin" };
        if (body instanceof FormData) {
            options.body = body;                                  // the browser sets the multipart header
        } else if (body !== undefined) {
            options.headers["Content-Type"] = "application/json";
            options.body = JSON.stringify(body);
        }
        let response;
        try {
            response = await fetch(url, options);
        } catch (networkError) {
            throw { message: "Could not reach the server. Check that Flask is running.", details: {} };
        }
        if (response.status === 401 && method !== "GET") {
            window.location.href = "/login?next=/";              // signed out (e.g. session expired)
        }
        const json = await response.json().catch(() => ({}));
        if (!response.ok) {
            const error = json.error || {};
            throw { message: error.message || `Request failed (${response.status}).`, details: error.details || {} };
        }
        return json;
    }

    /** A message at the top of the page that disappears after a few seconds. */
    function flash(message, type) {
        const box = document.createElement("div");
        box.className = `alert alert-${type || "success"} alert-dismissible fade show shadow-sm`;
        box.setAttribute("role", type === "danger" ? "alert" : "status");
        box.innerHTML = `${message}<button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>`;
        $("#flash").prepend(box);
        setTimeout(() => box.remove(), 6000);
    }

    /** Show the result of a form under it; highlight the fields the API rejected. */
    function showResult(form, type, message, details) {
        $$(".is-invalid", form).forEach(el => el.classList.remove("is-invalid"));
        const box = $(".form-result", form);
        let html = message;
        const entries = Object.entries(details || {});
        if (entries.length) {
            html += "<ul class='mb-0 mt-1'>" + entries.map(([field, problem]) => {
                const input = form.elements[field];
                if (input && input.classList) input.classList.add("is-invalid");
                return `<li><strong>${esc(field)}</strong>: ${esc(problem)}</li>`;
            }).join("") + "</ul>";
        }
        box.innerHTML = message ? `<div class="alert alert-${type} mb-0">${html}</div>` : "";
    }

    /** Disable the submit button and show a spinner while a request runs. */
    async function busy(form, work) {
        const button = $("button[type=submit]", form);
        const label = button.innerHTML;
        button.disabled = true;
        button.innerHTML = `<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>Saving...`;
        try {
            return await work();
        } finally {
            button.disabled = false;
            button.innerHTML = label;
        }
    }

    /** The browser's own required/min/max checks, before we call the API. */
    function browserCheck(form) {
        if (form.checkValidity()) return true;
        const details = {};
        $$("input, select", form).forEach(el => {
            if (!el.validity.valid) details[el.name] = el.validationMessage;
        });
        showResult(form, "danger", "Please fix these fields:", details);
        return false;
    }

    // ------------------------------------------------------------------
    // Switching views (navbar)
    // ------------------------------------------------------------------
    function showView(view) {
        ["dashboard", "inventory", "management", "scenario"].forEach(name => {
            $(`#${name}-view`).style.display = name === view ? "block" : "none";
        });
        $$("[data-view]").forEach(link => {
            const active = link.dataset.view === view;
            link.classList.toggle("active", active);
            if (active) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
        });
    }

    $$("[data-view]").forEach(link => link.addEventListener("click", event => {
        event.preventDefault();
        history.replaceState(null, "", "#" + link.dataset.view);
        showView(link.dataset.view);
    }));

    // ------------------------------------------------------------------
    // Drawing the page from API data
    // ------------------------------------------------------------------
    const chart = new Chart($("#demandChart"), {
        type: "line",
        data: {
            labels: DAYS,
            datasets: [{
                label: "Units sold",
                data: window.DIDSS.demand,
                borderColor: "#007bff",
                backgroundColor: "rgba(0, 123, 255, 0.2)",
                borderWidth: 2,
                fill: true,
                tension: 0.4
            }]
        },
        options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: { y: { beginAtZero: true, title: { display: true, text: "Units sold" } } }
        }
    });

    function badge(status) {
        return `<span class="badge ${BADGES[status] || "bg-secondary"}">${esc(status)}</span>`;
    }

    function thumb(p) {
        return p.image_filename
            ? `<img src="${esc(window.DIDSS.imageBase + p.image_filename)}" alt="" class="product-thumb">` : "";
    }

    function drawKpis(d) {
        $("#kpi-service").textContent = d.service_level_pct + "%";
        $("#kpi-stockouts").textContent = d.stockout_events;
        $("#kpi-value").textContent = rand(d.inventory_value);
        $("#kpi-emergency").textContent = d.emergency_orders;
        chart.data.datasets[0].data = DAYS.map(day => d.demand_by_weekday[day] || 0);
        chart.update();
        $("#alerts-body").innerHTML = d.alerts.length
            ? d.alerts.map(a => `<div class="alert alert-${esc(a.type)}" role="alert"><strong>${esc(a.prefix)}</strong> ${esc(a.message)}</div>`).join("")
            : `<p class="text-muted mb-0">No alerts: every product is above its reorder point.</p>`;
    }

    function drawReorderTable(products) {
        const rank = { URGENT: 0, REORDER: 1 };
        const rows = products.filter(p => p.status !== "OK")
            .sort((a, b) => rank[a.status] - rank[b.status] || a.current_stock / a.reorder_point - b.current_stock / b.reorder_point);
        $("#reorder-count").textContent = rows.length;
        $("#reorder-body").innerHTML = rows.length ? rows.map(p => `
            <tr>
                <td>${esc(p.name)} ${thumb(p)}</td>
                <td>${p.current_stock}</td>
                <td>${p.reorder_point}</td>
                <td>${p.recommended_order_qty}</td>
                <td>${badge(p.status)}</td>
                <td><button type="button" class="btn btn-sm btn-success text-nowrap" data-receive="${p.id}" data-qty="${p.recommended_order_qty}">Receive order</button></td>
            </tr>`).join("")
            : `<tr><td colspan="6" class="text-center text-muted">No products nearing reorder point.</td></tr>`;
    }

    function drawProductTable(products) {
        $("#product-count").textContent = products.length;
        $("#product-body").innerHTML = products.length ? products.map(p => `
            <tr>
                <td>${esc(p.sku)}</td>
                <td>${esc(p.name)} ${thumb(p)}</td>
                <td>${esc(p.category)}</td>
                <td>${p.current_stock}</td>
                <td>${Number(p.unit_cost).toFixed(2)}</td>
                <td>${esc(p.supplier_name)}</td>
                <td>${badge(p.status)}</td>
                <td><button type="button" class="btn btn-sm btn-outline-secondary" data-edit="${p.id}">Edit</button></td>
            </tr>`).join("")
            : `<tr><td colspan="8" class="text-center text-muted">No products yet. Click "+ Add Product".</td></tr>`;
    }

    /** Refill a <select>, keeping what the manager had chosen. */
    function fillSelect(select, items, label) {
        const chosen = select.value;
        const first = select.options[0].outerHTML;              // "Select a ..." placeholder
        select.innerHTML = first + items.map(item => `<option value="${item.id}">${esc(label(item))}</option>`).join("");
        select.value = chosen;
    }

    function drawSelects() {
        fillSelect($("#sale-product"), state.products, p => `${p.name} (${p.sku}) - ${p.current_stock} in stock`);
        fillSelect($("#scenarioProduct"), state.products, p => `${p.name} (${p.sku})`);
        fillSelect($("#supplier-id"), state.suppliers, s => `${s.name} (Current: ${s.lead_time_days} days)`);
        fillSelect($("#pf-supplier"), state.suppliers, s => s.name);
    }

    /** Fetch fresh figures from the API and redraw every part of the page. */
    async function refresh() {
        try {
            const [dashboard, products, suppliers] = await Promise.all([
                api("GET", "/api/dashboard"),
                api("GET", "/api/products?size=100&sort=sku"),
                api("GET", "/api/suppliers")
            ]);
            state.products = products.data;
            state.suppliers = suppliers.data;
            drawKpis(dashboard.data);
            drawReorderTable(state.products);
            drawProductTable(state.products);
            drawSelects();
        } catch (error) {
            flash(`Could not refresh the dashboard: ${esc(error.message)}`, "danger");
        }
    }

    // ------------------------------------------------------------------
    // Flow 1: log a sale -> stock down -> KPIs, reorder table and alerts update
    // ------------------------------------------------------------------
    const saleForm = $("#sale-form");
    saleForm.sale_date.value = new Date().toLocaleDateString("en-CA");   // today as YYYY-MM-DD
    saleForm.sale_date.max = saleForm.sale_date.value;
    saleForm.addEventListener("submit", event => {
        event.preventDefault();
        if (!browserCheck(saleForm)) return;
        busy(saleForm, async () => {
            try {
                const { data } = await api("POST", "/api/sales", {
                    product_id: Number(saleForm.product_id.value),
                    quantity_sold: Number(saleForm.quantity_sold.value),
                    sale_date: saleForm.sale_date.value
                });
                const p = data.product;
                showResult(saleForm, "success",
                    `Logged ${data.sale.quantity_sold} x ${esc(p.name)}. Stock is now <strong>${p.current_stock}</strong> ${badge(p.status)}`);
                saleForm.quantity_sold.value = "";
                if (p.status !== "OK") flash(`${esc(p.name)} is now <strong>${p.status}</strong>: order ${p.recommended_order_qty} units.`, "warning");
                await refresh();
            } catch (error) {
                showResult(saleForm, "danger", esc(error.message), error.details);
            }
        });
    });

    // ------------------------------------------------------------------
    // Flow 2: supplier lead time changes -> the model recalculates reorder points
    // ------------------------------------------------------------------
    const supplierForm = $("#supplier-form");
    supplierForm.addEventListener("submit", event => {
        event.preventDefault();
        if (!browserCheck(supplierForm)) return;
        const id = supplierForm.supplier_id.value;
        const before = state.products.filter(p => String(p.supplier_id) === id)
            .map(p => [p.id, p.reorder_point]);
        busy(supplierForm, async () => {
            try {
                const { data } = await api("PATCH", `/api/suppliers/${id}`, {
                    lead_time_days: Number(supplierForm.lead_time_days.value)
                });
                await refresh();
                const changes = before.map(([pid, oldRop]) => {
                    const p = state.products.find(x => x.id === pid);
                    return `<li>${esc(p.name)}: reorder point ${oldRop} &rarr; <strong>${p.reorder_point}</strong></li>`;
                }).join("");
                showResult(supplierForm, "success",
                    `${esc(data.name)} now delivers in ${data.lead_time_days} days. The model recalculated:<ul class="mb-0">${changes}</ul>`);
                supplierForm.lead_time_days.value = "";
            } catch (error) {
                showResult(supplierForm, "danger", esc(error.message), error.details);
            }
        });
    });

    // ------------------------------------------------------------------
    // Flow 3: upload the till's CSV -> all sales saved (or none) -> dashboard updates
    // ------------------------------------------------------------------
    const csvForm = $("#csv-form");
    csvForm.addEventListener("submit", event => {
        event.preventDefault();
        if (!browserCheck(csvForm)) return;
        const body = new FormData();
        body.append("file", csvForm.file.files[0]);
        busy(csvForm, async () => {
            try {
                const { data } = await api("POST", "/api/sales/import", body);
                showResult(csvForm, "success", `Imported <strong>${data.imported}</strong> sales for ${data.products.length} product(s).`);
                csvForm.reset();
                await refresh();
            } catch (error) {
                showResult(csvForm, "danger", esc(error.message), error.details);
            }
        });
    });

    // ------------------------------------------------------------------
    // Receive order: the supplier delivered the recommended quantity
    // ------------------------------------------------------------------
    document.addEventListener("click", async event => {
        const button = event.target.closest("[data-receive]");
        if (!button) return;
        const product = state.products.find(p => p.id === Number(button.dataset.receive));
        const qty = Number(button.dataset.qty) || 0;
        const name = product ? product.name : "this product";
        if (!qty || !window.confirm(`Receive ${qty} units of ${name}? This adds them to stock.`)) return;
        button.disabled = true;
        try {
            const { data } = await api("POST", `/api/products/${button.dataset.receive}/receive`, { quantity: qty });
            flash(`Received ${qty} x ${esc(data.name)}. Stock is now ${data.current_stock} ${badge(data.status)}`);
            await refresh();
        } catch (error) {
            flash(esc(error.message), "danger");
            button.disabled = false;
        }
    });

    // ------------------------------------------------------------------
    // Add / edit a product (inventory view)
    // ------------------------------------------------------------------
    const productForm = $("#product-form");
    const productModal = new bootstrap.Modal("#productModal");
    const FIELDS = ["sku", "name", "category", "supplier_id", "current_stock", "unit_cost", "annual_demand",
                    "avg_daily_demand", "std_dev_demand", "order_cost", "holding_cost"];
    const NUMBERS = new Set(FIELDS.slice(3));
    let editing = null;                                            // the product being edited, or null

    function openProductForm(product) {
        editing = product || null;
        productForm.reset();
        showResult(productForm, "", "");
        $("#productModalTitle").textContent = editing ? `Edit ${editing.name}` : "Add Product";
        FIELDS.forEach(f => { productForm.elements[f].value = editing ? editing[f] : ""; });
        productModal.show();
    }

    $("#add-product").addEventListener("click", () => openProductForm(null));
    document.addEventListener("click", event => {
        const button = event.target.closest("[data-edit]");
        if (button) openProductForm(state.products.find(p => p.id === Number(button.dataset.edit)));
    });

    productForm.addEventListener("submit", event => {
        event.preventDefault();
        if (!browserCheck(productForm)) return;
        const body = {};
        FIELDS.forEach(f => {
            const raw = productForm.elements[f].value.trim();
            if (raw === "") return;
            const value = NUMBERS.has(f) ? Number(raw) : raw;
            if (!editing || String(editing[f]) !== String(value)) body[f] = value;   // edit: send only changes
        });
        if (editing && !Object.keys(body).length) {
            showResult(productForm, "info", "Nothing has changed.");
            return;
        }
        busy(productForm, async () => {
            try {
                const { data } = editing
                    ? await api("PATCH", `/api/products/${editing.id}`, body)
                    : await api("POST", "/api/products", body);
                productModal.hide();
                flash(`${editing ? "Saved" : "Added"} ${esc(data.name)}. Safety stock ${data.safety_stock}, reorder point ${data.reorder_point} ${badge(data.status)}`);
                await refresh();
            } catch (error) {
                showResult(productForm, "danger", esc(error.message), error.details);
            }
        });
    });

    // ------------------------------------------------------------------
    // Scenario evaluation: the model's advice for a what-if (nothing saved)
    // ------------------------------------------------------------------
    const scenarioForm = $("#scenario-form");
    scenarioForm.addEventListener("submit", event => {
        event.preventDefault();
        if (!browserCheck(scenarioForm)) return;
        const params = new URLSearchParams({
            service_level: (Number(scenarioForm.service_level.value) / 100).toFixed(3),
            demand_change: scenarioForm.demand_change.value || "0"
        });
        if (scenarioForm.lead_time_days.value) params.set("lead_time_days", scenarioForm.lead_time_days.value);
        const out = $("#scenario-result");
        out.innerHTML = `<div class="text-muted"><span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>Running the model...</div>`;
        busy(scenarioForm, async () => {
            try {
                const { data: r } = await api("GET", `/api/products/${scenarioForm.product_id.value}/recommendation?${params}`);
                showResult(scenarioForm, "", "");
                const row = (label, today, scenario) => `<tr><th scope="row">${label}</th><td>${today}</td><td><strong>${scenario}</strong></td></tr>`;
                out.innerHTML = `
                    <h3 class="h6">${esc(r.name)} (${esc(r.sku)}) at ${(r.service_level * 100).toFixed(1)}% service level, z = ${r.z_score}</h3>
                    <p class="small text-muted">Scenario: demand ${r.scenario.demand_change_pct >= 0 ? "+" : ""}${r.scenario.demand_change_pct}%, lead time ${r.scenario.lead_time_days} days, ${r.inputs.avg_daily_demand} units a day.</p>
                    <div class="overflow-auto">
                    <table class="table table-sm align-middle">
                        <thead><tr><th scope="col"></th><th scope="col">Saved policy</th><th scope="col">Scenario</th></tr></thead>
                        <tbody>
                            ${row("Safety stock", r.saved_safety_stock, r.safety_stock)}
                            ${row("Reorder point", r.saved_reorder_point, r.reorder_point)}
                            ${row("Order quantity (EOQ)", "-", r.eoq == null ? "n/a" : r.eoq)}
                            ${row("Status with current stock (" + r.inputs.current_stock + ")", "", badge(r.status))}
                        </tbody>
                    </table>
                    </div>
                    <p class="mb-0">${r.status === "OK"
                        ? "Current stock covers this scenario. No order needed yet."
                        : `Order <strong>${r.recommended_order_qty}</strong> units now to stay at the target service level.`}</p>`;
            } catch (error) {
                out.innerHTML = `<p class="text-muted mb-0">No result.</p>`;
                showResult(scenarioForm, "danger", esc(error.message), error.details);
            }
        });
    });

    // ------------------------------------------------------------------
    // Start: open the view in the address bar (#inventory etc.), then load live data
    // ------------------------------------------------------------------
    const start = location.hash.slice(1);
    showView(["dashboard", "inventory", "management", "scenario"].includes(start) ? start : "dashboard");
    refresh();
})();
