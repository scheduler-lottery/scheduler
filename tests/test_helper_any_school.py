"""Scheduler Helper for any school's Canvas (1.1.0), its checklist on the
Canvas step (add it, import the class list, delete it), and what is kept:
nothing from Canvas but the names and emails a professor adds."""

import json

import db
import helperstore
import settings
from test_app import _canvas_list, q, raw, text, told_course

UPDATE_XML = ('<?xml version="1.0" encoding="UTF-8"?><gupdate xmlns="http://www.google.com/update2/response" '
              'protocol="2.0"><app appid="hafmidgmpikkkjphmcbnggidbaeoiapd" status="ok"><updatecheck '
              'codebase="https://x/y.crx" status="ok" version="{v}"/></app></gupdate>')


class _Answer:
    def __init__(self, body):
        self.body = body.encode()

    def read(self, limit):
        return self.body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _store_says(monkeypatch, version, asked):
    def urlopen(url, timeout):
        asked.append(url)
        if version is None:
            raise OSError("no answer")
        return _Answer(UPDATE_XML.format(v=version))

    monkeypatch.setattr(helperstore.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(helperstore, "_known", {"version": None, "until": None})


def test_the_store_version_is_asked_of_chrome_s_update_service_and_remembered(app, monkeypatch):
    asked = []
    _store_says(monkeypatch, "1.0.6", asked)
    monkeypatch.setattr(settings, "CANVAS_HELPER_ANY_SCHOOL", "")
    with app.app_context():
        assert helperstore.store_version() == "1.0.6" and not helperstore.any_school()
        assert "id%3Dhafmidgmpikkkjphmcbnggidbaeoiapd" in asked[0] and len(asked) == 1
        assert helperstore.store_version() == "1.0.6" and len(asked) == 1  # (remembered: this server)
        monkeypatch.setattr(helperstore, "_known", {"version": None, "until": None})
        assert helperstore.store_version() == "1.0.6" and len(asked) == 1  # (and in the database, for other servers)
        # 1.1.0 in the store: every school is offered it.
        db.run("DELETE FROM app_state WHERE key = :k", k=helperstore.KEY)
        db.commit()
        _store_says(monkeypatch, "1.1.0", asked)
        assert helperstore.any_school()
        # The update service down: only the schools it read from the start, and asked again soon (not in six hours).
        db.run("DELETE FROM app_state WHERE key = :k", k=helperstore.KEY)
        db.commit()
        _store_says(monkeypatch, None, asked)
        assert helperstore.store_version() is None and not helperstore.any_school()
        saved = json.loads(db.scalar("SELECT value FROM app_state WHERE key = :k", k=helperstore.KEY))
        assert saved["version"] is None
    # Said outright, either way.
    monkeypatch.setattr(settings, "CANVAS_HELPER_ANY_SCHOOL", "1")
    assert helperstore.any_school()
    monkeypatch.setattr(settings, "CANVAS_HELPER_ANY_SCHOOL", "0")
    assert not helperstore.any_school()


def test_the_canvas_step_offers_the_helper_for_any_school_once_the_store_has_1_1_0(prof, monkeypatch):
    sid = prof.create_sheet(allow_unlisted=False)
    assert "data-helper-any" not in raw(prof.get(f"/teach/s/{sid}"))  # (1.0.6 in the store: its schools only)
    monkeypatch.setattr(settings, "CANVAS_HELPER_ANY_SCHOOL", "1")
    assert "data-helper-any" in raw(prof.get(f"/teach/s/{sid}"))


def test_the_helper_is_a_three_step_checklist_with_a_word_on_what_is_kept(prof):
    sid = prof.create_sheet(allow_unlisted=False)
    page = raw(prof.get(f"/teach/s/{sid}/setup?step=students"))
    for step in ('data-check="add"', 'data-check="import"', 'data-check="done"'):
        assert step in page
    seen = text(page)
    assert "Add Scheduler Helper to Chrome" in seen and "Import your class list" in seen and "All done: delete it?" in seen
    assert "data-helper-go>Import class list</button>" in page
    assert "Is any of this recorded?" in seen and "Scheduler Helper keeps nothing." in seen
    assert "keeps only the names and email addresses you add" in seen
    assert "the browser on this computer remembers which Canvas course this sheet is for" in seen


def test_nothing_from_canvas_but_names_and_emails_is_kept_anywhere(prof):
    sid = prof.create_sheet(title="LAW 540", allow_unlisted=False)
    course = "2026FA_LAW_540_SEC20_SECRETCOURSE"
    prof.post(f"/teach/s/{sid}/canvas-list", {"list": _canvas_list(n=3, course=course, course_id="98765",
                                                                    host="canvas.secretschool.edu"), "how": "helper"})
    prof.get(f"/teach/s/{sid}")
    # A second list, waiting to be looked over, and a restore point taken when one replaces the list.
    r = prof.post(f"/teach/s/{sid}/canvas-list", {"list": _canvas_list(n=4, course=course, course_id="98765",
                                                                        host="canvas.secretschool.edu")})
    with prof.app.app_context():
        everything = json.dumps({t: [dict(r) for r in db.rows(f"SELECT * FROM {t}")] for t in db.TABLES}, default=str)
    assert "SECRETCOURSE" not in everything and "secretschool" not in everything and "98765" not in everything
    prof.post(r.headers["Location"], {"action": "replace"})
    with prof.app.app_context():
        everything = json.dumps({t: [dict(r) for r in db.rows(f"SELECT * FROM {t}")] for t in db.TABLES}, default=str)
    assert "SECRETCOURSE" not in everything and "secretschool" not in everything and "98765" not in everything
    assert "s0@u.school.edu" in everything  # (the class list itself: its names and emails)


def test_courses_kept_before_are_cleared_on_upgrade(app):
    with app.app_context():
        db.run("INSERT INTO instructors (id, email, created_at) VALUES ('i1', 'a@b.edu', '2026-01-01')")
        db.run("INSERT INTO sheets (id, owner_id, title, created_at, updated_at, canvas_course_id, canvas_course) "
               "VALUES ('s1', 'i1', 'T', '2026-01-01', '2026-01-01', '111', 'LAW 310')")
        db.commit()
        with db.get_engine().connect() as connection:
            db.apply_schema(connection)
        assert db.scalar("SELECT COALESCE(canvas_course_id, canvas_course) FROM sheets WHERE id = 's1'") is None


def test_the_page_tells_the_browser_the_course_once(prof):
    sid = prof.create_sheet(allow_unlisted=False)
    r = prof.post(f"/teach/s/{sid}/canvas-list", {"list": _canvas_list(n=2, course_id="321", course="ECON 1")})
    page = raw(prof.get(r.headers["Location"]))
    assert told_course(page) == {"id": "321", "name": "ECON 1"} and f'data-sheet="{sid}"' in page
    assert q(prof.app, "SELECT canvas_course_id FROM sheets WHERE id = :s", s=sid) is None
