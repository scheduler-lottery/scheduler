"""
Database access.

A thin layer over SQLAlchemy so the same SQL runs on Postgres (Supabase, in
production) and SQLite (local development and tests). Queries are plain SQL
with :named parameters, and rows come back as plain dicts.

Each request gets one connection, opened on first use. Nothing is saved
until the route calls commit() — whatever's uncommitted when the request
ends is rolled back.
"""

import os
import re
import threading
import traceback

from flask import g, has_request_context, request
from sqlalchemy import create_engine, event, text

import settings
from util import iso, new_id

SCHEMA_VERSION = "14"
SCHEMA_PATH = os.path.join(settings.BASE_DIR, "schema.sql")

# Every table the app owns. On Postgres each gets row-level security turned
# on with no policies, which shuts Supabase's auto-generated public REST API
# out of them. The app connects as the tables' owner, which RLS doesn't limit.
TABLES = [
    "app_state", "instructors", "sheets", "sheet_days", "roster", "submissions",
    "assignments", "name_pins", "student_signouts", "auth_failures", "login_codes",
    "login_links", "email_log", "snapshots", "pending_uploads", "error_log", "site_assets", "usage_daily",
    "remote_users", "invitations", "canvas_imports",
]

# Columns added after a table first existed. CREATE TABLE IF NOT EXISTS
# won't add them to a table that's already there, so they're added here —
# each only if missing, so this is safe to run on every start.
MIGRATIONS = [
    ("instructors", "timezone", "TEXT NOT NULL DEFAULT ''"),
    ("sheets", "rank_by", "TEXT NOT NULL DEFAULT ''"),
    ("sheets", "include_unranked", "INTEGER NOT NULL DEFAULT 1"),
    ("sheets", "lottery_seed", "INTEGER NOT NULL DEFAULT 0"),
    ("sheets", "scheduled_at", "TEXT"),
    ("sheets", "schedule_signature", "TEXT"),
    ("sheets", "published_at", "TEXT"),
    ("login_codes", "prev_hash", "TEXT NOT NULL DEFAULT ''"),
    ("login_codes", "prev_expires_at", "TEXT"),
    ("snapshots", "summary", "TEXT NOT NULL DEFAULT ''"),
    ("snapshots", "content_hash", "TEXT NOT NULL DEFAULT ''"),
    ("student_signouts", "reason", "TEXT NOT NULL DEFAULT ''"),
    ("student_signouts", "email", "TEXT NOT NULL DEFAULT ''"),
    ("login_codes", "from_instructor", "INTEGER NOT NULL DEFAULT 0"),
    ("sheet_days", "day_date", "TEXT"),
    ("sheets", "short_url", "TEXT"),
    ("sheets", "archived_at", "TEXT"),
    ("sheets", "theme", "TEXT"),
    ("sheets", "font", "TEXT"),
    ("login_links", "from_instructor", "INTEGER NOT NULL DEFAULT 0"),
    ("instructors", "canvas_host", "TEXT"),
    ("instructors", "canvas_school", "TEXT"),
    ("sheets", "closes_at", "TEXT"),
    ("sheets", "at_close", "TEXT NOT NULL DEFAULT 'ask'"),
    ("sheets", "auto_closed_at", "TEXT"),
    ("sheets", "deadline_asked_at", "TEXT"),
    ("sheets", "all_ranked_at", "TEXT"),
    ("roster", "name_pending", "INTEGER NOT NULL DEFAULT 0"),
]

_engine = None
_schema_ready = False
_lock = threading.RLock()


def database_url():
    url = settings.DATABASE_URL
    if not url:
        return "sqlite:///" + settings.SQLITE_PATH
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def get_engine():
    global _engine
    with _lock:
        if _engine is None:
            url = database_url()
            if url.startswith("sqlite"):
                engine = create_engine(
                    url, hide_parameters=True, connect_args={"check_same_thread": False}
                )

                @event.listens_for(engine, "connect")
                def _sqlite_foreign_keys(dbapi_conn, _record):
                    dbapi_conn.execute("PRAGMA foreign_keys = ON")
            else:
                # Supabase's transaction pooler hands each transaction to
                # whichever server connection is free, so server-side
                # prepared statements can't be reused between them.
                connect_args = {"prepare_threshold": None, "connect_timeout": 10}
                if "sslmode=" not in url and not any(h in url for h in ("@localhost", "@127.0.0.1")):
                    connect_args["sslmode"] = "require"  # never talk to a remote database unencrypted
                engine = create_engine(
                    url,
                    # Keep query values (student names, emails) out of error
                    # messages, which end up in logs and the error_log table.
                    hide_parameters=True,
                    pool_size=2,
                    max_overflow=3,
                    pool_pre_ping=True,
                    pool_recycle=240,
                    connect_args=connect_args,
                )
            _engine = engine
        return _engine


