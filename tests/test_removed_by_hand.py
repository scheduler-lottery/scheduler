"""Students an instructor took off the class list by hand, who are still in
Canvas: the next "Update from Canvas" leaves them off and says so, rather
than calling them new (from walking the weekly update through as a
professor)."""

import json

from test_app import follow, q, text


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
