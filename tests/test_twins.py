"""Students who share a name ("Sam Rivera", "Sam Rivera (2)") when a new
class list comes in: each keeps their own name, and with it their PIN, email
and ranking (sheets.carry_over). Through the real pages: the upload, the
review page, Replace, then the student signing in."""

import json

from conftest import day_keys
from test_app import db_rows, follow, html, q, text

import sheets
from util import normalize_name

HEAD = "Student Name,Student ID,Email\n"
OTHERS = "Maya Patel,1004,mp@u.school.edu\nJo Park,1007,jp@u.school.edu\n"
LATE = "Li Wu,1010,lw@u.school.edu\n"  # someone new too, so the new list always goes to the review page


def roster(app, sid):
    return {r["name_key"]: r["email"] for r in db_rows(
        app, "SELECT name_key, email FROM roster WHERE sheet_id = :sid AND is_test = 0", sid=sid)}


def ranking(app, sid, key):
    found = q(app, "SELECT ranking FROM submissions WHERE sheet_id = :sid AND name_key = :key", sid=sid, key=key)
    return json.loads(found) if isinstance(found, str) else found


def gets_in_with_pin(browser, sid, name, pin):
    b = browser()
    b.post(f"/c/{sid}/login", {"name": name})
    return b.post(f"/c/{sid}/pin", {"mode": "enter", "pin": pin}).headers.get("Location", "").endswith("/home")


def sign_in_twin(browser, sid, key, email):
    """A student who shares a name with others who have emails: types it, picks their own email."""
    b = browser()
    b.post(f"/c/{sid}/login", {"name": key, "pick": key, "which": "1"})
    assert b.outbox[-1].to == [email]
    assert b.post(f"/c/{sid}/verify", {"code": b.last_code(email)}).headers["Location"].endswith("/home")
    return b


def review(prof, r, action="replace"):
    """The review page a new list goes to (its text), then its button."""
    assert "/roster/review/" in r.headers["Location"], r.headers["Location"]
    page = text(prof.get(r.headers["Location"]))
    prof.post(r.headers["Location"], {"action": action})
    return page


def helper_list(names):
    """A class list as Scheduler Helper brings it, from a school that hides emails."""
    return json.dumps({"type": "scheduler-class-list", "v": 2, "courseId": "4242", "host": "canvas.school.edu",
                       "course": "LAW 540", "students": [{"name": n} for n in names]})


# ---------------------------------------------------------------------------
# Through the pages
# ---------------------------------------------------------------------------
def test_a_no_email_twin_drops_and_the_other_still_gets_in_with_their_own_pin(prof, browser):
    sid = prof.create_sheet(allow_unlisted=False)
    prof.upload(sid, HEAD + "Sam Rivera,1005,\nSam Rivera,1006,\n" + OTHERS, "students.csv")
    keys = day_keys(sid)
    browser().sign_in_student(sid, "Sam Rivera", pin="5927").rank(sid, keys[::-1])  # #1005
    browser().sign_in_student(sid, "Sam Rivera (2)", pin="4831").rank(sid, keys)  # #1006
    # #1005 drops the course. Nothing on the new list says which Sam Rivera is left.
    page = review(prof, prof.upload(sid, HEAD + "Sam Rivera,1006,\n" + OTHERS + LATE, "students.csv"))
    assert "would come off" not in page and "0 on your list but not in this one" in page  # names neither twin
    assert "3 already on your list" in page and "1 new: Li Wu" in page
    # Both stay just as they were, for the instructor to sort out: nobody is handed the other's name.
    assert roster(prof.app, sid) == {"sam rivera": "", "sam rivera 2": "", "maya patel": "mp@u.school.edu",
                                     "jo park": "jp@u.school.edu", "li wu": "lw@u.school.edu"}
    assert ranking(prof.app, sid, "sam rivera") == keys[::-1] and ranking(prof.app, sid, "sam rivera 2") == keys
    assert gets_in_with_pin(browser, sid, "Sam Rivera (2)", "4831")  # #1006, with their own PIN
    assert not gets_in_with_pin(browser, sid, "Sam Rivera", "4831")  # and not as the other one
    assert gets_in_with_pin(browser, sid, "Sam Rivera", "5927")