def reset_engine():
    """Forget the current engine (tests point the app at a fresh database)."""
    global _engine, _schema_ready
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _schema_ready = False


def is_postgres():
    return get_engine().dialect.name == "postgresql"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def schema_statements():
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        lines = [ln for ln in fh.read().splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def apply_schema(connection):
    for statement in schema_statements():
        connection.execute(text(statement))
    from sqlalchemy import inspect

    inspector = inspect(connection)
    for table, column, ddl in MIGRATIONS:
        existing = {c["name"] for c in inspector.get_columns(table)}
        if column not in existing:
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
    if connection.dialect.name == "postgresql":
        for table in TABLES:
            connection.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    connection.execute(
        text(
            "INSERT INTO app_state (key, value) VALUES ('schema_version', :v) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value"
        ),
        {"v": SCHEMA_VERSION},
    )
    connection.commit()


def _current_version(connection):
    try:
        return connection.execute(
            text("SELECT value FROM app_state WHERE key = 'schema_version'")
        ).scalar()
    except Exception:
        connection.rollback()  # Postgres won't run anything else until we do
        return None


def ensure_schema():
    """Create the tables on first use. After that it's one cheap query per
    server start, which is all a cold start on Vercel pays."""
    global _schema_ready
    if _schema_ready:
        return
    with _lock:
        if _schema_ready:
            return
        with get_engine().connect() as connection:
            if _current_version(connection) != SCHEMA_VERSION:
                apply_schema(connection)
        _schema_ready = True


# ---------------------------------------------------------------------------
# Per-request connection and query helpers
# ---------------------------------------------------------------------------
def conn():
    connection = g.get("_db")
    if connection is None:
        ensure_schema()
        connection = g._db = get_engine().connect()
    return connection


def close_conn(_exc=None):
    connection = g.pop("_db", None)
    if connection is not None:
        connection.close()  # rolls back anything left uncommitted


def rows(sql, **params):
    return [dict(r) for r in conn().execute(text(sql), params).mappings()]


def row(sql, **params):
    found = conn().execute(text(sql), params).mappings().first()
    return dict(found) if found is not None else None


def scalar(sql, **params):
    return conn().execute(text(sql), params).scalar()


def run(sql, **params):
    return conn().execute(text(sql), params).rowcount


def run_many(sql, param_list):
    param_list = list(param_list)
    if param_list:
        conn().execute(text(sql), param_list)


def commit():
    conn().commit()


def rollback():
    connection = g.get("_db")
    if connection is not None:
        connection.rollback()


# ---------------------------------------------------------------------------
# Error log
# ---------------------------------------------------------------------------
EMAIL_PATTERN = re.compile(r"[^\s@'\"<>(),;:]+@[^\s@'\"<>(),;:]+\.[A-Za-z]{2,}")


def _scrub(value):
    """Error text can quote whatever was being handled — a mail server's
    refusal names the recipient, for instance. Never keep addresses."""
    return EMAIL_PATTERN.sub("[email]", value or "")


def _route():
    """The route pattern ("/teach/s/<sid>/roster"), not the actual path: a
    sheet id is effectively a key to that sheet, so the log never holds one."""
    if has_request_context():
        if request.url_rule is not None:
            return request.url_rule.rule
        return "(unmatched path)"
    return ""


def record_error(method, error, detail=None):
    """Write to error_log on a connection of its own, so the entry is saved
    even when the request that failed is about to roll back. Never raises."""
    try:
        detail = _scrub(detail if detail is not None else traceback.format_exc())
        error = _scrub(error)
        path = _route()
        with get_engine().begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO error_log (id, created_at, method, path, error, detail) "
                    "VALUES (:id, :at, :method, :path, :error, :detail)"
                ),
                {
                    "id": new_id(12),
                    "at": iso(),
                    "method": (method or "")[:10],
                    "path": (path or "")[:300],
                    "error": (error or "")[:500],
                    "detail": (detail or "")[-6000:],
                },
            )
    except Exception:
        traceback.print_exc()
