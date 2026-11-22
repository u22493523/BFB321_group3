"""Connection to MySQL (local Workbench or Aiven). Every other file reaches the
database through the helpers in this file."""
import os
from contextlib import contextmanager

import pymysql
import pymysql.cursors
from dotenv import load_dotenv
from flask import current_app, g, has_app_context

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))  # DB_HOST, DB_PASSWORD, ... from .env


def _setting(name, default):
    """Inside the Flask app use config.py (which reads .env); otherwise (init_db.py) read .env."""
    if has_app_context() and name in current_app.config:
        return current_app.config[name]
    return os.getenv(name, default)


def connect(database=None):
    """Open a new MySQL connection using the settings in .env."""
    options = dict(
        host=_setting("DB_HOST", "127.0.0.1"),
        port=int(_setting("DB_PORT", "3306")),
        user=_setting("DB_USER", "root"),
        password=_setting("DB_PASSWORD", ""),
        database=database or _setting("DB_NAME", "taphouse_didss"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,  # rows come back as dictionaries
        autocommit=False,                        # nothing is saved until commit()
        connect_timeout=15,
    )

    # Optional: verify the server with a CA certificate (only when DB_SSL_CA is set).
    ca = os.getenv("DB_SSL_CA", "").strip()
    if ca:
        ca_path = ca if os.path.isabs(ca) else os.path.join(BASE_DIR, ca)
        if not os.path.exists(ca_path):
            raise FileNotFoundError(
                f"DB_SSL_CA points to '{ca}' but that file was not found. "
                "Download the CA certificate from Aiven and save it as ca.pem next to app.py."
            )
        options["ssl_ca"] = ca_path
        options["ssl_verify_cert"] = True
        options["ssl_verify_identity"] = (
            os.getenv("DB_SSL_VERIFY_HOST", "false").lower() == "true"
        )

    return pymysql.connect(**options)


# ---------------------------------------------------------------------
# One connection per request
# ---------------------------------------------------------------------
def get_db():
    """Open the connection the first time it is needed in this request."""
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(exception=None):
    """End of request: undo anything not committed if the request failed, then close."""
    db = g.pop("db", None)
    if db is not None:
        if exception is not None:
            db.rollback()
        db.close()


def init_app(app):
    app.teardown_appcontext(close_db)


# ---------------------------------------------------------------------
# Helpers - parameterised queries to prevent SQL injection
# ---------------------------------------------------------------------
def query_all(sql, params=None):
    """SELECT many rows -> list of dictionaries."""
    with get_db().cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def query_one(sql, params=None):
    """SELECT the first row -> one dictionary (or None)."""
    with get_db().cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchone()


def execute(sql, params=None):
    """INSERT / UPDATE / DELETE: run it, commit, return the new row id.
    If MySQL refuses the change, roll back so nothing is half-saved."""
    db = get_db()
    try:
        with db.cursor() as cur:
            cur.execute(sql, params or ())
            new_id = cur.lastrowid
        db.commit()
    except Exception:
        db.rollback()
        raise
    return new_id


@contextmanager
def transaction():
    """Several writes that must succeed or fail together (e.g. log a sale AND
    reduce stock). Commits at the end; rolls everything back on any error.

        with transaction() as cur:
            cur.execute(...)
            cur.execute(...)
    """
    db = get_db()
    try:
        with db.cursor() as cur:
            yield cur
        db.commit()
    except Exception:
        db.rollback()
        raise