def test_the_email_twin_leaving_never_gives_their_email_to_the_pin_twin(prof, browser):
    sid = prof.create_sheet(allow_unlisted=False)
    prof.upload(sid, HEAD + "Sam Rivera,1005,\nSam Rivera,1006,srivera2@u.school.edu\n" + OTHERS, "students.csv")
    assert roster(prof.app, sid)["sam rivera"] == "srivera2@u.school.edu"  # the one with an email comes first
    keys = day_keys(sid)
    browser().sign_in_student(sid, "Sam Rivera (2)", pin="4831").rank(sid, keys)  # #1005, with no email
    # #1006 (srivera2@) drops; the one left has no email on the new list.
    page = review(prof, prof.upload(sid, HEAD + "Sam Rivera,1005,\n" + OTHERS + LATE, "students.csv"))
    assert "would come off" not in page
    rows = roster(prof.app, sid)
    assert rows["sam rivera 2"] == "" and rows["sam rivera"] == "srivera2@u.school.edu"  # each kept their own
    assert gets_in_with_pin(browser, sid, "Sam Rivera (2)", "4831")
    assert ranking(prof.app, sid, "sam rivera 2") == keys
    # Signing in as "Sam Rivera" still sends the code to that student's own address, not #1005's name.
    b = browser()
    assert b.post(f"/c/{sid}/login", {"name": "Sam Rivera"}).headers["Location"].endswith("/verify")
    assert b.outbox[-1].to == ["srivera2@u.school.edu"]


def test_a_pin_twin_whose_email_now_shows_keeps_their_name_and_ranking(prof, browser):
    sid = prof.create_sheet(allow_unlisted=False)
    prof.upload(sid, HEAD + "Sam Rivera,1005,\nSam Rivera,1006,zz@u.school.edu\n" + OTHERS, "students.csv")
    keys = day_keys(sid)
    browser().sign_in_student(sid, "Sam Rivera", "zz@u.school.edu").rank(sid, keys[::-1])  # #1006
    browser().sign_in_student(sid, "Sam Rivera (2)", pin="4831").rank(sid, keys)  # #1005
    # Canvas now lists #1005's address, which sorts before #1006's, so the new list numbers them the other way.
    page = review(prof, prof.upload(sid, HEAD + "Sam Rivera,1005,aa@u.school.edu\nSam Rivera,1006,zz@u.school.edu\n"
                                    + OTHERS, "students.csv"))
    assert "0 new" in page and "New email address for: Sam Rivera (2)" in page
    rows = roster(prof.app, sid)
    assert rows["sam rivera"] == "zz@u.school.edu" and rows["sam rivera 2"] == "aa@u.school.edu"
    assert ranking(prof.app, sid, "sam rivera") == keys[::-1] and ranking(prof.app, sid, "sam rivera 2") == keys
    # #1005 now signs in with a code sent to their own address, to their own ranking.
    s = browser()
    assert "Which one is you?" in text(s.post(f"/c/{sid}/login", {"name": "Sam Rivera"}))
    s.post(f"/c/{sid}/login", {"name": "Sam Rivera", "pick": "sam rivera 2", "which": "1"})
    assert s.outbox[-1].to == ["aa@u.school.edu"]
    assert s.post(f"/c/{sid}/verify", {"code": s.last_code("aa@u.school.edu")}).headers["Location"].endswith("/home")
    assert "Hi Sam Rivera (2)" in text(s.get(f"/c/{sid}/home"))


