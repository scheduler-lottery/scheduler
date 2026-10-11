"""The class list's review page and reports, second round: buttons that say
who comes and who goes, the report saying who was taken off and offering
the new students the link, "Wrong course?" for Scheduler Helper's lists,
the note about Canvas's file in Downloads, and the school and courses the
Canvas questions start from. Each runs on SQLite and on Postgres."""

import io
import json
import zipfile
from datetime import timedelta

import pytest
from flask import session, template_rendered

import canvasdir
import canvas_oauth
import db
import settings
import sheets
import teach
from conftest import CANVAS_ROSTER
from test_app import (NO_HEADINGS, _buttons, _canvas_list, _connect, _connectable_canvas, _pending_id, follow, html, q,
                      raw, text)
from util import now, parse_iso


def _csv(*people):
    """A class list file: (name, email) pairs, or names (with an email made from them)."""
    rows = [p if isinstance(p, tuple) else (p, p.lower().replace(" ", ".") + "@school.edu") for p in people]
    return "Name,Email\n" + "".join(f"{name},{email}\n" for name, email in rows)


STUDENTS = [f"Student {i}" for i in range(1, 6)]


@pytest.fixture
def listed(prof):
    """A sheet whose list is Student 1-5 and Pat Dropped, with its first report dismissed."""
    sid = prof.create_sheet(allow_unlisted=False)
    prof.upload(sid, _csv(*STUDENTS, "Pat Dropped"), "first.csv")
    prof.post(f"/teach/s/{sid}/roster/report/dismiss")
    return sid


def _review(prof, sid, content, filename="second.csv"):
    r = prof.upload(sid, content, filename)
    assert "/roster/review/" in r.headers["Location"], r.headers["Location"]
    return r.headers["Location"]


def _roster(app, sid):
    with app.app_context():
        return {r["display_name"]: r["email"] for r in sheets.get_roster(sid) if not r["is_test"]}


@pytest.fixture
def rendered(app):
    """The context of each page rendered: [(template name, context)]."""
    seen = []

    def record(sender, template, context, **extra):
        seen.append((template.name, context))
    template_rendered.connect(record, app)
    yield seen
    template_rendered.disconnect(record, app)


# ---------------------------------------------------------------------------
# The review page's buttons name who comes and who goes (#17)
# ---------------------------------------------------------------------------
def test_the_buttons_say_who_is_added_and_who_is_taken_off(prof, listed):
    review = _review(prof, listed, _csv(*STUDENTS, "Student 6"))
    page = raw(prof.get(review))
    assert _buttons(page) == [("Only add Student 6 (keep Pat Dropped on my list)", False),
                              ("Update my list: add Student 6, take off Pat Dropped", True),
                              ("Cancel — keep my current list", False)]
    assert "Rankings are never deleted. Before your list changes, a copy of it is saved" in text(page)
    assert "<bdi>Student 6</bdi>" in page  # (a name in any script reads the right way round)
    # Only taking off: one button that says so.
    review = _review(prof, listed, _csv(*STUDENTS))
    assert _buttons(raw(prof.get(review))) == [("Take Pat Dropped off my list", True),
                                               ("Cancel — keep my current list", False)]


def test_past_three_names_the_buttons_name_two_and_count_the_rest(prof):
    sid = prof.create_sheet(allow_unlisted=False)
    kids = [f"Kid {c}" for c in "ABCDEFGHIJ"]
    prof.upload(sid, _csv(*kids))
    many = [f"New {c}" for c in "ABCDE"]
    review = _review(prof, sid, _csv(*kids[:6], *many))
    assert _buttons(raw(prof.get(review)))[:2] == [
        ("Only add New A, New B and 3 more (keep everyone already on my list)", False),
        ("Update my list: add New A, New B and 3 more, take off Kid G, Kid H and 2 more", True)]
    # Three names or fewer: all of them.
    review = _review(prof, sid, _csv(*kids[:8], *many[:3]))
    assert _buttons(raw(prof.get(review)))[:2] == [
        ("Only add New A, New B and New C (keep Kid I and Kid J on my list)", False),
        ("Update my list: add New A, New B and New C, take off Kid I and Kid J", True)]


