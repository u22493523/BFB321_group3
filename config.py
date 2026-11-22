"""Settings for each environment: development, testing and production.

Choose one with APP_ENV in .env (development is the default). Secrets and
database details always come from .env, never from this file.

    APP_ENV=development   debug on, logs to the console and logs/app.log
    APP_ENV=testing       separate test database (TEST_DB_NAME), console logs only
    APP_ENV=production    debug off, secure cookies, warnings and errors only
"""
import os

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


class Config:
    """Settings shared by every environment."""
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")

    # MySQL connection (read by db.py)
    DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
    DB_PORT = int(os.getenv("DB_PORT", "3306"))
    DB_USER = os.getenv("DB_USER", "root")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")
    DB_NAME = os.getenv("DB_NAME", "taphouse_didss")

    # Supply chain model: proposal objective 3 asks for at least 95%
    SERVICE_LEVEL = float(os.getenv("SERVICE_LEVEL", "0.95"))

    # API list endpoints: ?page=1&size=10, never more than 100 rows at once
    DEFAULT_PAGE_SIZE = 10
    MAX_PAGE_SIZE = 100
    MAX_CONTENT_LENGTH = 1 * 1024 * 1024   # CSV uploads: 1 MB at most (413 if larger)

    # Logging (set up in errors.py)
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE = os.path.join(BASE_DIR, "logs", "app.log")

    # Session cookie for the manager login (auth.py)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"     # not sent with requests from other sites

    DEBUG = False
    TESTING = False


class DevelopmentConfig(Config):
    DEBUG = True


class TestingConfig(Config):
    """Used by tests/test_api.py. Points at a separate database so the tests
    can wipe and rebuild it without touching your real data."""
    TESTING = True
    DB_NAME = os.getenv("TEST_DB_NAME", "taphouse_didss_test")
    LOG_FILE = None
    LOG_LEVEL = "WARNING"      # keep test output readable


class ProductionConfig(Config):
    LOG_LEVEL = os.getenv("LOG_LEVEL", "WARNING")
    SESSION_COOKIE_SECURE = True


CONFIGS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config(name=None):
    """Return the settings class for APP_ENV (or `name`), defaulting to development."""
    name = (name or os.getenv("APP_ENV", "development")).lower()
    if name not in CONFIGS:
        raise ValueError(f"APP_ENV must be one of {', '.join(CONFIGS)} (got '{name}').")
    return CONFIGS[name]