def test_through_scheduler_helper_a_twin_dropping_leaves_the_other_their_pin(prof, browser):
    sid = prof.create_sheet(allow_unlisted=False)
    review(prof, prof.post(f"/teach/s/{sid}/canvas-list",
                           {"list": helper_list(["Sam Rivera", "Sam Rivera", "Maya Patel", "Jo Park"])}))
    assert sorted(roster(prof.app, sid)) == ["jo park", "maya patel", "sam rivera", "sam rivera 2"]
    keys = day_keys(sid)
    browser().sign_in_student(sid, "Sam Rivera", pin="5927").rank(sid, keys[::-1])
    browser().sign_in_student(sid, "Sam Rivera (2)", pin="4831").rank(sid, keys)
    # The first Sam Rivera (by Canvas's order) drops; a newcomer joins.
    r = prof.post(f"/teach/s/{sid}/canvas-list",
                  {"list": helper_list(["Sam Rivera", "Maya Patel", "Jo Park", "Li Wu"])})
    assert "would come off" not in review(prof, r)
    assert gets_in_with_pin(browser, sid, "Sam Rivera (2)", "4831")
    assert gets_in_with_pin(browser, sid, "Sam Rivera", "5927")
    assert ranking(prof.app, sid, "sam rivera 2") == keys


def test_twins_listed_the_same_keep_the_emails_typed_in_for_them(prof):
    """A school that hides emails; the instructor typed in both twins'. Next week's list (the same two)
    keeps each where it was."""
    sid = prof.create_sheet(allow_unlisted=False)
    prof.upload(sid, HEAD + "Sam Rivera,1005,\nSam Rivera,1006,\n" + OTHERS, "students.csv")
    for key, name, email in (("sam rivera", "Sam Rivera", "a5@u.school.edu"),
                             ("sam rivera 2", "Sam Rivera (2)", "b6@u.school.edu")):
        prof.post(f"/teach/s/{sid}/roster/edit", {"name_key": key, "name": name, "email": email})
    assert roster(prof.app, sid)["sam rivera 2"] == "b6@u.school.edu"
    review(prof, prof.upload(sid, HEAD + "Sam Rivera,1005,\nSam Rivera,1006,\nMaya Patel,1004,mp@u.school.edu\n",
                             "students.csv"))
    rows = roster(prof.app, sid)
    assert rows["sam rivera"] == "a5@u.school.edu" and rows["sam rivera 2"] == "b6@u.school.edu"
    assert "jo park" not in rows  # the list was replaced


def test_a_newcomer_with_the_same_name_never_takes_the_first_ones_name_ranking_or_note(prof, browser):
    sid = prof.create_sheet(allow_unlisted=False)
    prof.upload(sid, "Name,Email\nAlex Kim,zz@u.school.edu\nMaya Patel,mp@u.school.edu\nJo Park,jp@u.school.edu\n")
    keys = day_keys(sid)
    first = browser().sign_in_student(sid, "Alex Kim", "zz@u.school.edu")
    first.rank(sid, keys, comments={keys[0]: "my private note"})
    # A second Alex Kim enrolls, with an email that sorts first: the new list calls them plain "Alex Kim".
    page = review(prof, prof.upload(sid, "Name,Email\nAlex Kim,zz@u.school.edu\nAlex Kim,aa@u.school.edu\n"
                                         "Maya Patel,mp@u.school.edu\nJo Park,jp@u.school.edu\n"))
    assert "1 new: Alex Kim (2)" in page and "New email address" not in page
    assert roster(prof.app, sid) == {"alex kim": "zz@u.school.edu", "alex kim 2": "aa@u.school.edu",
                                     "maya patel": "mp@u.school.edu", "jo park": "jp@u.school.edu"}
    assert ranking(prof.app, sid, "alex kim") == keys
    newcomer = browser()
    newcomer.post(f"/c/{sid}/login", {"name": "Alex Kim", "pick": "alex kim 2", "which": "1"})
    assert newcomer.outbox[-1].to == ["aa@u.school.edu"]
    newcomer.post(f"/c/{sid}/verify", {"code": newcomer.last_code("aa@u.school.edu")})
    assert "Hi Alex Kim (2)" in text(newcomer.get(f"/c/{sid}/home"))
    assert "my private note" not in html(newcomer.get(f"/c/{sid}/rank"))
    # Nothing about the first Alex Kim changed: still signed in, with their own ranking and note.
    assert "my private note" in html(first.get(f"/c/{sid}/rank"))


