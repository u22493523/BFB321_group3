"""Manager login (Step 5).

The TapHouse manager signs in with the account in the users table (seeded
in seed.sql; credentials in the README). Passwords are checked against the
Werkzeug hash, never stored or compared as plain text.

    Pages      the dashboard needs a signed-in manager; otherwise -> /login
    API reads  GET endpoints stay open so markers can test them with curl
    API writes POST and PATCH endpoints need a signed-in manager (401 otherwise)
"""
from functools import wraps

from flask import (Blueprint, redirect, render_template, request, session,
                   url_for)
from werkzeug.security import check_password_hash

from db import query_one
from errors import APIError

auth = Blueprint("auth", __name__)


def current_user():
    """The signed-in manager as a dict, or None."""
    user_id = session.get("user_id")
    if user_id is None:
        return None
    return query_one("SELECT id, first_name, last_name, email FROM users WHERE id = %s", (user_id,))


def check_login(email, password):
    """Return the user if the email and password match, else None."""
    user = query_one("SELECT id, first_name, last_name, email, password_hash FROM users WHERE email = %s",
                     ((email or "").strip().lower(),))
    if user and check_password_hash(user["password_hash"], password or ""):
        return user
    return None


def login_required(view):
    """Pages: send visitors who are not signed in to the login page."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def api_login_required(view):
    """API writes: answer 401 JSON instead of redirecting."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            raise APIError("Sign in as the TapHouse manager to change data.", status_code=401)
        return view(*args, **kwargs)
    return wrapper


@auth.route("/login", methods=["GET", "POST"])
def login():
    error = None
    email = ""
    if request.method == "POST":
        email = request.form.get("email", "")
        user = check_login(email, request.form.get("password", ""))
        if user:
            session.clear()
            session["user_id"] = user["id"]
            target = request.args.get("next", "")
            # only follow local paths such as /dashboard, never another site
            if not target.startswith("/") or target.startswith("//"):
                target = url_for("index")
            return redirect(target)
        error = "Incorrect email or password."
    return render_template("login.html", error=error, email=email), (401 if error else 200)


@auth.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------
# The same login for API clients (curl, Postman, tests): JSON in, session cookie out
# ---------------------------------------------------------------------
@auth.post("/api/auth/login")
def api_login():
    data = request.get_json(silent=True) or {}
    user = check_login(data.get("email"), data.get("password"))
    if user is None:
        raise APIError("Incorrect email or password.", status_code=401)
    session.clear()
    session["user_id"] = user["id"]
    return {"data": {"id": user["id"], "email": user["email"],
                     "name": f"{user['first_name']} {user['last_name']}"}}


@auth.post("/api/auth/logout")
def api_logout():
    session.clear()
    return {"data": {"signed_out": True}}