def test_with_nobody_taken_off_one_button_adds_them_and_updates_a_changed_email(prof, listed):
    changed = [(n, n.lower().replace(" ", ".") + "@school.edu") for n in STUDENTS]
    changed[2] = ("Student 3", "s3.new@school.edu")
    review = _review(prof, listed, _csv(*changed, "Pat Dropped", "Student 6"))
    assert _buttons(raw(prof.get(review))) == [("Add Student 6, and update Student 3’s email", True),
                                               ("Cancel — keep my current list", False)]
    page = follow(prof, prof.post(review, {"action": "replace"}))
    # Updated, not added again as a second Student 3.
    assert _roster(prof.app, listed)["Student 3"] == "s3.new@school.edu"
    assert not any(name.startswith("Student 3 (") for name in _roster(prof.app, listed))
    assert len(_roster(prof.app, listed)) == 7
    assert "Added 1 new student to your list from “second.csv”." in page and "Replaced your list" not in page
    assert "Taken off" not in page


def test_with_only_an_email_or_a_spelling_changed_the_button_says_so(prof, listed):
    review = _review(prof, listed, _csv(*STUDENTS[:2], ("Student 3", "s3.new@school.edu"), *STUDENTS[3:],
                                        "Pat Dropped"))
    assert _buttons(raw(prof.get(review)))[0] == ("Update Student 3’s email", True)
    two = [(n, f"{n[-1]}.new@school.edu") for n in STUDENTS[:2]]
    review = _review(prof, listed, _csv(*two, *STUDENTS[2:], "Pat Dropped"))
    assert _buttons(raw(prof.get(review)))[0] == ("Update the emails of Student 1 and Student 2", True)
    # A spelling only (an accent): the list is brought up to date, and the report says nobody came or went.
    review = _review(prof, listed, _csv(*STUDENTS, ("Pát Dropped", "pat.dropped@school.edu")))
    assert _buttons(raw(prof.get(review)))[0] == ("Update my list to match", True)
    page = follow(prof, prof.post(review, {"action": "replace"}))
    assert "Updated your list from “second.csv”: nobody new and nobody gone." in page
    assert "Pát Dropped" in _roster(prof.app, listed)


def test_keeping_an_email_the_new_list_lacks_is_said_in_the_buttons_words(prof, listed):
    review = _review(prof, listed, _csv(*STUDENTS[:4], ("Student 5", ""), "Pat Dropped", "Student 6"))
    page = text(prof.get(review))
    assert "If you update your list, we'll keep the emails we have for them." in page and "If you replace" not in page


# ---------------------------------------------------------------------------
# After an update: who was taken off, and the link for who's new (#18)
# ---------------------------------------------------------------------------
def test_after_an_update_on_a_shared_sheet_the_new_student_is_offered_the_link(prof, listed):
    prof.post(f"/teach/s/{listed}/shared")
    review = _review(prof, listed, _csv(*STUDENTS, "Student 6", ("Pin Person", "")))
    page = follow(prof, prof.post(review, {"action": "replace"}))
    assert "Replaced your list with 7 students" in page
    assert ("Taken off (1): <bdi>Pat Dropped</bdi>. They can't sign in to this sheet any more; anything they already "
            "ranked stays, marked “not on your list”.") in page
    assert "sign in differently" not in page  # nobody still on the list signs in differently
    assert "Send your new student the link" in page and "student.6@school.edu" in page.split('id="send-new"')[1]
    # Shown once.
    assert 'id="send-new"' not in html(prof.get(f"/teach/s/{listed}"))


def test_the_link_is_offered_only_once_the_sheet_is_shared_and_not_in_setup(prof, listed):
    review = _review(prof, listed, _csv(*STUDENTS, "Pat Dropped", "Student 6"))
    page = follow(prof, prof.post(review, {"action": "replace"}))
    assert "Added 1 new student" in page and 'id="send-new"' not in page  # nobody has the link yet
    prof.post(f"/teach/s/{listed}/shared")
    r = prof.post(f"/teach/s/{listed}/roster", {"roster": (io.BytesIO(_csv(*STUDENTS, "Pat Dropped", "Student 6",
                                                                           "Student 7").encode()), "third.csv"),
                                                "setup": "1"}, content_type="multipart/form-data")
    r = prof.post(r.headers["Location"], {"action": "replace"})
    assert r.headers["Location"].endswith("/setup?step=students")  # where sharing the link comes next
    with prof.client.session_transaction() as s:
        assert "just_added" not in s
    # "Only add" offers it too.
    r = prof.upload(listed, _csv("Student 1", "Student 8"), "fourth.csv")
    page = follow(prof, prof.post(r.headers["Location"], {"action": "add"}))
    assert "Added 1 new student" in page and "student.8@school.edu" in page.split('id="send-new"')[1]


