"""Students an instructor took off the class list by hand, who are still in
Canvas: the next "Update from Canvas" leaves them off and says so, rather
than calling them new (from walking the weekly update through as a
professor)."""

import json
import re

from test_app import follow, q, raw, text


def canvas(names, course="2026FA_LAW_540_SEC20"):
    """A class list as Scheduler Helper brings it."""
    return json.dumps({"type": "scheduler-class-list", "v": 2, "courseId": "202", "host": "canvas.school.edu",
                       "course": course, "students": [{"name": n, "email": n.lower().replace(" ", ".") + "@u.edu"}
                                                      for n in names]})


CLASS = ["Ana Lima", "Bo Chen", "Cy Diaz", "Di Eng", "Ed Fox"]


def on_list(prof, sid):
    return {r for (r,) in _rows(prof, "SELECT display_name FROM roster WHERE sheet_id = :sid AND is_test = 0", sid)}


def _rows(prof, sql, sid):
    import db

    with prof.app.app_context():
        return [tuple(r.values()) for r in db.rows(sql, sid=sid)]


def update(prof, sid, names):
    return prof.post(f"/teach/s/{sid}/canvas-list", {"list": canvas(names), "how": "helper"})


def started(prof):
    sid = prof.create_sheet(allow_unlisted=False)
    update(prof, sid, CLASS)
    prof.post(f"/teach/s/{sid}/roster/report/dismiss")
    prof.post(f"/teach/s/{sid}/roster/remove", {"name_key": "bo chen"})
    assert "Bo Chen" not in on_list(prof, sid)
    return sid


def test_a_student_taken_off_by_hand_stays_off_and_the_update_says_so(prof):
    sid = started(prof)
    page = follow(prof, update(prof, sid, CLASS))
    assert "already matches 2026FA_LAW_540_SEC20 in Canvas" in page
    assert "Bo Chen is still in Canvas, but you took them off your list, so they stay off. Nothing changed." in page
    assert "Bo Chen" not in on_list(prof, sid)
    # Every week, the same one press.
    assert "/roster/review/" not in update(prof, sid, CLASS).headers["Location"]


def test_with_a_newcomer_too_the_button_adds_only_the_newcomer(prof):
    sid = started(prof)
    r = update(prof, sid, CLASS + ["Flo Gray"])
    assert "/roster/review/" in r.headers["Location"]
    page = text(prof.get(r.headers["Location"]))
    assert "1 new: Flo Gray" in page
    assert "Still in Canvas, but you took them off your list: Bo Chen" in page and "They stay off." in page
    assert "Add Flo Gray" in page and "Add Bo Chen" not in page and "Put Bo Chen back on my list too" in page
    prof.post(r.headers["Location"], {"action": "replace"})
    assert on_list(prof, sid) == set(CLASS + ["Flo Gray"]) - {"Bo Chen"}
    # Still off, and still said, next time.
    assert "Bo Chen is still in Canvas" in follow(prof, update(prof, sid, CLASS + ["Flo Gray"]))


def test_ticking_put_back_adds_them_too_and_forgets_they_were_taken_off(prof):
    sid = started(prof)
    r = update(prof, sid, CLASS + ["Flo Gray"])
    prof.post(r.headers["Location"], {"action": "replace", "put_back": "1"})
    assert on_list(prof, sid) == set(CLASS + ["Flo Gray"])
    assert q(prof.app, "SELECT COUNT(*) FROM removed_by_hand WHERE sheet_id = :sid", sid=sid) == 0


def test_added_back_by_hand_or_by_undo_and_they_count_as_on_the_list_again(prof):
    sid = started(prof)
    prof.post(f"/teach/s/{sid}/roster/quick-add", {"people": "Bo Chen <bo.chen@u.edu>"})
    assert "Bo Chen" in on_list(prof, sid)
    page = follow(prof, update(prof, sid, CLASS))
    assert "nobody new and nobody gone. Nothing changed." in page and "still in Canvas" not in page
    assert q(prof.app, "SELECT COUNT(*) FROM removed_by_hand WHERE sheet_id = :sid", sid=sid) == 0


def test_once_canvas_drops_them_a_later_return_is_new(prof):
    sid = started(prof)
    others = [n for n in CLASS if n != "Bo Chen"]
    assert "Nothing changed" in follow(prof, update(prof, sid, others))  # Canvas caught up
    assert q(prof.app, "SELECT COUNT(*) FROM removed_by_hand WHERE sheet_id = :sid", sid=sid) == 0
    r = update(prof, sid, CLASS)  # enrolled again
    assert "1 new: Bo Chen" in text(prof.get(r.headers["Location"]))


