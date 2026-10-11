"""Short links (example.com/<code>) and codes typed in the "Have a code?"
box: wrong ones are counted per browser and per network, and past the limit
no code is looked up at all, so the box and the short links can't be used to
try codes until one is a class's. A sheet keeps its short link through being
deleted and brought back. And several servers starting at once on an older
Postgres database all come up (the schema upgrade waits its turn)."""

import contextlib
import os
import random
import signal
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import create_engine, text

import db
import sheets
from conftest import ROOT
from test_app import follow, html, q
from util import ID_ALPHABET

OTHER_NETWORK = {"REMOTE_ADDR": "203.0.113.9"}


def short_of(prof, sid):
    """The sheet's short code, as its class page shows it."""
    prof.get(f"/teach/s/{sid}")
    return q(prof.app, "SELECT short_code FROM sheets WHERE id = :sid", sid=sid)


def guess(length=8):
    """A made-up code shaped like a short link's (what a scanner sends)."""
    while True:
        code = "".join(random.choice(ID_ALPHABET) for _ in range(length))
        if any(ch.isdigit() for ch in code):
            return code


def misses(app):
    return q(app, "SELECT COUNT(*) FROM auth_failures WHERE scope = 'join'")


# ---------------------------------------------------------------------------
# Guessing is limited, before any code is looked up
# ---------------------------------------------------------------------------
def test_wrong_short_links_count_and_then_even_the_right_one_waits(prof, browser):
    import app as app_module

    sid = prof.create_sheet()
    short = short_of(prof, sid)
    s = browser()
    for _ in range(app_module.CODE_MISSES_PER_BROWSER):
        assert s.get(f"/{guess()}").status_code == 404
    r = s.get(f"/{short}")
    assert r.status_code == 429
    page = html(r)
    assert "Too many links that didn’t match a class" in page and "the one with /c/ in it" in page
    assert 'id="join-code"' not in page  # the code box would be refused too
    assert "Too many codes that didn't match" in follow(s, s.get(f"/join?code={short}"))
    # The full link never goes through a code lookup, so it always works.
    assert s.get(f"/c/{sid}").status_code == 200
    # Someone else (another browser, another network) isn't held up.
    assert browser().get(f"/{short}", environ_base=OTHER_NETWORK).headers["Location"].endswith(f"/c/{sid}")


def test_a_scanner_that_sends_no_cookies_is_stopped_by_the_network_ceiling(prof, browser):
    import app as app_module

    sid = prof.create_sheet()
    short = short_of(prof, sid)
    for i in range(app_module.CODE_MISSES_PER_NETWORK):
        b = browser()  # a new browser every time, as a script that drops cookies is
        if i % 2:
            assert b.get(f"/{guess()}").status_code == 404
        else:
            b.get(f"/join?code={guess()}")
    assert browser().get(f"/{short}").status_code == 429
    b = browser()
    assert "Too many codes that didn't match" in follow(b, b.get(f"/join?code={short}"))
    assert browser().get(f"/{short}", environ_base=OTHER_NETWORK).headers["Location"].endswith(f"/c/{sid}")


def test_the_code_box_and_short_links_share_one_count(prof, browser):
    import app as app_module

    sid = prof.create_sheet()
    s = browser()
    half = app_module.CODE_MISSES_PER_BROWSER // 2
    for _ in range(half):
        s.get(f"/join?code={guess()}")
    for _ in range(app_module.CODE_MISSES_PER_BROWSER - half):
        s.get(f"/{guess()}")
    assert "Too many codes that didn't match" in follow(s, s.get(f"/join?code={sid}"))
    assert s.get(f"/{sid}").status_code == 429
    # Each miss is one row for the browser and one for the network.
    assert misses(prof.app) == 2 * app_module.CODE_MISSES_PER_BROWSER


def test_only_code_shaped_paths_are_counted(prof, browser):
    sid = prof.create_sheet()
    short = short_of(prof, sid)
    s = browser()
    for path in ("/robots.txt", "/wp-login.php", "/ads.txt", "/favicon.ico", "/xmlrpc.php", "/zzzzzz",
                 "/abc", "/.env", "/apple-touch-icon.png", "/" + "k" * 12 + "2"):
        assert s.get(path).status_code == 404, path
    assert misses(prof.app) == 0
    assert s.get(f"/{guess()}").status_code == 404
    assert misses(prof.app) == 2
    # A link copied with the end of a sentence, or typed in capitals, still works.
    for typed in (f"{short}.", f"{short.upper()}", f"{short},"):
        assert s.get(f"/{typed}").headers["Location"].endswith(f"/c/{sid}"), typed
    assert misses(prof.app) == 2