def test_a_student_still_on_the_list_with_a_new_email_still_gets_the_word_about_signing_in(prof, listed):
    prof.post(f"/teach/s/{listed}/shared")
    review = _review(prof, listed, _csv(("Student 1", "s1.new@school.edu"), *STUDENTS[1:]))
    page = follow(prof, prof.post(review, {"action": "replace"}))
    assert "Some students now sign in differently" in page and "Taken off (1)" in page


def test_a_long_list_taken_off_names_twelve_and_counts_the_rest(prof):
    sid = prof.create_sheet(allow_unlisted=False)
    prof.upload(sid, _csv(*[f"Old {i:02}" for i in range(20)]))
    r = prof.upload(sid, _csv(*[f"New {i:02}" for i in range(20)]), "other.csv")
    page = text(follow(prof, prof.post(r.headers["Location"], {"action": "replace"})))
    assert "Taken off (20): Old 00 , Old 01" in page and "Old 11 and 8 more." in page and "Old 12" not in page


def test_the_setup_page_report_says_who_was_taken_off(prof):
    sid = prof.create_sheet(allow_unlisted=False)
    prof.post(f"/teach/s/{sid}/roster", {"roster": (io.BytesIO(_csv("Ana Lima", "Bo Chen").encode()), "a.csv"),
                                         "setup": "1"}, content_type="multipart/form-data")
    r = prof.post(f"/teach/s/{sid}/roster", {"roster": (io.BytesIO(_csv("Ana Lima", "Cy Cole").encode()), "b.csv"),
                                             "setup": "1"}, content_type="multipart/form-data")
    page = follow(prof, prof.post(r.headers["Location"], {"action": "replace"}))
    assert "Taken off (1): <bdi>Bo Chen</bdi>." in page


# ---------------------------------------------------------------------------
# "Wrong course?": Scheduler Helper's list turned down, and a list started over (#16, #2)
# ---------------------------------------------------------------------------
def test_wrong_course_on_a_helper_list_goes_back_to_ask_which_course(prof):
    sid = prof.create_sheet(title="LAW 310", allow_unlisted=False)
    prof.post(f"/teach/s/{sid}/canvas-list", {"list": _canvas_list(n=4, course_id="111", course="LAW 310")})
    r = prof.post(f"/teach/s/{sid}/canvas-list", {"list": _canvas_list(n=5, course_id="111", course="LAW 310")})
    review = r.headers["Location"]
    assert 'name="action" value="pick" class="btn-link">Pick a different Canvas course</button>' in raw(prof.get(review))
    r = prof.post(review, {"action": "pick"})
    assert r.headers["Location"].endswith(f"/teach/s/{sid}?pick=1#canvas-update")
    assert "Kept your current class list. Nothing changed." in follow(prof, r)
    assert not _pending_id(prof.app, sid) and len(_roster(prof.app, sid)) == 4
    assert q(prof.app, "SELECT canvas_course_id FROM sheets WHERE id = :s", s=sid) == "111"
    # From setup: back to setup, asking.
    r = prof.post(f"/teach/s/{sid}/canvas-list", {"list": _canvas_list(n=5, course_id="111", course="LAW 310"),
                                                  "setup": "1"})
    r = prof.post(r.headers["Location"], {"action": "pick"})
    assert r.headers["Location"].endswith(f"/teach/s/{sid}/setup?step=students&pick=1")


def test_only_a_helper_list_can_be_turned_down_for_its_course(prof, listed):
    review = _review(prof, listed, _csv(*STUDENTS, "Student 6"))
    page = raw(prof.get(review))
    assert 'value="pick"' not in page
    r = prof.post(review, {"action": "pick"})  # (a file has no course to pick: nothing happens)
    assert r.status_code == 200 and _pending_id(prof.app, listed)