def test_names_pasted_in_are_meant_and_starting_over_forgets(prof):
    sid = started(prof)
    r = prof.post(f"/teach/s/{sid}/roster", {"pasted": "Bo Chen, bo.chen@u.edu\nFlo Gray, flo.gray@u.edu"})
    page = text(prof.get(r.headers["Location"]))
    assert "2 new: Bo Chen" in page and "took them off" not in page
    prof.post(r.headers["Location"], {"action": "add"})
    assert {"Bo Chen", "Flo Gray"} <= on_list(prof, sid)
    prof.post(f"/teach/s/{sid}/roster/remove", {"name_key": "flo gray"})
    prof.post(f"/teach/s/{sid}/roster/clear")
    # (kept while the list is off, for an undo of that; a first list again starts afresh)
    assert q(prof.app, "SELECT COUNT(*) FROM removed_by_hand WHERE sheet_id = :sid", sid=sid) == 1
    update(prof, sid, CLASS)
    assert q(prof.app, "SELECT COUNT(*) FROM removed_by_hand WHERE sheet_id = :sid", sid=sid) == 0


def test_a_report_goes_once_the_list_is_changed_by_hand_and_an_undo_lists_nobody_as_added(prof):
    sid = prof.create_sheet(allow_unlisted=False)
    update(prof, sid, CLASS)
    prof.post(f"/teach/s/{sid}/roster/report/dismiss")
    r = update(prof, sid, CLASS + ["Flo Gray"])
    prof.post(r.headers["Location"], {"action": "replace"})
    page = text(prof.get(f"/teach/s/{sid}"))
    assert "Who was added (1)" in page
    prof.post(f"/teach/s/{sid}/roster/quick-add", {"people": "Pat Visitor <pat@u.edu>"})
    assert "Who was added" not in text(prof.get(f"/teach/s/{sid}"))
    # "Put the previous one back": the list is back, and nobody is listed as added.
    r = update(prof, sid, CLASS + ["Flo Gray", "Gus Hall"])
    prof.post(r.headers["Location"], {"action": "replace"})
    undo = q(prof.app, "SELECT id FROM snapshots WHERE sheet_id = :sid ORDER BY created_at DESC LIMIT 1", sid=sid)
    prof.post(f"/teach/s/{sid}/undo/{undo}", {"parts": "roster"})
    page = text(prof.get(f"/teach/s/{sid}"))
    assert "Your previous class list is back" in page and "Who was added" not in page


# ---------------------------------------------------------------------------
# From the Chrome test round: renamed in Canvas, unchanged updates, old reports
# ---------------------------------------------------------------------------
def test_a_student_renamed_in_canvas_keeps_their_ranking_and_is_never_doubled(prof, browser):
    from conftest import day_keys

    sid = started(prof)
    keys = day_keys(sid)
    cy = browser().sign_in_student(sid, "Cy Diaz", email="cy.diaz@u.edu")
    assert cy.rank(sid, keys).status_code == 200
    renamed = [n if n != "Cy Diaz" else "Cy Diaz-Okafor" for n in CLASS]
    listed = json.loads(canvas(renamed))
    for p in listed["students"]:
        if p["name"] == "Cy Diaz-Okafor":
            p["email"] = "cy.diaz@u.edu"  # (Canvas's new name, the same email)
    r = prof.post(f"/teach/s/{sid}/canvas-list", {"list": json.dumps(listed), "how": "helper"})
    assert "/roster/review/" in r.headers["Location"]
    page = text(prof.get(r.headers["Location"]))
    assert "the same email, so the same student" in page and "Use the new name from Canvas" in page
    assert "would come off" not in page and "new: Cy" not in page
    prof.post(r.headers["Location"], {"action": "replace"})
    rows = _rows(prof, "SELECT display_name, email FROM roster WHERE sheet_id = :sid AND is_test = 0", sid)
    assert ("Cy Diaz-Okafor", "cy.diaz@u.edu") in rows and not any(name == "Cy Diaz" for name, _ in rows)
    assert len([1 for name, _ in rows if name.startswith("Cy")]) == 1
    assert q(prof.app, "SELECT name_key FROM submissions WHERE sheet_id = :sid", sid=sid) == "cy diaz okafor"
    report = text(prof.get(f"/teach/s/{sid}"))
    assert "Cy Diaz is now “Cy Diaz-Okafor”" in report and "Who was added" not in report
    # The highlighted "Only add" (another change pending too) never makes a second, emailless copy either.
    assert "already matches" in follow(prof, prof.post(f"/teach/s/{sid}/canvas-list",
                                                       {"list": json.dumps(listed), "how": "helper"}))