def test_a_third_student_of_one_name_is_numbered_after_the_two_already_listed(prof, browser):
    sid = prof.create_sheet(allow_unlisted=False)
    prof.upload(sid, "Name,Email\nAlex Kim,bb@u.school.edu\nAlex Kim,zz@u.school.edu\nSam Lee,sl@u.school.edu\n")
    sign_in_twin(browser, sid, "alex kim 2", "zz@u.school.edu").rank(sid, day_keys(sid))
    review(prof, prof.upload(sid, "Name,Email\nAlex Kim,bb@u.school.edu\nAlex Kim,zz@u.school.edu\n"
                                  "Alex Kim,aa@u.school.edu\nSam Lee,sl@u.school.edu\n"))
    assert roster(prof.app, sid) == {"alex kim": "bb@u.school.edu", "alex kim 2": "zz@u.school.edu",
                                     "alex kim 3": "aa@u.school.edu", "sam lee": "sl@u.school.edu"}
    assert q(prof.app, "SELECT name_key FROM submissions WHERE sheet_id = :sid", sid=sid) == "alex kim 2"


def test_adding_a_list_that_now_shows_a_pin_twins_email_fills_it_in(prof, browser):
    sid = prof.create_sheet(allow_unlisted=False)
    prof.upload(sid, HEAD + "Sam Rivera,1005,\nSam Rivera,1006,zz@u.school.edu\n" + OTHERS, "students.csv")
    keys = day_keys(sid)
    browser().sign_in_student(sid, "Sam Rivera (2)", pin="4831").rank(sid, keys)  # #1005, with no email
    # The whole class again, now with #1005's address (sorting first), and a newcomer: Add, not Replace.
    review(prof, prof.upload(sid, HEAD + "Sam Rivera,1005,aa@u.school.edu\nSam Rivera,1006,zz@u.school.edu\n"
                                  + OTHERS + LATE, "students.csv"), action="add")
    rows = roster(prof.app, sid)
    assert rows["sam rivera"] == "zz@u.school.edu" and rows["sam rivera 2"] == "aa@u.school.edu"
    assert "sam rivera 3" not in rows and rows["li wu"] == "lw@u.school.edu"
    assert ranking(prof.app, sid, "sam rivera 2") == keys


def test_a_late_joiner_typed_in_with_a_twins_name_is_added_numbered(prof, browser):
    """Just one name and email in the quick-add box: nothing says they're the twin with no email, so they're a
    newcomer, and that twin keeps their PIN and ranking."""
    sid = prof.create_sheet(allow_unlisted=False)
    prof.upload(sid, HEAD + "Sam Rivera,1005,\nSam Rivera,1006,zz@u.school.edu\n" + OTHERS, "students.csv")
    keys = day_keys(sid)
    browser().sign_in_student(sid, "Sam Rivera (2)", pin="4831").rank(sid, keys)
    page = follow(prof, prof.post(f"/teach/s/{sid}/roster/quick-add", {"people": "Sam Rivera, aa@u.school.edu"}))
    assert "Added 1 student: Sam Rivera (3)" in text(page)
    rows = roster(prof.app, sid)
    assert rows["sam rivera 2"] == "" and rows["sam rivera 3"] == "aa@u.school.edu"
    assert gets_in_with_pin(browser, sid, "Sam Rivera (2)", "4831")


def test_a_student_with_no_email_signs_in_by_their_number_beside_twins_with_emails(prof, browser):
    sid = prof.create_sheet(allow_unlisted=False)
    prof.upload(sid, HEAD + "Sam Rivera,1005,\nSam Rivera,1006,zz@u.school.edu\nSam Rivera,1008,aa@u.school.edu\n"
                + OTHERS, "students.csv")
    assert "the one with no email is “Sam Rivera (3)”" in text(prof.get(f"/teach/s/{sid}"))
    browser().sign_in_student(sid, "Sam Rivera (3)", pin="4831")  # straight to their PIN, not the others' emails
    assert gets_in_with_pin(browser, sid, "Sam Rivera (3)", "4831")
    s = browser()
    assert "Which one is you?" in text(s.post(f"/c/{sid}/login", {"name": "Sam Rivera"}))