def test_starting_over_a_list_from_canvas_asks_which_course_next(prof):
    sid = prof.create_sheet(title="LAW 310", allow_unlisted=False)
    prof.post(f"/teach/s/{sid}/canvas-list", {"list": _canvas_list(n=4, course_id="111", course="LAW 310")})
    r = prof.post(f"/teach/s/{sid}/roster/clear")
    assert r.headers["Location"].endswith(f"/teach/s/{sid}?pick=1#class-list")
    assert "Removed the class list (4 students)" in follow(prof, r)  # its Undo, as before
    # A list from a file: no course to ask about.
    b = prof.create_sheet(title="Other")
    prof.upload(b)
    r = prof.post(f"/teach/s/{b}/roster/clear")
    assert r.headers["Location"].endswith(f"/teach/s/{b}#class-list")
    r = prof.post(f"/teach/s/{b}/roster/clear", {"setup": "1"})
    assert r.headers["Location"].endswith(f"/teach/s/{b}/setup?step=students")


# ---------------------------------------------------------------------------
# Two students with one name, one fewer of them, and nothing to say which left (contract with sheets)
# ---------------------------------------------------------------------------
def test_when_it_cant_tell_which_twin_left_the_review_says_so(prof, monkeypatch):
    real = sheets.compare_rosters

    def unsure(current, incoming):
        return {**real(current, incoming), "unsure": ["Sam Rivera"]}
    sid = prof.create_sheet(allow_unlisted=False)
    prof.upload(sid, _csv("Ana Lima", "Bo Chen"))
    monkeypatch.setattr(sheets, "compare_rosters", unsure)
    # The same names again: not "already matches", since that would swallow the question.
    r = prof.upload(sid, _csv("Ana Lima", "Bo Chen"), "again.csv")
    assert "/roster/review/" in r.headers["Location"]
    page = raw(prof.get(r.headers["Location"]))
    assert ("Your list has more students named Sam Rivera than the new one, and nothing (no email) tells which one "
            "left, so we'll keep all of them as they are. Ask Sam Rivera which name they sign in with, then take the "
            "other off under “See or change the list”.") in html(prof.get(r.headers["Location"]))
    assert _buttons(page) == [("Update my list to match", True), ("Cancel — keep my current list", False)]