def test_hits_are_not_counted(prof, browser):
    sid = prof.create_sheet()
    short = short_of(prof, sid)
    s = browser()
    for _ in range(40):
        assert s.get(f"/{short}").headers["Location"].endswith(f"/c/{sid}")
    assert s.get(f"/join?code={sid}").headers["Location"].endswith(f"/c/{sid}")
    assert misses(prof.app) == 0


# ---------------------------------------------------------------------------
# 8-character short codes, with 6-character ones still working
# ---------------------------------------------------------------------------
def test_new_short_codes_are_8_characters_and_older_6_character_ones_still_work(prof, browser):
    sid = prof.create_sheet()
    short = short_of(prof, sid)
    assert len(short) == 8 and any(ch.isdigit() for ch in short) and all(ch in ID_ALPHABET for ch in short)
    older = prof.create_sheet(title="Last term")
    with prof.app.app_context():
        db.run("UPDATE sheets SET short_code = 'k7q2mx' WHERE id = :sid", sid=older)
        db.commit()
    assert "http://localhost/k7q2mx" in html(prof.get(f"/teach/s/{older}"))  # kept, not replaced
    s = browser()
    assert s.get("/k7q2mx").headers["Location"].endswith(f"/c/{older}")
    assert s.get("/join?code=K7Q2MX").headers["Location"].endswith(f"/c/{older}")
    assert s.get(f"/{short}").headers["Location"].endswith(f"/c/{sid}")


def test_a_short_code_is_never_an_older_class_links_code(prof, browser, monkeypatch):
    # Older classes have 8-character links too, and those are looked up first.
    first = prof.create_sheet(title="Spring")
    with prof.app.app_context():
        data = sheets.export_sheet(sheets.get_sheet(first))
        sheets.restore("k7q2mx9p", sheets.get_sheet(first)["owner_id"], data, keep_newer=False)
        db.commit()
    made = iter(["k7q2mx9p", "w8kewx3m"])
    real = sheets.new_id
    monkeypatch.setattr(sheets, "new_id", lambda n=12: next(made) if n == sheets.SHORT_CODE_LENGTH else real(n))
    sid = prof.create_sheet(title="Fall")
    assert short_of(prof, sid) == "w8kewx3m"
    s = browser()
    assert s.get("/k7q2mx9p").headers["Location"].endswith("/c/k7q2mx9p")
    assert s.get("/w8kewx3m").headers["Location"].endswith(f"/c/{sid}")


# ---------------------------------------------------------------------------
# Delete, bring back: the same short link
# ---------------------------------------------------------------------------
def deleted_snapshot(prof, sid):
    return q(prof.app, "SELECT id FROM snapshots WHERE sheet_id = :sid AND kind = 'deleted'", sid=sid)


def test_a_sheet_brought_back_keeps_its_short_link(prof, browser):
    sid = prof.create_sheet(title="Oops")
    short = short_of(prof, sid)
    prof.post(f"/teach/s/{sid}/delete-sheet", {"confirm": "DELETE"})
    assert q(prof.app, "SELECT COUNT(*) FROM sheets WHERE id = :sid", sid=sid) == 0
    r = prof.post(f"/teach/restore-deleted/{deleted_snapshot(prof, sid)}")
    assert "Its student link works again too" in follow(prof, r)
    assert short_of(prof, sid) == short
    assert browser().get(f"/{short}").headers["Location"].endswith(f"/c/{sid}")


def test_a_short_link_taken_meanwhile_stays_with_the_class_that_has_it(prof, browser):
    sid = prof.create_sheet(title="Oops")
    short = short_of(prof, sid)
    prof.post(f"/teach/s/{sid}/delete-sheet", {"confirm": "DELETE"})
    other = prof.create_sheet(title="Another class")
    with prof.app.app_context():
        db.run("UPDATE sheets SET short_code = :c WHERE id = :sid", c=short, sid=other)
        db.commit()
    prof.post(f"/teach/restore-deleted/{deleted_snapshot(prof, sid)}")
    assert short_of(prof, sid) not in (short, None)
    assert browser().get(f"/{short}").headers["Location"].endswith(f"/c/{other}")


