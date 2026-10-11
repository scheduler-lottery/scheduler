"""The Canvas step as a professor reads it: words for their kind of computer,
pages that say what really happens, and warnings only when something is
really wrong (from walking it through as professors who don't know tech)."""

import re

import settings
from test_app import _connectable_canvas, follow, raw, text


def test_a_section_named_in_words_or_by_the_same_number_is_no_warning(prof):
    analytics = "Student Name,Student ID,Email,Section Name\n"
    rows = "Ana Lima,1,ana@school.edu,{0}\nBo Chen,2,bo@school.edu,{0}\n"
    # A title in words: nothing to compare Canvas's section code with.
    sid = prof.create_sheet(title="Evidence Seminar")
    page = follow(prof, prof.upload(sid, analytics + rows.format("LAW 540 SEC20")))
    assert "Added 2 students" in page and "report-warn" not in page
    # The same course number, written Canvas's way.
    sid = prof.create_sheet(title="LAW 540 Evidence")
    page = follow(prof, prof.upload(sid, analytics + rows.format("2026FA_LAW_540-0_SEC20")))
    assert "report-warn" not in page
    # A different course number: the wrong course's list, likely.
    sid = prof.create_sheet(title="LAW 540 Evidence")
    page = follow(prof, prof.upload(sid, analytics + "Ana Lima,1,ana@school.edu,LAW 610 SEC1\n"
                                                     "Bo Chen,2,bo@school.edu,LAW 610 SEC2\n"))
    assert "report-warn" in page and "sections “LAW 610 SEC1”, “LAW 610 SEC2”" in page
    assert "Every row" not in page


def test_the_canvas_step_has_words_for_each_kind_of_computer(prof):
    sid = prof.create_sheet(allow_unlisted=False)
    page = raw(prof.get(f"/teach/s/{sid}/setup?step=students"))
    for marker in ("data-phone-only", "data-desktop-only", "data-safari-only"):
        assert marker in page
    # The box says which box it is, so a wrong file comes back to it.
    assert f'name="box" value="canvas-file-{sid}"' in page
    assert f'aria-labelledby="canvas-file-{sid}-title"' in page
    words = text(page)
    assert "its name ends in .zip" in words and "Downloads" in words
    assert "Drag in" not in words and "Drag in" not in text(prof.get("/"))
    # The grades in Canvas's file: said before the file is chosen, not after.
    assert words.index("It also has grades in it") < words.index("Click here to choose Canvas's file")


def test_the_pages_about_canvas_say_what_happens_on_this_site(prof, monkeypatch):
    privacy = text(prof.get("/privacy"))
    assert "comes to this site as soon as the instructor chooses it" in privacy
    assert "Scheduler Helper" not in privacy and "Connect Canvas" not in privacy  # neither is offered here yet
    assert "isn't offered on this site yet" in text(prof.get("/helper"))
    monkeypatch.setattr(settings, "CANVAS_HELPER_IDS", ["abcdefghijklmnopabcdefghijklmnop"])
    assert "Scheduler Helper" in text(prof.get("/privacy"))
    assert "isn't offered on this site yet" not in text(prof.get("/helper"))


def test_connect_canvas_shows_only_for_the_school_whose_key_this_site_has(prof, monkeypatch):
    _connectable_canvas(monkeypatch)
    sid = prof.create_sheet(allow_unlisted=False)
    page = raw(prof.get(f"/teach/s/{sid}/setup?step=students"))
    assert 'data-connect-host="canvas.test"' in page
    assert re.search(r"<section[^>]*data-connect[^>]*>", page) and "Authorize" in text(page)
    assert "Canvas says that for every app" in text(page)  # what Authorize means, before Canvas asks
    r = prof.post_json("/teach/canvas/school", {"domain": "canvas.other.edu", "name": "Other University"})
    assert r.status_code == 200, raw(r)
    page = raw(prof.get(f"/teach/s/{sid}/setup?step=students"))
    section = re.search(r"<section[^>]*data-connect[^>]*>", page).group(0)
    assert " hidden" in section  # another school's professor isn't sent to this one's Canvas


def test_connect_canvas_brings_the_course_the_sheet_is_named_for(prof, monkeypatch):
    from test_app import _authorize, q

    canvas, fake = _connectable_canvas(monkeypatch)
    sid = prof.create_sheet(title="LAW 540 Evidence", allow_unlisted=False)
    back = _authorize(canvas, prof.get(f"/teach/s/{sid}/canvas/connect").headers["Location"])
    r = prof.get(back.path + "?" + back.query)
    assert r.headers["Location"].endswith(f"/teach/s/{sid}#class-list")  # no course to pick: straight to the list
    assert q(prof.app, "SELECT COUNT(*) FROM roster WHERE sheet_id = :sid", sid=sid) == 8
    assert q(prof.app, "SELECT canvas_course_id FROM sheets WHERE id = :sid", sid=sid) == "202"
    assert not fake.TOKENS  # the permission given back
    # A name that doesn't say which course: the list of courses, to pick from.
    other = prof.create_sheet(title="Seminar on Privacy", allow_unlisted=False)
    back = _authorize(canvas, prof.get(f"/teach/s/{other}/canvas/connect").headers["Location"])
    assert prof.get(back.path + "?" + back.query).headers["Location"].endswith("/teach/canvas/oauth/pick")


def test_a_list_from_canvas_without_emails_is_not_taken_for_the_wrong_file(prof):
    import json

    sid = prof.create_sheet(allow_unlisted=False)
    prof.upload(sid, "Name,Email\nAna Lima,ana@school.edu\n")  # a list already there: the next one is looked over
    listed = json.dumps({"type": "scheduler-class-list", "v": 2, "courseId": "77", "course": "LAW 540",
                         "students": [{"name": "Ana Lima", "email": ""}, {"name": "Bo Chen", "email": ""}]})
    r = prof.post(f"/teach/s/{sid}/canvas-list", {"list": listed, "how": "helper"})
    page = text(prof.get(r.headers["Location"]))
    assert "no Name or Email column" not in page