# ---------------------------------------------------------------------------
# Canvas's file has grades in it: the report says it can go (#29)
# ---------------------------------------------------------------------------
def _zipped(files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return out.getvalue()


GRADES = "Assignment Name,Due Date,Average Score,Points Possible\nEssay 1,2026-10-01,88,100\n"
TIP = "Canvas's file is still in your Downloads folder, and it has grades in it. You can delete it now."


def _send(prof, sid, files, box, **form):
    return prof.post(f"/teach/s/{sid}/roster", {"roster": files, "box": box, **form}, content_type="multipart/form-data")


def test_after_canvas_s_file_goes_in_the_report_says_to_delete_it(prof):
    sid = prof.create_sheet(allow_unlisted=False)
    data = _zipped({"course_grade.csv": GRADES, "students.csv": CANVAS_ROSTER})
    page = follow(prof, _send(prof, sid, (io.BytesIO(data), "course_analytics.zip"), "roster-file"))
    assert TIP + "</li>" in page  # (a .zip is still a .zip: no word about a folder)
    # The CSV from the folder Safari made, in the Canvas guide's box: and the folder too.
    b = prof.create_sheet(title="B", allow_unlisted=False)
    page = follow(prof, _send(prof, b, (io.BytesIO(CANVAS_ROSTER.encode()), "students.csv"), f"canvas-file-{b}"))
    assert TIP + " If Safari turned it into a folder, delete that whole folder." in page
    # Both files of that folder at once.
    c = prof.create_sheet(title="C", allow_unlisted=False)
    page = follow(prof, _send(prof, c, [(io.BytesIO(GRADES.encode()), "course_grade.csv"),
                                        (io.BytesIO(CANVAS_ROSTER.encode()), "students.csv")], "roster-file"))
    assert TIP in page
    # Any other file, or names pasted: no such word.
    d = prof.create_sheet(title="D", allow_unlisted=False)
    assert "Canvas's file is still" not in follow(prof, prof.upload(d))
    e = prof.create_sheet(title="E", allow_unlisted=False)
    page = follow(prof, prof.post(f"/teach/s/{e}/roster", {"pasted": "Ana Lima, ana@school.edu"}))
    assert "Added 1 student" in page and "Canvas's file is still" not in page


def test_the_word_about_canvas_s_file_comes_through_the_review_and_the_already_matches_note(prof):
    sid = prof.create_sheet(allow_unlisted=False)
    prof.upload(sid)
    page = follow(prof, _send(prof, sid, (io.BytesIO(CANVAS_ROSTER.encode()), "students.csv"), f"canvas-file-{sid}"))
    assert "already matches “students.csv”" in page
    assert "When you're done, you can delete Canvas's file from Downloads: it has grades in it." in page
    more = CANVAS_ROSTER.replace("Test Student", "Ana Lima,\"Lima, Ana\",105,90%,100,2026-10-01,2026-10-01,,1,1,"
                                                 "ana@school.edu,9005\nTest Student")
    r = _send(prof, sid, (io.BytesIO(more.encode()), "students.csv"), f"canvas-file-{sid}")
    assert TIP in html(prof.get(r.headers["Location"]))  # among the notes about the file
    assert TIP in follow(prof, prof.post(r.headers["Location"], {"action": "replace"}))


def test_the_word_about_canvas_s_file_survives_a_long_report(prof):
    sid = prof.create_sheet(allow_unlisted=False)
    students = [{"name_key": "ana lima", "display_name": "Ana Lima", "email": "ana@school.edu", "name_pending": 0}]
    tips = [f"Note {i}." for i in range(12)] + [TIP + " If Safari turned it into a folder, delete that whole folder."]
    with prof.app.test_request_context():
        teach._apply_roster(sheets.get_sheet(sid), students, "replace", "Added 1 student.", tips, False, "a file")
        shown = session["roster_report"]["tips"]
    assert len(shown) == teach.REPORT_TIPS and shown[-1].startswith(TIP) and shown[0] == "Note 0."


def test_the_wrong_file_from_canvas_s_box_gets_no_word_about_deleting_it(prof):
    sid = prof.create_sheet(allow_unlisted=False)
    r = _send(prof, sid, (io.BytesIO(NO_HEADINGS.encode()), "course_grade.csv"), f"canvas-file-{sid}")
    page = html(prof.get(r.headers["Location"]))
    assert "This file has no Name or Email column" in page and "Canvas's file is still" not in page


# ---------------------------------------------------------------------------
# Signing out gives a waiting Canvas permission back, however long it waited (#13)
# ---------------------------------------------------------------------------
def test_signing_out_gives_canvas_its_permission_back_after_ten_minutes(prof, monkeypatch):
    sid = prof.create_sheet(allow_unlisted=False)
    canvas, fake = _connectable_canvas(monkeypatch)
    _connect(prof, canvas, sid, "?pick=1")
    assert len(fake.TOKENS) == 1
    monkeypatch.setattr(canvas_oauth, "SEALED_FOR", -1)  # the course list left open past ten minutes
    prof.post("/teach/logout")
    assert not fake.TOKENS


# ---------------------------------------------------------------------------
# The school: known from the email for the schools Scheduler Helper reads (#21)
# ---------------------------------------------------------------------------
def test_a_professor_at_a_school_the_helper_reads_is_not_asked_which_school(browser, monkeypatch):
    asked = []
    monkeypatch.setattr(settings, "CANVAS_DEV_HOST", "")
    monkeypatch.setattr(canvasdir, "_ask", lambda **p: asked.append(p))  # (the directory is down, too)
    prof = browser().sign_in_instructor("prof@law.northwestern.edu")
    sid = prof.create_sheet(allow_unlisted=False)
    page = raw(prof.get(f"/teach/s/{sid}"))
    start = page.split('data-step="start"')[1].split("</section>")[0]
    assert 'data-go="canvas"' in start and 'data-go="school"' not in start
    assert 'data-canvas-host="canvas.northwestern.edu"' in page and "Northwestern University" in page
    assert not asked and q(prof.app, "SELECT canvas_host FROM instructors WHERE email = :e",
                           e="prof@law.northwestern.edu") is None  # nothing saved: as good as chosen
    # Anywhere else, the question as before.
    other = browser().sign_in_instructor("prof@school.edu")
    sid = other.create_sheet(allow_unlisted=False)
    assert 'data-go="school"' in raw(other.get(f"/teach/s/{sid}"))
    # A look-alike domain isn't the school.
    third = browser().sign_in_instructor("prof@notnorthwestern.edu")
    assert 'data-go="school"' in raw(third.get(f"/teach/s/{third.create_sheet(allow_unlisted=False)}"))


def test_the_helper_s_schools_and_its_canvas_sites_are_one_list():
    assert settings.CANVAS_HELPER_HOSTS == [host for _name, host in settings.CANVAS_HELPER_SCHOOLS.values()]


def test_a_directory_that_didnt_answer_is_asked_again_in_minutes(app, monkeypatch):
    def remembered(domain):
        saved = json.loads(db.scalar("SELECT value FROM app_state WHERE key = :k", k="canvas-school:" + domain))
        return parse_iso(saved["until"]) - now()

    with app.app_context():
        monkeypatch.setattr(canvasdir, "_ask", lambda **p: None)
        assert canvasdir.guess("a@law.down.edu") is None
        assert remembered("law.down.edu") <= timedelta(minutes=canvasdir.RETRY_MINUTES)
        # law.x.edu answered, x.edu didn't: not "no school" for a month either.
        monkeypatch.setattr(canvasdir, "_ask", lambda domain, **p: [] if domain == "law.half.edu" else None)
        assert canvasdir.guess("a@law.half.edu") is None
        assert remembered("law.half.edu") <= timedelta(minutes=canvasdir.RETRY_MINUTES)
        # An answer (even "no school") is kept for a month.
        monkeypatch.setattr(canvasdir, "_ask", lambda **p: [])
        assert canvasdir.guess("a@up.edu") is None
        assert remembered("up.edu") > timedelta(days=canvasdir.REMEMBER_DAYS - 1)


# ---------------------------------------------------------------------------
# What the Canvas questions start from: said yes before, and courses on other sheets (contract 3)
# ---------------------------------------------------------------------------
def _context(rendered, name):
    return next(context for template, context in reversed(rendered) if template == name)


def test_the_pages_know_whether_canvas_was_used_before_and_which_courses_have_sheets(prof, rendered):
    a = prof.create_sheet(title="LAW 540 Evidence", allow_unlisted=False)
    b = prof.create_sheet(title="Seminar on Privacy", allow_unlisted=False)
    prof.get(f"/teach/s/{b}/setup?step=students")
    assert _context(rendered, "teach_setup.html")["canvas_before"] is False
    assert _context(rendered, "teach_setup.html")["used_courses"] == {}
    prof.post(f"/teach/s/{a}/canvas-list", {"list": _canvas_list(n=3, course_id="202", course="2026FA_LAW_540_SEC20")})
    prof.get(f"/teach/s/{b}/setup?step=students")
    setup = _context(rendered, "teach_setup.html")
    assert setup["canvas_before"] is True  # the helper's list saved the school
    assert setup["used_courses"] == {"202": "LAW 540 Evidence"}
    prof.get(f"/teach/s/{b}")
    assert _context(rendered, "sheet_tab_class.html")["used_courses"] == {"202": "LAW 540 Evidence"}
    assert _context(rendered, "sheet_tab_class.html")["canvas_before"] is True
    prof.get(f"/teach/s/{a}")
    assert _context(rendered, "sheet_tab_class.html")["used_courses"] == {}  # not its own course
    # Two sheets with one course: the one changed last.
    c = prof.create_sheet(title="LAW 540 again", allow_unlisted=False)
    prof.post(f"/teach/s/{c}/canvas-list", {"list": _canvas_list(n=3, course_id="202", course="2026FA_LAW_540_SEC20")})
    with prof.app.app_context():
        db.run("UPDATE sheets SET updated_at = :at WHERE id = :s", at="2000-01-01T00:00:00+00:00", s=a)
        db.commit()
    prof.get(f"/teach/s/{b}")
    assert _context(rendered, "sheet_tab_class.html")["used_courses"] == {"202": "LAW 540 again"}


def test_another_professor_s_courses_are_never_listed(prof, browser, rendered):
    a = prof.create_sheet(title="LAW 540 Evidence", allow_unlisted=False)
    prof.post(f"/teach/s/{a}/canvas-list", {"list": _canvas_list(n=3, course_id="202")})
    other = browser().sign_in_instructor("other@school.edu")
    sid = other.create_sheet(allow_unlisted=False)
    other.get(f"/teach/s/{sid}")
    assert _context(rendered, "sheet_tab_class.html")["used_courses"] == {}
    assert _context(rendered, "sheet_tab_class.html")["canvas_before"] is False
