"""
Test setup. Every app test runs twice: against SQLite, and against a real
Postgres server when one is available — set TEST_PG_BIN to the folder holding
initdb/pg_ctl (e.g. TEST_PG_BIN=/opt/homebrew/opt/postgresql@17/bin).
Email goes to an in-memory outbox instead of the network.
"""

import io
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile

import pytest
from sqlalchemy import create_engine, text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as app_module  # noqa: E402
import db  # noqa: E402
import settings  # noqa: E402

PG_BIN = os.environ.get("TEST_PG_BIN", "")

CANVAS_ROSTER = (
    "Full name,Sortable name,Canvas user id,Overall course grade,Assignment on time percent,"
    "Last page view time,Last participation time,Last logged out,Page Views,Participations,Email,SIS Id\n"
    "Alex Johnson,\"Johnson, Alex\",101,93%,100,2026-10-01,2026-10-01,,55,12,alex@school.edu,9001\n"
    "Sam Lee,\"Lee, Sam\",102,88%,90,2026-10-01,2026-10-01,,40,8,sam@school.edu,9002\n"
    "Riya Patel,\"Patel, Riya\",103,95%,100,2026-10-01,2026-10-01,,61,14,riya@school.edu,9003\n"
    "Test Student,\"Student, Test\",104,,,,,,,,,\n"
)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def pg_server():
    if not PG_BIN:
        pytest.skip("set TEST_PG_BIN to also run against Postgres")
    datadir = tempfile.mkdtemp(prefix="sched-pg-")
    subprocess.run(
        # UTF8 like Supabase; without it, a shell with no locale gets SQL_ASCII.
        [os.path.join(PG_BIN, "initdb"), "-D", datadir, "-U", "postgres", "-A", "trust", "--no-sync",
         "-E", "UTF8", "--no-locale"],
        check=True, capture_output=True,
    )
    port = _free_port()
    subprocess.run(
        [os.path.join(PG_BIN, "pg_ctl"), "-D", datadir, "-l", os.path.join(datadir, "log"), "-w",
         "-o", f"-p {port} -h 127.0.0.1 -k {datadir} -F", "start"],
        check=True, capture_output=True,
    )
    yield f"postgresql://postgres@127.0.0.1:{port}/postgres"
    subprocess.run([os.path.join(PG_BIN, "pg_ctl"), "-D", datadir, "-m", "immediate", "stop"], capture_output=True)
    shutil.rmtree(datadir, ignore_errors=True)