# ---------------------------------------------------------------------------
# carry_over and compare_rosters on their own
# ---------------------------------------------------------------------------
def row(name, email="", is_test=0):
    return {"name_key": normalize_name(name), "display_name": name, "email": email, "is_test": is_test,
            "name_pending": 0}


def student(name, email=""):
    return {"name_key": normalize_name(name), "display_name": name, "email": email, "name_pending": 0}


def keys_and_emails(students):
    return {s["name_key"]: s["email"] for s in students}


def test_the_review_says_which_names_are_unsure_and_counts_the_new_lists_own():
    current = [row("Sam Rivera"), row("Sam Rivera (2)"), row("Maya Patel", "mp@x.edu")]
    diff = sheets.compare_rosters(current, [student("Sam Rivera"), student("Maya Patel", "mp@x.edu")])
    assert diff["unsure"] == ["Sam Rivera"] and diff["dropping"] == [] and diff["adding"] == []
    assert diff["incoming"] == 2 and len(diff["same"]) == 2 and diff["current"] == 3
    # Nobody left without an email, or the email says who stayed: nothing unsure.
    current = [row("Sam Rivera", "a@x.edu"), row("Sam Rivera (2)")]
    diff = sheets.compare_rosters(current, [student("Sam Rivera", "a@x.edu")])
    assert diff["unsure"] == [] and [r["display_name"] for r in diff["dropping"]] == ["Sam Rivera (2)"]
    current = [row("Alex Kim", "a@x.edu"), row("Alex Kim (2)", "b@x.edu")]
    assert sheets.compare_rosters(current, [student("Alex Kim", "c@x.edu")])["unsure"] == []


def test_carry_over_keeps_each_student_on_their_own_name():
    # A no-email student stays paired with a no-email entry, and the newcomer with an email is numbered.
    out, unsure = sheets.carry_over([row("Sam Rivera")], [student("Sam Rivera", "aa@x.edu"), student("Sam Rivera (2)")])
    assert keys_and_emails(out) == {"sam rivera": "", "sam rivera 2": "aa@x.edu"} and unsure == []
    # The only one left of a name, who had a number, keeps it.
    out, _ = sheets.carry_over([row("Sam Rivera (2)", "b@x.edu")], [student("Sam Rivera")])
    assert keys_and_emails(out) == {"sam rivera 2": ""}
    # "Student 1" and "Student 2" are two names, not one name numbered.
    out, _ = sheets.carry_over([row("Student 1", "s1@x.edu"), row("Student 2", "s2@x.edu")],
                               [student("Student 2", "s2@x.edu"), student("Student 1", "s1@x.edu")])
    assert keys_and_emails(out) == {"student 1": "s1@x.edu", "student 2": "s2@x.edu"}
    # A newcomer never takes a name someone on the list has (a test student's included).
    out, _ = sheets.carry_over([row("Pat Lee", "p@x.edu"), row("Test Student 1", "prof@x.edu", is_test=1)],
                               [student("Pat Lee", "new@x.edu"), student("Pat Lee (2)", "p@x.edu"),
                                student("Test Student 1", "t@x.edu")])
    assert keys_and_emails(out) == {"pat lee": "p@x.edu", "pat lee 2": "new@x.edu", "test student 1 2": "t@x.edu"}
    # A list that gives two students one key ("Student (2)", the second Student, and "Student 2") never
    # stores two rows under it.
    out, _ = sheets.carry_over([], [student("Student", "s@x.edu"), student("Student (2)", "t@x.edu"),
                                    student("Student 2", "u@x.edu")])
    assert len({s["name_key"] for s in out}) == 3
    # An accent Canvas fixed is kept when it doesn't change the name.
    out, _ = sheets.carry_over([row("Ana Lima", "ana@x.edu")], [student("Ana Límà", "ana@x.edu")])
    assert out[0]["display_name"] == "Ana Límà" and out[0]["name_key"] == "ana lima"
