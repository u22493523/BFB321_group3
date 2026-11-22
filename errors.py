"""Error handling and logging in one place.

Every API error comes back in the same JSON shape, with no stack traces:

    {"error": {"code": 400, "message": "Some fields are invalid.",
               "details": {"sku": "This field is required."}}}

Pages get a short friendly message instead. Every request and every
failure is written to the log (console and logs/app.log).
"""
import logging
import os
import time
from logging.handlers import RotatingFileHandler

import pymysql
from flask import g, jsonify, request
from markupsafe import escape
from werkzeug.exceptions import HTTPException

log = logging.getLogger("taphouse")


# ---------------------------------------------------------------------
# Errors raised by models.py and api.py
# ---------------------------------------------------------------------
class APIError(Exception):
    """An error the user can fix. Raise it anywhere; the handler below turns
    it into a JSON response with the right status code."""
    status_code = 400

    def __init__(self, message, details=None, status_code=None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if status_code:
            self.status_code = status_code


class ValidationError(APIError):
    """400: missing, wrong type or out-of-range input."""

    def __init__(self, details, message="Some fields are invalid."):
        super().__init__(message, details)


class NotFoundError(APIError):
    status_code = 404


class ConflictError(APIError):
    """409: the request clashes with existing data (e.g. a duplicate SKU)."""
    status_code = 409


# MySQL error numbers we can explain to the user instead of crashing
MYSQL_DUPLICATE = 1062            # UNIQUE constraint (duplicate SKU, email, name)
MYSQL_BAD_FOREIGN_KEY = 1452      # supplier_id / product_id that does not exist
MYSQL_ROW_IN_USE = 1451           # deleting a row that others still refer to
MYSQL_CHECK_FAILED = (3819, 4025)  # CHECK constraint (MySQL 8, MariaDB)


def _wants_json():
    return request.path.startswith("/api/")


def _error_response(status, message, details=None):
    if _wants_json():
        body = {"error": {"code": status, "message": message}}
        if details:
            body["error"]["details"] = details
        return jsonify(body), status
    return (f"<h1>{status}</h1><p>{escape(message)}</p><p><a href='/'>Back to the dashboard</a></p>",
            status)


def register_error_handlers(app):
    @app.errorhandler(APIError)
    def handle_api_error(err):
        log.info("%s %s -> %s %s %s", request.method, request.path, err.status_code,
                 err.message, err.details or "")
        return _error_response(err.status_code, err.message, err.details)

    @app.errorhandler(pymysql.err.IntegrityError)
    def handle_integrity_error(err):
        # The transaction is already rolled back in db.py; explain what clashed.
        number = err.args[0] if err.args else None
        log.warning("Database constraint on %s %s: %s", request.method, request.path, err)
        if number == MYSQL_DUPLICATE:
            return _error_response(409, "A record with this value already exists.")
        if number == MYSQL_BAD_FOREIGN_KEY:
            return _error_response(400, "A linked record (supplier or product) does not exist.")
        if number == MYSQL_ROW_IN_USE:
            return _error_response(409, "This record is still used by other records.")
        return _error_response(400, "The data breaks a database rule.")

    @app.errorhandler(pymysql.err.OperationalError)
    def handle_operational_error(err):
        number = err.args[0] if err.args else None
        if number in MYSQL_CHECK_FAILED:
            log.warning("CHECK constraint on %s %s: %s", request.method, request.path, err)
            return _error_response(400, "A value is outside the allowed range.")
        log.exception("Database unavailable on %s %s", request.method, request.path)
        return _error_response(503, "The database is not reachable. Try again shortly.")

    @app.errorhandler(HTTPException)
    def handle_http_error(err):
        # 400 bad JSON, 404 unknown URL, 405 wrong method, ...
        log.info("%s %s -> %s", request.method, request.path, err.code)
        return _error_response(err.code, err.description)

    @app.errorhandler(Exception)
    def handle_unexpected_error(err):
        # Full traceback goes to the log only; the user never sees it.
        log.exception("Unhandled error on %s %s", request.method, request.path)
        return _error_response(500, "Something went wrong on the server.")


def configure_logging(app):
    """Console logging always; a rotating logs/app.log file unless LOG_FILE is None."""
    level = getattr(logging, app.config["LOG_LEVEL"].upper(), logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    log.setLevel(level)
    if not log.handlers:
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        log.addHandler(console)
        if app.config.get("LOG_FILE"):
            os.makedirs(os.path.dirname(app.config["LOG_FILE"]), exist_ok=True)
            file_handler = RotatingFileHandler(app.config["LOG_FILE"], maxBytes=1_000_000,
                                               backupCount=3, encoding="utf-8")
            file_handler.setFormatter(fmt)
            log.addHandler(file_handler)

    @app.before_request
    def start_timer():
        g.request_started = time.perf_counter()

    @app.after_request
    def log_request(response):
        if not request.path.startswith("/static/"):
            ms = (time.perf_counter() - g.get("request_started", time.perf_counter())) * 1000
            log.info("%s %s %s %.0fms", request.method, request.full_path.rstrip("?"),
                     response.status_code, ms)
        return response