def test_a_backup_brought_back_as_a_new_sheet_gets_its_own_short_link(prof):
    sid = prof.create_sheet()
    short = short_of(prof, sid)
    with prof.app.app_context():
        data = sheets.export_sheet(sheets.get_sheet(sid))
        assert data["sheet"]["short_code"] == short  # in backups and restore points
        new_sid = sheets.new_sheet_id()
        sheets.restore(new_sid, sheets.get_sheet(sid)["owner_id"], data, keep_newer=False)
        # Brought back over the same sheet (not deleted): the link it has stays.
        data["sheet"]["short_code"] = "m4n5p6q7"
        sheets.restore(sid, sheets.get_sheet(sid)["owner_id"], data, keep_newer=False)
        db.commit()
    assert short_of(prof, new_sid) not in (short, None)
    assert short_of(prof, sid) == short


@contextlib.contextmanager
def within(seconds):
    """Fails the test, rather than hanging the run, if this takes too long."""
    def stuck(*_):
        raise TimeoutError(f"still going after {seconds} seconds")

    previous = signal.signal(signal.SIGALRM, stuck)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def test_short_code_for_a_sheet_that_is_gone_is_none(app):
    with within(5), app.app_context():
        assert sheets.short_code("nosuchsheet2") is None


def test_class_page_deleted_from_another_tab_while_loading(prof, monkeypatch):
    sid = prof.create_sheet()
    real = sheets.short_code

    def deleted_meanwhile(code_sid):
        sheets.delete_sheet(sheets.get_sheet(code_sid))
        db.commit()
        return real(code_sid)

    monkeypatch.setattr(sheets, "short_code", deleted_meanwhile)
    with within(10):
        r = prof.get(f"/teach/s/{sid}")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/teach/s/{sid}")
    monkeypatch.setattr(sheets, "short_code", real)
    assert "You deleted this sheet" in html(prof.get(r.headers["Location"]))


# ---------------------------------------------------------------------------
# Several servers starting at once (Postgres)
# ---------------------------------------------------------------------------
STARTER = """
import sys
sys.path.insert(0, {root!r})
import db
db.get_engine().connect().close()  # connected already, so they all start together
print("ready", flush=True)
sys.stdin.readline()
db.ensure_schema()
print("ok", flush=True)
"""


def _new_database(pg_server):
    name = "start_" + uuid.uuid4().hex[:10]
    admin = create_engine(pg_server.replace("postgresql://", "postgresql+psycopg://"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f"CREATE DATABASE {name}"))
    admin.dispose()
    return pg_server.rsplit("/", 1)[0] + "/" + name


def _start_together(url, count):
    env = dict(os.environ, DATABASE_URL=url, PYTHONDONTWRITEBYTECODE="1")
    env.pop("VERCEL", None)
    procs = [subprocess.Popen([sys.executable, "-c", STARTER.format(root=ROOT)], cwd=ROOT, env=env, text=True,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
             for _ in range(count)]
    for p in procs:
        assert p.stdout.readline().strip() == "ready", p.communicate(timeout=30)[1]
    for p in procs:  # go
        p.stdin.write("\n")
        p.stdin.flush()
    results = []
    for p in procs:
        out, err = p.communicate(timeout=120)
        results.append((p.returncode, out.strip(), [ln for ln in err.splitlines() if "Error" in ln][-1:]))
    return results


def _version_and_column(url):
    engine = create_engine(url.replace("postgresql://", "postgresql+psycopg://"))
    with engine.connect() as c:
        version = c.execute(text("SELECT value FROM app_state WHERE key = 'schema_version'")).scalar()
        column = c.execute(text("SELECT 1 FROM information_schema.columns "
                                "WHERE table_name = 'sheets' AND column_name = 'short_code'")).scalar()
    engine.dispose()
    return version, bool(column)


@pytest.mark.parametrize("trial", range(3))
def test_servers_starting_together_all_upgrade_a_version_16_database(pg_server, trial):
    url = _new_database(pg_server)
    engine = create_engine(url.replace("postgresql://", "postgresql+psycopg://"))
    with engine.connect() as c:
        db.apply_schema(c)
        # Version 16: the same, but before sheets had a short link of this site's own.
        c.execute(text("DROP INDEX sheets_by_short_code"))
        c.execute(text("ALTER TABLE sheets DROP COLUMN short_code"))
        c.execute(text("UPDATE app_state SET value = '16' WHERE key = 'schema_version'"))
        c.commit()
    engine.dispose()
    results = _start_together(url, 4)
    assert all(code == 0 and out.endswith("ok") for code, out, _err in results), results
    assert _version_and_column(url) == (db.SCHEMA_VERSION, True)


def test_servers_starting_together_on_an_empty_database_all_come_up(pg_server):
    url = _new_database(pg_server)
    results = _start_together(url, 4)
    assert all(code == 0 and out.endswith("ok") for code, out, _err in results), results
    assert _version_and_column(url) == (db.SCHEMA_VERSION, True)