@pytest.fixture(params=["sqlite", "postgres"])
def app(request, tmp_path, monkeypatch):
    if request.param == "postgres":
        url = request.getfixturevalue("pg_server")
        engine = create_engine(url.replace("postgresql://", "postgresql+psycopg://"))
        with engine.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
        engine.dispose()
        monkeypatch.setattr(settings, "DATABASE_URL", url)
    else:
        monkeypatch.setattr(settings, "DATABASE_URL", "")
        monkeypatch.setattr(settings, "SQLITE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "IS_VERCEL", False)
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(settings, "SMTP_FROM", "codes@test.example")
    monkeypatch.setattr(settings, "OWNER_EMAIL", "owner@gmail.com")
    monkeypatch.setattr(settings, "INSTRUCTOR_EMAIL_DOMAINS", [".edu"])
    monkeypatch.setattr(settings, "EMAIL_DAILY_LIMIT", 90)
    monkeypatch.setattr(settings, "CRON_SECRET", "cron-secret")

    flask_app = app_module.app
    flask_app.config.update(TESTING=True, MAIL_BACKEND="locmem")
    app_module.mail.init_app(flask_app)
    flask_app.extensions["mailman"].outbox = []
    db.reset_engine()
    yield flask_app
    db.reset_engine()


class Browser:
    """One person's browser: its own cookies, plus helpers for the forms."""

    def __init__(self, flask_app):
        self.app = flask_app
        self.client = flask_app.test_client()
        self.token = secrets.token_urlsafe(16)  # like a real browser's own session token

    # -- plumbing ---------------------------------------------------------
    def csrf(self):
        with self.client.session_transaction() as s:
            s.setdefault("csrf", self.token)
            return s["csrf"]

    def get(self, url, **kw):
        return self.client.get(url, **kw)

    def post(self, url, data=None, **kw):
        data = dict(data or {})
        data.setdefault("csrf_token", self.csrf())
        return self.client.post(url, data=data, **kw)

    def post_json(self, url, payload):
        return self.client.post(url, json=payload, headers={"X-CSRF-Token": self.csrf()})

    @property
    def outbox(self):
        return self.app.extensions["mailman"].outbox

    def last_code(self, to=None):
        for message in reversed(self.outbox):
            if (to is None or to in message.to) and "code" in message.subject:
                return re.search(r"\n    (\d{6})\n", message.body).group(1)
        raise AssertionError(f"no code emailed to {to}")

    # -- instructor -------------------------------------------------------
    def sign_in_instructor(self, email="prof@school.edu"):
        r = self.post("/teach/login", {"email": email})
        assert r.status_code == 302, r.get_data(as_text=True)
        r = self.post("/teach/verify", {"code": self.last_code(email)})
        assert r.status_code == 302 and "/teach" in r.headers["Location"]
        return self

    def create_sheet(self, title="CS & Law presentations", days=("Oct 27", "Nov 10", "Nov 17", "Nov 24"),
                     capacity=2, allow_unlisted=True, show_preview=True):
        r = self.post("/teach/new", {
            "title": title,
            "day_date": [test_date(label, i) for i, label in enumerate(days)],
            "day_key": [""] * len(days),
            "capacity": str(capacity),
            "allow_unlisted": "1" if allow_unlisted else "0",
            "show_preview": "1" if show_preview else "",
        })
        assert r.status_code == 302, r.get_data(as_text=True)
        return r.headers["Location"].split("#")[0].rstrip("/").split("/")[-1]

    def upload(self, sid, content=CANVAS_ROSTER, filename="roster.csv"):
        if isinstance(content, str):
            content = content.encode("utf-8")
        return self.post(
            f"/teach/s/{sid}/roster",
            {"roster": (io.BytesIO(content), filename)},
            content_type="multipart/form-data",
        )

    # -- student ----------------------------------------------------------
    def sign_in_student(self, sid, name, email=None, pin="4831"):
        """Sign in the way a student would: type the name, confirm "I'm new"
        if asked, then the emailed code or a PIN."""
        r = self.post(f"/c/{sid}/login", {"name": name})
        if r.status_code == 200 and "Sign me up as a new person" in r.get_data(as_text=True):
            r = self.post(f"/c/{sid}/login", {"name": name, "pick": "__new__"})
        assert r.status_code == 302, r.get_data(as_text=True)
        if r.headers["Location"].endswith("/verify"):
            r = self.post(f"/c/{sid}/verify", {"code": self.last_code(email)})
        elif r.headers["Location"].endswith("/pin"):
            r = self.enter_pin(sid, pin)
        assert r.status_code == 302 and r.headers["Location"].endswith("/home"), r.headers["Location"]
        return self

    def enter_pin(self, sid, pin):
        page = self.get(f"/c/{sid}/pin").get_data(as_text=True)
        if "Choose a PIN" in page:
            return self.post(f"/c/{sid}/pin", {"mode": "choose", "pin": pin, "pin_again": pin})
        return self.post(f"/c/{sid}/pin", {"mode": "enter", "pin": pin})

    def rank(self, sid, ranking, excluded=(), comments=None, base_version=None):
        payload = {"ranking": list(ranking), "excluded": list(excluded), "comments": comments or {}}
        if base_version is not None:
            payload["base_version"] = base_version
        return self.post_json(f"/c/{sid}/save", payload)


@pytest.fixture
def browser(app):
    return lambda: Browser(app)


@pytest.fixture
def prof(browser):
    return browser().sign_in_instructor("prof@school.edu")


WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def test_date(label, i=0):
    """A real date for a day named in a test: "Mon".."Sun" fall in the week
    of Mon, Mar 1, 2027; "Oct 27" is that day in 2027; anything else is a
    week apart. (Days are picked on a calendar, so they're always dates.)"""
    from datetime import date, datetime, timedelta

    if label[:3] in WEEKDAYS and len(label) <= 9:
        return (date(2027, 3, 1) + timedelta(days=WEEKDAYS.index(label[:3]))).isoformat()
    try:
        return datetime.strptime(f"{label} 2027", "%b %d %Y").date().isoformat()
    except ValueError:
        return (date(2027, 3, 1) + timedelta(days=7 * i)).isoformat()


def day_dates(sid):
    with app_module.app.app_context():
        return [d["day_date"] for d in db.rows(
            "SELECT day_date FROM sheet_days WHERE sheet_id = :sid ORDER BY sort_order", sid=sid
        )]


def day_keys(sid):
    """The sheet's day keys, in order (read straight from the database)."""
    with app_module.app.app_context():
        return [d["key"] for d in db.rows(
            "SELECT day_key AS key FROM sheet_days WHERE sheet_id = :sid ORDER BY sort_order", sid=sid
        )]
