"""
How close the site is to its free plans' limits, for the owner's page.

The site measures what it can itself: the database's size (exactly), and,
for each day, the requests it answers, the bytes it sends back, and the time
it spends answering (an upper bound on Vercel's "Active CPU", which leaves
out time spent waiting on the database). Static files are served by Vercel's
network without reaching the site, so those numbers leave them out; the
Vercel and Supabase dashboards have the exact totals.

The daily job emails the owner when anything passes ALERT_AT.
"""

import os
import time
from dataclasses import dataclass
from datetime import timedelta

from flask import g, request
from sqlalchemy import text

import db
import settings
import signin
from util import iso, now

# Free-plan limits: Vercel Hobby (per month) and Supabase Free.
VERCEL_INVOCATIONS = 1_000_000
VERCEL_CPU_HOURS = 4
VERCEL_TRANSFER_GB = 100
SUPABASE_DATABASE_MB = 500
WARN_AT = 0.7
ALERT_AT = 0.75
DANGER_AT = 0.9
KEEP_DAYS = 120


# ---------------------------------------------------------------------------
# Counting each request
# ---------------------------------------------------------------------------
def start():
    g.usage_started = time.perf_counter()


def measure(response):
    started = g.get("usage_started")
    if started is not None and request.endpoint != "static":
        g.usage = (int((time.perf_counter() - started) * 1000), response.calculate_content_length() or 0)
    return response


def save(_exc=None):
    """Add this request to today's totals. Runs after the request's own
    database connection has closed, on a connection of its own, so it can
    never commit (or wait on) anything the page left unfinished."""
    found = g.pop("usage", None)
    if not found:
        return
    milliseconds, size = found
    try:
        db.ensure_schema()
        with db.get_engine().begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO usage_daily (day, kind, amount) VALUES "
                    "(:day, 'requests', 1), (:day, 'bytes', :size), (:day, 'ms', :ms) "
                    "ON CONFLICT (day, kind) DO UPDATE SET amount = usage_daily.amount + excluded.amount"
                ),
                {"day": now().date().isoformat(), "size": size, "ms": milliseconds},
            )
    except Exception:  # noqa: BLE001 - bookkeeping must never break a page
        pass


# ---------------------------------------------------------------------------
# The meters
# ---------------------------------------------------------------------------
@dataclass
class Meter:
    name: str
    used: float
    limit: float
    unit: str
    note: str

    @property
    def share(self):
        return self.used / self.limit if self.limit else 0

    @property
    def level(self):
        return "danger" if self.share >= DANGER_AT else "warn" if self.share >= WARN_AT else "ok"

    def shown(self, value):
        if self.unit in ("MB", "GB", "hours"):
            return f"{value:,.2f}".rstrip("0").rstrip(".") + f" {self.unit}"
        return f"{int(value):,}"


def database_bytes():
    if db.is_postgres():
        return db.scalar("SELECT pg_database_size(current_database())") or 0
    try:
        return os.path.getsize(settings.SQLITE_PATH)
    except OSError:
        return 0


def meters():
    since = (now().date() - timedelta(days=29)).isoformat()
    totals = {
        r["kind"]: int(r["total"] or 0)
        for r in db.rows("SELECT kind, SUM(amount) AS total FROM usage_daily WHERE day >= :since GROUP BY kind",
                         since=since)
    }
    return [
        Meter("Database size", database_bytes() / 1_000_000, SUPABASE_DATABASE_MB, "MB",
              "Supabase's free plan holds 500 MB. Past that, the database stops taking new data."),
        Meter("Requests answered, last 30 days", totals.get("requests", 0), VERCEL_INVOCATIONS, "",
              "Vercel's free plan allows 1,000,000 a month. Past that, the site pauses for up to 30 days."),
        Meter("Time spent answering, last 30 days", totals.get("ms", 0) / 3_600_000, VERCEL_CPU_HOURS, "hours",
              "Vercel's free plan allows 4 hours of active processing a month. This counts every moment, waiting "
              "included, so the real figure is lower."),
        Meter("Data sent, last 30 days", totals.get("bytes", 0) / 1_000_000_000, VERCEL_TRANSFER_GB, "GB",
              "Vercel's free plan allows 100 GB a month. This counts pages; styles, scripts, and fonts add a "
              "little, and browsers keep them."),
        Meter("Sign-in emails, last 24 hours", signin.emails_sent_today(), settings.EMAIL_DAILY_LIMIT, "",
              "This site's own daily cap on sign-in emails (EMAIL_DAILY_LIMIT). Past it, new codes wait until "
              "tomorrow."),
    ]


def first_day():
    return db.scalar("SELECT MIN(day) FROM usage_daily")


def _state(key):
    return db.scalar("SELECT value FROM app_state WHERE key = :key", key=key)


def _set_state(key, value):
    db.run(
        "INSERT INTO app_state (key, value) VALUES (:key, :value) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        key=key, value=value,
    )


def last_daily_job():
    return _state("daily_job_at")


# ---------------------------------------------------------------------------
# The daily check (called from maintenance.run_daily)
# ---------------------------------------------------------------------------
def daily_check():
    """Note that the daily job ran (it keeps Supabase's free project awake),
    drop old totals, and email the owner — at most once a day — when a
    meter passes ALERT_AT. Commits. Returns the names of meters over it."""
    _set_state("daily_job_at", iso())
    db.run("DELETE FROM usage_daily WHERE day < :cutoff",
           cutoff=(now().date() - timedelta(days=KEEP_DAYS)).isoformat())
    db.commit()
    current = meters()
    high = [m for m in current if m.share >= ALERT_AT]
    today = now().date().isoformat()
    if not high or _state("usage_alert_day") == today:
        return [m.name for m in high]
    if signin.smtp_ready() and settings.OWNER_EMAIL and signin.daily_room("alert") > 0:
        lines = [f"- {m.name}: {m.shown(m.used)} of {m.shown(m.limit)} ({m.share:.0%}). {m.note}" for m in current]
        body = (
            f"{settings.APP_NAME} is getting close to a free-plan limit:\n\n"
            + "\n".join(f"  * {m.name} is at {m.share:.0%}" for m in high)
            + "\n\nAll the meters:\n" + "\n".join(lines)
            + "\n\nSee them any time on the site's /owner page. The Vercel and Supabase dashboards have the "
              "exact figures.\n"
        )
        try:
            signin.send_email(settings.OWNER_EMAIL, f"{settings.APP_NAME}: close to a free-plan limit", body)
            signin.log_email(settings.OWNER_EMAIL, "alert")
            _set_state("usage_alert_day", today)
            db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            db.record_error("EMAIL", f"Sending the usage alert failed: {exc!r}")
    return [m.name for m in high]