def test_an_update_that_changes_nothing_replaces_last_weeks_report_and_offers_removal(prof):
    sid = prof.create_sheet(allow_unlisted=False)
    update(prof, sid, CLASS)
    r = update(prof, sid, CLASS + ["Flo Gray"])
    prof.post(r.headers["Location"], {"action": "replace"})
    assert "Wrong list? Put the previous one back" in text(prof.get(f"/teach/s/{sid}"))  # last week's report
    update(prof, sid, CLASS + ["Flo Gray"])  # this week: nothing changed
    page = text(prof.get(f"/teach/s/{sid}"))
    assert "already matches" in page and "Nothing changed" in page
    assert "Wrong list? Put the previous one back" not in page and "Who was added" not in page
    assert "data-helper-remove" in raw(prof.get(f"/teach/s/{sid}"))
    # A list from Canvas's file that changes nothing: just said, nothing to remove.
    other = prof.create_sheet(allow_unlisted=False)
    prof.upload(other, "Name,Email\nAna Lima,ana@u.edu\n")
    prof.post(f"/teach/s/{other}/roster/report/dismiss")
    page = follow(prof, prof.upload(other, "Name,Email\nAna Lima,ana@u.edu\n"))
    assert "already matches" in page and "data-helper-remove" not in raw(prof.get(f"/teach/s/{other}"))


def test_cancel_on_a_helper_review_offers_removal_and_an_old_report_fades(prof, monkeypatch):
    import time as clock

    sid = started(prof)
    r = update(prof, sid, CLASS + ["Flo Gray"])
    prof.post(r.headers["Location"], {"action": "cancel"})
    page = raw(prof.get(f"/teach/s/{sid}"))
    assert "Kept your current class list. Nothing changed." in text(page) and "data-helper-remove" in page
    # Half a day later, the report (and its undo) is gone from the class page.
    later = clock.time() + 13 * 3600
    monkeypatch.setattr(clock, "time", lambda: later)
    assert "Kept your current class list" not in text(prof.get(f"/teach/s/{sid}"))


def test_taken_off_by_hand_records_survive_an_undo_and_a_list_without_emails(prof):
    sid = started(prof)  # Bo Chen taken off by hand
    prof.post(f"/teach/s/{sid}/roster/clear")
    snap = q(prof.app, "SELECT id FROM snapshots WHERE sheet_id = :sid ORDER BY created_at DESC LIMIT 1", sid=sid)
    prof.post(f"/teach/s/{sid}/undo/{snap}", {"parts": "roster"})
    assert "Bo Chen is still in Canvas" in follow(prof, update(prof, sid, CLASS))
    # Canvas listing Bo with no email this time: still Bo, still off.
    bare = json.loads(canvas(CLASS))
    for p in bare["students"]:
        if p["name"] == "Bo Chen":
            p["email"] = ""
    prof.post(f"/teach/s/{sid}/canvas-list", {"list": json.dumps(bare), "how": "helper"})
    assert q(prof.app, "SELECT COUNT(*) FROM removed_by_hand WHERE sheet_id = :sid", sid=sid) == 1


def test_a_file_of_late_adds_doesnt_forget_who_was_taken_off(prof):
    sid = started(prof)
    r = prof.upload(sid, "Name,Email\nFlo Gray,flo.gray@u.edu\n", "late-adds.csv")
    prof.post(r.headers["Location"], {"action": "add"})
    assert "Flo Gray" in on_list(prof, sid)
    assert q(prof.app, "SELECT COUNT(*) FROM removed_by_hand WHERE sheet_id = :sid", sid=sid) == 1


def test_a_link_opened_with_at_canvas_lands_on_the_canvas_step(prof):
    sid = prof.create_sheet(allow_unlisted=False)
    assert 'data-first="canvas"' in raw(prof.get(f"/teach/s/{sid}/setup?step=students&at=canvas"))
    update(prof, sid, CLASS)
    assert re.search(r'<details[^>]*id="canvas-update"[^>]*open', raw(prof.get(f"/teach/s/{sid}?at=canvas")))


def test_reports_name_a_canvas_course_plainly_and_say_who_was_taken_off(prof):
    sid = prof.create_sheet(allow_unlisted=False)
    update(prof, sid, CLASS)
    assert "Added 5 students from 2026FA_LAW_540_SEC20 in Canvas" in text(prof.get(f"/teach/s/{sid}"))
    r = update(prof, sid, CLASS[:-1])
    assert "2026FA_LAW_540_SEC20 in Canvas: 4 students" in text(prof.get(r.headers["Location"]))
    prof.post(r.headers["Location"], {"action": "replace"})
    assert "Updated your list from 2026FA_LAW_540_SEC20 in Canvas: took Ed Fox off." in text(prof.get(f"/teach/s/{sid}"))
