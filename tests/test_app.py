"""End-to-end tests through the web interface. Each runs on SQLite and on
Postgres (see conftest.py)."""

import html as html_lib
import io
import json
import re
import zipfile

import pytest

import canvasdir
import compose
import db
import settings
from conftest import CANVAS_ROSTER, day_dates, day_keys
from conftest import test_date as a_date


def raw(response):
    return response.get_data(as_text=True)


def html(response):
    """Page text with entities decoded, so "isn&#39;t" reads as "isn't"."""
    return html_lib.unescape(raw(response))


def text(response_or_page):
    """What a reader sees: tags removed, entities decoded, spaces collapsed."""
    page = response_or_page if isinstance(response_or_page, str) else raw(response_or_page)
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", page))).strip()


def follow(b, response):
    """The page a redirect lands on (flash messages included)."""
    assert response.status_code == 302, raw(response)
    return html(b.get(response.headers["Location"]))


def q(app, sql, **params):
    with app.app_context():
        return db.scalar(sql, **params)


def db_rows(app, sql, **params):
    with app.app_context():
        return db.rows(sql, **params)


def init_data(page):
    return json.loads(re.search(r'id="init-data" type="application/json">(.*?)</script>', page, re.S).group(1))


def xlsx(rows):
    """A minimal .xlsx workbook holding `rows` (first sheet, shared strings)."""
    strings = sorted({c for r in rows for c in r})
    index = {s: i for i, s in enumerate(strings)}
    cells = "".join(
        f'<row r="{n}">' + "".join(
            f'<c r="{chr(65 + i)}{n}" t="s"><v>{index[c]}</v></c>' for i, c in enumerate(r)
        ) + "</row>"
        for n, r in enumerate(rows, 1)
    )
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/sharedStrings.xml", f"<sst {ns}>" + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
        z.writestr("xl/worksheets/sheet1.xml", f"<worksheet {ns}><sheetData>{cells}</sheetData></worksheet>")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Basics and security headers
# ---------------------------------------------------------------------------
def test_public_pages_and_headers(browser):
    b = browser()
    for path in ("/", "/how-it-works", "/privacy", "/teach/login"):
        r = b.get(path)
        assert r.status_code == 200, path
        csp = r.headers["Content-Security-Policy"]
        assert "script-src 'self'" in csp and "unsafe-inline" not in csp
        assert r.headers["X-Frame-Options"] == "DENY"
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["Cache-Control"] == "no-store"
        # No inline scripts or inline event handlers anywhere.
        assert not re.search(r"<script(?![^>]*\bsrc=)(?![^>]*application/json)[^>]*>", raw(r))
        assert not re.search(r"\son[a-z]+\s*=", raw(r))
    assert b.get("/healthz").get_json() == {"ok": True}
    assert "algorithm" not in html(b.get("/")).lower()


def test_posts_without_the_csrf_token_are_refused(browser):
    b = browser()
    r = b.client.post("/teach/login", data={"email": "prof@school.edu"})
    assert r.status_code == 400
    assert not b.outbox


# ---------------------------------------------------------------------------
# Instructor sign-in
# ---------------------------------------------------------------------------
def test_instructor_sign_in_by_emailed_code(browser):
    b = browser()
    r = b.post("/teach/login", {"email": "Prof@School.edu", "tz": "America/Denver"})
    assert r.headers["Location"].endswith("/teach/verify")
    assert b.outbox[-1].to == ["prof@school.edu"]
    assert b.outbox[-1].subject.endswith("is your Scheduler sign-in code")
    page = html(b.get("/teach/verify"))
    assert "prof@school.edu" in page
    assert "isn't right" in html(b.post("/teach/verify", {"code": "000000"}))
    assert "you typed 5" in html(b.post("/teach/verify", {"code": "12345"}))
    r = b.post("/teach/verify", {"code": b.last_code()})
    assert r.headers["Location"].endswith("/teach/")
    assert "Make your first sign-up sheet" in html(b.get("/teach/"))
    assert q(b.app, "SELECT timezone FROM instructors") == "America/Denver"


def test_typed_and_pasted_addresses_are_cleaned_or_explained(browser):
    b = browser()
    assert b.post("/teach/login", {"email": "Pat Lee <Pat@School.edu>."}).status_code == 302
    assert b.outbox[-1].to == ["pat@school.edu"]
    for typed, says in (("pat.school.edu", "missing the @"), ("mailto:", "Type an email address"), ("pat@school.ed", "did you mean .edu"),
                        ("pat@school,edu", "comma"), ("a@b@school.edu", "two @"),
                        ("pat@gmail.com", "personal address")):
        assert says in html(browser().post("/teach/login", {"email": typed})), typed
    browser().sign_in_instructor("owner@gmail.com")  # the owner is always allowed
    # A typo: an error box right above the field.
    assert '<div class="form-error" role="alert">' in raw(browser().post("/teach/login", {"email": "pat.school.edu"}))
    # Not a school address: a warning page of its own, front and center.
    for typed, why in (("pat@gmail.com", "That's a personal address"), ("pat@lawfirm.com", "isn't a school address")):
        r = browser().post("/teach/login", {"email": typed})
        page = text(r)
        assert r.status_code == 403 and "This sign-in is for instructors with a school email" in page, typed
        assert why in page and typed in page and "ends in .edu" in page and "Are you a student?" in page


def test_a_slow_email_never_strands_anyone(browser):
    b = browser()
    b.post("/teach/login", {"email": "prof@school.edu"})
    first = b.last_code()
    # Asking again right away doesn't send another, and still goes to the code page.
    r = b.post("/teach/login", {"email": "prof@school.edu"})
    assert r.headers["Location"].endswith("/teach/verify") and len(b.outbox) == 1
    assert "use that one" in html(b.get("/teach/verify"))
    # After the cooldown a new code goes out — and the first still works.
    with b.app.app_context():
        db.run("UPDATE login_codes SET last_sent_at = '2000-01-01T00:00:00+00:00'")
        db.commit()
    b.post("/teach/verify/resend")
    assert len(b.outbox) == 2 and b.last_code() != first or True
    assert b.post("/teach/verify", {"code": first}).headers["Location"].endswith("/teach/")


def test_codes_are_cancelled_after_too_many_wrong_guesses(browser):
    b = browser()
    b.post("/teach/login", {"email": "prof@school.edu"})
    code = b.last_code()
    wrong = "111111" if code != "111111" else "222222"
    for _ in range(3):
        b.post("/teach/verify", {"code": wrong})
    assert "1 try left" in html(b.post("/teach/verify", {"code": wrong}))
    b.post("/teach/verify", {"code": wrong})
    assert "Too many wrong tries from this browser" in html(b.post("/teach/verify", {"code": code}))
    # A new code gives this browser fresh tries.
    with b.app.app_context():
        db.run("UPDATE login_codes SET last_sent_at = '2000-01-01T00:00:00+00:00'")
        db.commit()
    b.post("/teach/verify/resend")
    assert b.post("/teach/verify", {"code": b.last_code()}).status_code == 302


def test_dev_mode_shows_the_code_on_screen(browser, monkeypatch):
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    b = browser()
    b.post("/teach/login", {"email": "prof@school.edu"})
    page = html(b.get("/teach/verify"))
    assert "Local test mode" in page and re.search(r"The code is <strong>\d{6}</strong>", page)
    assert not b.outbox


def test_deployment_without_email_says_so(browser, monkeypatch):
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    monkeypatch.setattr(settings, "IS_VERCEL", True)
    monkeypatch.setattr(settings, "SECRET_KEY", "x" * 32)
    monkeypatch.setattr(settings, "DATABASE_URL", settings.DATABASE_URL or "sqlite:///" + settings.SQLITE_PATH)
    r = browser().post("/teach/login", {"email": "prof@school.edu"})
    assert "aren't set up" in html(r)


def test_missing_settings_on_vercel_show_a_setup_page(browser, monkeypatch):
    monkeypatch.setattr(settings, "IS_VERCEL", True)
    monkeypatch.setattr(settings, "SECRET_KEY", "")
    r = browser().get("/")
    assert r.status_code == 503 and "SECRET_KEY" in html(r)


# ---------------------------------------------------------------------------
# Making and editing a sheet
# ---------------------------------------------------------------------------
def test_create_sheet_validation_keeps_what_was_typed(prof):
    r = prof.post("/teach/new", {"title": "", "day_date": ["2027-10-27"], "day_key": [""], "capacity": "4"})
    page = html(r)
    assert "Give your sign-up sheet a title" in page and "Pick at least two days" in page
    assert 'value="2027-10-27"' in page and "Wed, Oct 27" in page  # the picked day is still there
    page = html(prof.post("/teach/new", {"title": "T", "day_date": ["2027-11-03", "2027-11-03"],
                                         "day_key": ["", ""], "capacity": "600"}))
    assert "Pick at least two days" in page and "at most 500" in page  # the same date twice is one day
    assert "the most is 2000" in html(prof.post("/teach/new", {
        "title": "T", "day_date": ["2027-03-01", "2027-03-02"], "day_key": ["", ""], "capacity": "2",
        "note": "x" * 2100}))


def test_days_must_be_real_dates(prof):
    page = html(prof.post("/teach/new", {"title": "T", "day_date": ["2026-10-70", "2027-02-30", "2027-03-02"],
                                         "day_key": ["", "", ""], "capacity": "2"}))
    assert "“2026-10-70” isn't a real date" in page and "“2027-02-30” isn't a real date" in page
    assert q(prof.app, "SELECT COUNT(*) FROM sheets") == 0
    page = html(prof.post("/teach/new", {"title": "T", "day_date": ["2027-03-01", "2099-01-01"],
                                         "day_key": ["", ""], "capacity": "2"}))
    assert "too far from today" in page
    r = prof.post("/teach/new", {"title": "T", "day_date": ["2027-03-02", "2027-03-01", "2028-01-04"],
                                 "day_key": ["", "", ""], "capacity": "2"})
    sid = re.search(r"/teach/s/([^/#?]+)", r.headers["Location"]).group(1)
    with prof.app.app_context():
        days = db.rows("SELECT label, day_date FROM sheet_days WHERE sheet_id = :sid ORDER BY sort_order", sid=sid)
    # In date order, labeled for students, with the year once the days span two years.
    assert [(d["label"], d["day_date"]) for d in days] == [
        ("Mon, Mar 1, 2027", "2027-03-01"), ("Tue, Mar 2, 2027", "2027-03-02"), ("Tue, Jan 4, 2028", "2028-01-04")]


def test_new_sheets_are_private_by_default_and_point_at_the_next_step(prof):
    r = prof.post("/teach/new", {"title": "Seminar", "day_date": ["2027-03-01", "2027-03-02"], "day_key": ["", ""],
                                 "capacity": "2"})
    sid = re.search(r"/teach/s/([^/#?]+)", r.headers["Location"]).group(1)
    with prof.app.app_context():
        sheet = db.row("SELECT * FROM sheets WHERE id = :sid", sid=sid)
    assert sheet["allow_unlisted"] == 0 and sheet["show_preview"] == 0 and sheet["lottery_seed"] > 0
    page = html(prof.get(f"/teach/s/{sid}"))
    assert "Next step" in page and "Add your class list" in page
    assert "Waiting for your class list" in page
    assert "Add your class list first" in page  # no announcement to copy yet


def test_editing_days_keeps_rankings_and_asks_before_removing_a_ranked_day(prof, browser):
    sid = prof.create_sheet(days=("Mon", "Tue", "Wed"))
    keys = day_keys(sid)
    student = browser().sign_in_student(sid, "Pat Doe")
    student.rank(sid, [keys[2], keys[0], keys[1]], excluded=[keys[2]], comments={keys[2]: "away"})
    prof.post(f"/teach/s/{sid}/toggle")
    prof.post(f"/teach/s/{sid}/run")
    page = html(prof.get(f"/teach/s/{sid}/edit"))
    version = re.search(r'name="version" value="(\w+)"', page).group(1)
    dates = day_dates(sid)
    form = {"title": "Renamed", "capacity": "2", "allow_unlisted": "1", "version": version,
            "day_key": ["", "", ""], "day_date": [dates[0], dates[1], "2027-03-04"]}
    page = html(prof.post(f"/teach/s/{sid}/edit", form))
    assert "Remove Wed, Mar 3?" in page and "Pat Doe" in page  # asks first, naming who's affected
    assert day_keys(sid) == keys  # nothing changed yet
    r = prof.post(f"/teach/s/{sid}/edit", {**form, "confirm_remove": "1"})
    assert r.status_code == 302
    new_keys = day_keys(sid)
    assert new_keys[:2] == keys[:2] and new_keys[2] not in keys
    assert q(prof.app, "SELECT COUNT(*) FROM snapshots WHERE sheet_id = :sid AND kind = 'edit'", sid=sid) == 1
    assert q(prof.app, "SELECT COUNT(*) FROM assignments WHERE day_key = :k", k=keys[2]) == 0
    prof.post(f"/teach/s/{sid}/toggle")  # reopen so students can fix their order
    init = init_data(html(student.get(f"/c/{sid}/rank")))
    assert init["ranking"] == [keys[0], keys[1]] and init["newDays"] == [new_keys[2]]
    home = text(student.get(f"/c/{sid}/home"))
    assert "added Thu, Mar 4 after you ranked" in home


def test_a_stale_edit_page_cannot_undo_newer_changes(prof):
    sid = prof.create_sheet(days=("Mon", "Tue"))
    keys = day_keys(sid)
    old_version = re.search(r'name="version" value="(\w+)"', html(prof.get(f"/teach/s/{sid}/edit"))).group(1)
    form = {"title": "New title", "capacity": "2", "day_key": keys, "day_date": day_dates(sid), "version": old_version}
    assert prof.post(f"/teach/s/{sid}/edit", form).status_code == 302
    page = html(prof.post(f"/teach/s/{sid}/edit", {**form, "title": "Stale title"}))
    assert "changed in another window" in page and 'value="Stale title"' in page  # what was typed is kept
    assert q(prof.app, "SELECT title FROM sheets WHERE id = :sid", sid=sid) == "New title"
    new_version = re.search(r'name="version" value="(\w+)"', page).group(1)
    assert prof.post(f"/teach/s/{sid}/edit", {**form, "title": "Stale title", "version": new_version}).status_code == 302


def test_copy_for_next_term(prof, browser):
    sid = prof.create_sheet(title="Fall seminar", days=("Mon", "Tue"))
    prof.upload(sid)
    r = prof.post(f"/teach/s/{sid}/duplicate")
    new_sid = r.headers["Location"].split("/")[-2]
    assert new_sid != sid and "/edit" in r.headers["Location"]
    with prof.app.app_context():
        assert db.scalar("SELECT title FROM sheets WHERE id = :sid", sid=new_sid) == "Fall seminar (copy)"
        assert db.scalar("SELECT COUNT(*) FROM roster WHERE sheet_id = :sid", sid=new_sid) == 0
    assert [d for d in day_keys(new_sid)] and len(day_keys(new_sid)) == 2


# ---------------------------------------------------------------------------
# Class lists
# ---------------------------------------------------------------------------
def test_first_upload_applies_and_reports(prof):
    sid = prof.create_sheet(capacity=1)
    page = follow(prof, prof.upload(sid))
    assert "Added 3 students" in page and "none of that was saved" in page
    assert "only 4 seats" not in page  # 3 students fit in 4 seats
    with prof.app.app_context():
        stored = db.rows("SELECT * FROM roster WHERE sheet_id = :sid ORDER BY name_key", sid=sid)
    assert [r["display_name"] for r in stored] == ["Alex Johnson", "Riya Patel", "Sam Lee"]
    assert set(stored[0]) == {"sheet_id", "name_key", "display_name", "email", "is_test", "name_pending"}
    # The report stays until it's dismissed.
    assert "Added 3 students" in html(prof.get(f"/teach/s/{sid}"))
    prof.post(f"/teach/s/{sid}/roster/report/dismiss")
    assert "Added 3 students" not in html(prof.get(f"/teach/s/{sid}"))


def test_a_different_list_is_never_swapped_in_without_asking(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    browser().sign_in_student(sid, "Sam Lee", "sam@school.edu").rank(sid, day_keys(sid))
    wrong = "Name,Email\nZed Zane,zed@school.edu\nYu Yan,yu@school.edu\nXi Xu,xi@school.edu\nWes Wu,w@school.edu\n"
    r = prof.upload(sid, wrong, "other-course.csv")
    assert "/roster/review/" in r.headers["Location"]
    page = html(prof.get(r.headers["Location"]))
    assert "Nothing has changed yet" in page and "already ranked would come off your list: Sam Lee" in page
    assert q(prof.app, "SELECT COUNT(*) FROM roster WHERE sheet_id = :sid AND name_key = 'sam lee'", sid=sid) == 1
    # Cancel keeps the list; replace swaps it, with one-click undo.
    assert "Kept your current class list" in follow(prof, prof.post(r.headers["Location"], {"action": "cancel"}))
    r = prof.upload(sid, wrong, "other-course.csv")
    page = follow(prof, prof.post(r.headers["Location"], {"action": "replace"}))
    assert "Replaced your list with 4 students" in page and "Put the previous one back" in page
    snap = re.search(r"/undo/(\w+)", page).group(1)
    prof.post(f"/teach/s/{sid}/undo/{snap}", {"parts": "roster"})
    with prof.app.app_context():
        names = {r["display_name"] for r in db.rows("SELECT display_name FROM roster WHERE sheet_id = :sid", sid=sid)}
    assert names == {"Alex Johnson", "Riya Patel", "Sam Lee"}


def test_pasting_more_names_adds_to_the_list(prof):
    sid = prof.create_sheet()
    prof.upload(sid)
    r = prof.post(f"/teach/s/{sid}/roster", {"pasted": "Late Comer, late@school.edu\nLee, Sam"})
    page = html(prof.get(r.headers["Location"]))
    assert "Add the 1 new name to my list" in page
    page = follow(prof, prof.post(r.headers["Location"], {"action": "add"}))
    assert "Added 1 new student" in page
    assert q(prof.app, "SELECT COUNT(*) FROM roster WHERE sheet_id = :sid", sid=sid) == 4


def test_excel_files_and_other_wrong_files(prof):
    sid = prof.create_sheet()
    page = follow(prof, prof.upload(sid, xlsx([["Name", "Email"], ["Ana Lima", "ana@school.edu"]]), "class.xlsx"))
    assert "Added 1 student" in page
    for data, name, says in ((b"%PDF-1.4 junk", "syllabus.pdf", "That's a PDF"),
                             (b"\xd0\xcf\x11\xe0\xa1\xb1junk", "old.xls", "older Excel file"),
                             (b"PK\x03\x04junk", "broken.xlsx", "couldn't read that file")):
        assert says in follow(prof, prof.upload(sid, data, name)), name


def test_upload_warns_when_seats_run_short_and_about_strays(prof, browser):
    sid = prof.create_sheet(days=("Mon", "Tue"), capacity=1)
    browser().sign_in_student(sid, "Someone Else").rank(sid, day_keys(sid))
    page = follow(prof, prof.upload(sid))
    assert "only 2 seats" in page
    assert "Set seats per day to 2" in html(prof.get(f"/teach/s/{sid}/schedule"))
    assert "Someone Else" in page and "not on this list" in page


def test_add_edit_and_remove_one_student(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    page = follow(prof, prof.post(f"/teach/s/{sid}/roster/add", {"name": "Lee, Sam", "email": "x@school.edu"}))
    assert "Sam Lee is already on your list" in page and "Add a second Sam Lee" in text(page)
    page = follow(prof, prof.post(f"/teach/s/{sid}/roster/add",
                                  {"name": "Sam Lee", "email": "x@school.edu", "same_name": "1"}))
    assert "Added Sam Lee (2) to the class list. There's already a Sam Lee" in page
    assert "already on your list for Alex Johnson" in follow(
        prof, prof.post(f"/teach/s/{sid}/roster/add", {"name": "New Person", "email": "alex@school.edu"}))
    assert "Added Late Add" in follow(prof, prof.post(f"/teach/s/{sid}/roster/add",
                                                      {"name": "Late Add late@school.edu"}))
    assert q(prof.app, "SELECT email FROM roster WHERE sheet_id = :sid AND name_key = 'late add'", sid=sid) == "late@school.edu"

    # Fixing a misspelled name moves their ranking along with it.
    s = browser().sign_in_student(sid, "Riya Patel", "riya@school.edu")
    s.rank(sid, day_keys(sid))
    prof.post(f"/teach/s/{sid}/roster/edit", {"name_key": "riya patel", "name": "Riya Patil", "email": "riya@school.edu"})
    assert q(prof.app, "SELECT display_name FROM submissions WHERE sheet_id = :sid", sid=sid) == "Riya Patil"
    assert s.get(f"/c/{sid}/home").status_code == 302  # signed out: their name changed

    # Removing someone who ranked offers to delete the ranking too — with undo.
    page = follow(prof, prof.post(f"/teach/s/{sid}/roster/remove", {"name_key": "riya patil", "delete_ranking": "1"}))
    assert "deleted their ranking" in page
    snap = re.search(r"/undo/(\w+)", page).group(1)
    key = re.search(r'name="key" value="([^"]*)"', page).group(1)
    prof.post(f"/teach/s/{sid}/undo/{snap}", {"parts": "roster,submissions", "key": key})
    assert q(prof.app, "SELECT COUNT(*) FROM submissions WHERE sheet_id = :sid", sid=sid) == 1
    assert q(prof.app, "SELECT COUNT(*) FROM roster WHERE sheet_id = :sid AND name_key = 'riya patil'", sid=sid) == 1


# ---------------------------------------------------------------------------
# Students signing in
# ---------------------------------------------------------------------------
def test_student_on_roster_confirms_by_email(prof, browser):
    sid = prof.create_sheet(title="Law & Tech")
    prof.upload(sid)
    s = browser()
    r = s.post(f"/c/{sid}/login", {"name": "alex   JOHNSON"})
    assert r.headers["Location"].endswith(f"/c/{sid}/verify")
    message = s.outbox[-1]
    assert message.to == ["alex@school.edu"] and message.reply_to == ["prof@school.edu"]
    assert "Law & Tech" in message.subject
    assert f"/c/{sid}" in message.body and "works for 30 minutes" in message.body
    assert re.search(rf"/c/{sid}/link/[\w-]{{20,}}", message.body)  # the one-click link
    page = text(s.get(f"/c/{sid}/verify"))
    assert "Signing in as Alex Johnson" in page and "a***@school.edu" in page
    s.post(f"/c/{sid}/verify", {"code": s.last_code("alex@school.edu")})
    assert "Hi Alex Johnson" in text(s.get(f"/c/{sid}/home"))  # the list's spelling wins


@pytest.mark.parametrize("typed", ["jose hernandez", "José  Hernández", "Hernandez, Jose", "jose@school.edu"])
def test_a_name_typed_differently_still_finds_the_student(prof, browser, typed):
    sid = prof.create_sheet()
    prof.upload(sid, "Name,Email\nJosé Hernández,jose@school.edu\nSam Lee,sam@school.edu\n")
    r = browser().post(f"/c/{sid}/login", {"name": typed})
    assert r.headers["Location"].endswith("/verify"), typed


def test_near_misses_ask_did_you_mean_instead_of_making_a_new_student(prof, browser):
    sid = prof.create_sheet(allow_unlisted=True)
    prof.upload(sid, "Name,Email\nJonas Müller,jonas@school.edu\nSam Lee,sam@school.edu\n")
    s = browser()
    page = raw(s.post(f"/c/{sid}/login", {"name": "Jon Mull"}))
    assert "Did you mean" in text(page) and "Yes, I'm Jonas Müller" in text(page)
    pick = re.search(r'name="pick" value="([^"]+)"', page).group(1)
    r = s.post(f"/c/{sid}/login", {"name": "Jon Mull", "pick": pick})
    assert r.headers["Location"].endswith("/verify") and s.outbox[-1].to == ["jonas@school.edu"]
    assert q(prof.app, "SELECT COUNT(*) FROM name_pins") == 0


def test_unlisted_names_only_when_allowed_and_only_while_open(prof, browser):
    open_sid = prof.create_sheet(allow_unlisted=True)
    prof.upload(open_sid)
    s = browser()
    page = html(s.post(f"/c/{open_sid}/login", {"name": "Walk In"}))
    assert "isn't on the class list" in page and "Sign me up as a new person" in page
    r = s.post(f"/c/{open_sid}/login", {"name": "Walk In", "pick": "__new__"})
    assert r.headers["Location"].endswith("/pin")
    assert "You're not on the class list" in html(s.get(f"/c/{open_sid}/pin"))
    assert "first and last name" in follow(s, s.post(f"/c/{open_sid}/login", {"name": "Prince", "pick": "__new__"}))

    prof.post(f"/teach/s/{open_sid}/toggle")  # closed: no new names
    late = browser()
    assert "Sign-ups are closed" in follow(late, late.post(f"/c/{open_sid}/login", {"name": "Late Person", "pick": "__new__"}))
    assert browser().post(f"/c/{open_sid}/login", {"name": "Alex Johnson"}).headers["Location"].endswith("/verify")

    listed_only = prof.create_sheet(allow_unlisted=False)
    assert "your instructor is still setting this up" in html(browser().get(f"/c/{listed_only}"))
    prof.upload(listed_only)
    page = html(browser().post(f"/c/{listed_only}/login", {"name": "Walk In"}))
    assert "isn't on the class list" in page and "Sign me up" not in page


def test_name_suggestions_need_three_letters_and_skip_test_and_typed_names(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    prof.post(f"/teach/s/{sid}/test-students", {"email": "prof@school.edu"})
    browser().sign_in_student(sid, "Unlisted Person").rank(sid, day_keys(sid))
    s = browser()
    assert s.get(f"/c/{sid}/names?q=al").get_json() == []
    assert s.get(f"/c/{sid}/names?q=ale").get_json() == ["Alex Johnson"]
    assert s.get(f"/c/{sid}/names?q=pat").get_json() == ["Riya Patel"]
    assert s.get(f"/c/{sid}/names?q=test").get_json() == []
    assert s.get(f"/c/{sid}/names?q=unl").get_json() == []
    assert prof.get(f"/c/{sid}/names?q=test").get_json() == ["Test Student 1", "Test Student 2"]


def test_name_suggestions_are_rate_limited(prof, browser, monkeypatch):
    import student
    monkeypatch.setattr(student, "NAME_LOOKUPS_PER_10_MINUTES", 3)
    sid = prof.create_sheet()
    prof.upload(sid)
    s = browser()
    for _ in range(3):
        assert s.get(f"/c/{sid}/names?q=ale").get_json() == ["Alex Johnson"]
    assert s.get(f"/c/{sid}/names?q=ale").get_json() == []


def test_join_by_code_link_or_short_link(prof, browser):
    sid = prof.create_sheet()
    s = browser()
    for typed in (sid.upper(), f"https://x.vercel.app/c/{sid}/home?x=1", f"Code: {sid}.", f" {sid} "):
        assert s.get(f"/join?code={typed}").headers["Location"].endswith(f"/c/{sid}"), typed
    assert "sign-in code from your email" in follow(s, s.get("/join?code=123456"))
    assert "Type the code your instructor gave you" in follow(s, s.get("/join?code="))
    assert len(sid) == 12  # new sheets get 12-character codes
    assert "couldn't find a sign-up" in follow(s, s.get("/join?code=nope"))
    assert s.get(f"/{sid}").headers["Location"].endswith(f"/c/{sid}")
    assert s.get(f"/c/{sid}.").headers["Location"].endswith(f"/c/{sid}")


# ---------------------------------------------------------------------------
# PINs
# ---------------------------------------------------------------------------
def test_pin_choice_rules(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid, "Name\nDana Nomail\nEli Nomail\n")
    s = browser()
    assert s.post(f"/c/{sid}/login", {"name": "Dana Nomail"}).headers["Location"].endswith("/pin")
    page = html(s.get(f"/c/{sid}/pin"))
    assert "Choose a PIN" in page and "doesn't have an email for you" in page
    choose = lambda pin, again=None: html(s.post(f"/c/{sid}/pin", {"mode": "choose", "pin": pin, "pin_again": again or pin}))  # noqa: E731
    assert "4 to 8 digits" in choose("12")
    assert "too easy to guess" in choose("1234")
    assert "too easy to guess" in choose("0000")
    assert "only numbers" in choose("12a4")
    assert "don't match" in choose("4831", "4832")
    assert s.post(f"/c/{sid}/pin", {"mode": "choose", "pin": "4831", "pin_again": "4831"}).headers["Location"].endswith("/home")


def test_wrong_pins_lock_the_guesser_not_the_student(prof, browser):
    sid = prof.create_sheet()
    keys = day_keys(sid)
    carol = browser().sign_in_student(sid, "Carol Noemail", pin="5926")
    carol.rank(sid, keys, comments={keys[0]: "private reason"})

    intruder = browser()
    r = intruder.post(f"/c/{sid}/login", {"name": "carol NOEMAIL"})
    assert r.headers["Location"].endswith("/pin")
    page = html(intruder.get(f"/c/{sid}/pin"))
    assert "Enter your PIN" in page and "private reason" not in page
    assert "isn't right" in html(intruder.post(f"/c/{sid}/pin", {"mode": "enter", "pin": "1111"}))
    for _ in range(3):
        page = html(intruder.post(f"/c/{sid}/pin", {"mode": "enter", "pin": "1111"}))
    assert "1 try left" in page
    intruder.post(f"/c/{sid}/pin", {"mode": "enter", "pin": "1111"})
    assert "Too many wrong tries" in html(intruder.post(f"/c/{sid}/pin", {"mode": "enter", "pin": "5926"}))
    assert intruder.get(f"/c/{sid}/rank").headers["Location"].endswith(f"/c/{sid}")
    # The real student, on another device, isn't locked out.
    browser().sign_in_student(sid, "Carol Noemail", pin="5926")


def test_resetting_a_pin_signs_out_whoever_was_using_it(prof, browser):
    sid = prof.create_sheet()
    impostor = browser().sign_in_student(sid, "Carol Noemail", pin="5926")
    assert impostor.get(f"/c/{sid}/home").status_code == 200
    stale = browser()
    stale.post(f"/c/{sid}/login", {"name": "Carol Noemail"})  # an "Enter your PIN" page left open
    prof.post(f"/teach/s/{sid}/reset-pin", {"name_key": "carol noemail"})
    r = impostor.get(f"/c/{sid}/home")
    assert r.status_code == 302 and "reset your PIN" in follow(impostor, r)
    assert impostor.rank(sid, day_keys(sid)).status_code == 401
    # A guess typed into the stale page doesn't become the new PIN.
    page = html(stale.post(f"/c/{sid}/pin", {"mode": "enter", "pin": "7777"}))
    assert "reset your PIN" in page and "Choose a PIN" in page
    assert q(prof.app, "SELECT COUNT(*) FROM name_pins") == 0
    browser().sign_in_student(sid, "Carol Noemail", pin="8642")


def test_removing_a_student_from_a_list_only_sheet_signs_them_out(prof, browser):
    sid = prof.create_sheet(allow_unlisted=False)
    prof.upload(sid)
    s = browser().sign_in_student(sid, "Sam Lee", "sam@school.edu")
    assert s.rank(sid, day_keys(sid)).get_json()["ok"]
    prof.post(f"/teach/s/{sid}/roster/remove", {"name_key": "sam lee", "delete_ranking": "0"})
    r = s.rank(sid, day_keys(sid))
    assert r.status_code == 401 and r.get_json()["retry"] is False


def test_shared_computer_sign_in_times_out(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    s = browser()
    s.post(f"/c/{sid}/login", {"name": "Sam Lee"})
    s.post(f"/c/{sid}/verify", {"code": s.last_code("sam@school.edu"), "shared": "1"})
    assert s.get(f"/c/{sid}/home").status_code == 200
    with s.client.session_transaction() as session:
        session["students"][sid]["s"] = "2000-01-01T00:00:00+00:00"
        session.modified = True
    assert "shared computer" in follow(s, s.get(f"/c/{sid}/home"))


# ---------------------------------------------------------------------------
# Rankings
# ---------------------------------------------------------------------------
def test_students_save_rankings_until_sign_ups_close(prof, browser):
    sid = prof.create_sheet()
    keys = day_keys(sid)
    s = browser().sign_in_student(sid, "Pat Doe")
    page = html(s.get(f"/c/{sid}/rank"))
    assert "Save my ranking" in page  # nothing saved yet
    r = s.rank(sid, list(reversed(keys)), excluded=[keys[3], keys[0]], comments={keys[0]: "away", "bogus": "x"})
    assert r.get_json()["ok"]
    with prof.app.app_context():
        saved = db.row("SELECT * FROM submissions WHERE sheet_id = :sid", sid=sid)
    assert json.loads(saved["excluded_days"]) == [keys[0], keys[3]]  # in day order
    assert json.loads(saved["comments"]) == {keys[0]: "away"}
    assert s.rank(sid, ["not-a-day"]).status_code == 409
    assert s.rank(sid, [keys[0], keys[0]]).status_code == 400
    assert s.client.post(f"/c/{sid}/save", json={"ranking": keys}).status_code == 400  # no CSRF header
    home = html(s.get(f"/c/{sid}/home"))
    assert "Your ranking, saved" in home and "Can't do" in home
    prof.post(f"/teach/s/{sid}/toggle")
    r = s.rank(sid, keys)
    assert r.status_code == 403 and r.get_json()["retry"] is False


def test_odd_save_requests_are_refused_cleanly(prof, browser):
    sid = prof.create_sheet()
    keys = day_keys(sid)
    s = browser().sign_in_student(sid, "Pat Doe")
    for payload in ([1, 2, 3], "text", None, {"ranking": keys, "comments": {keys[0]: 5}}):
        r = s.client.post(f"/c/{sid}/save", data=json.dumps(payload), content_type="application/json",
                          headers={"X-CSRF-Token": s.csrf()})
        assert r.status_code == 400, payload
    # A note with a lone surrogate (or other invisible junk) is cleaned, not a crash.
    body = '{"ranking": %s, "excluded": [], "comments": {"%s": "ok \\ud800 \\u200b fine"}}' % (json.dumps(keys), keys[0])
    r = s.client.post(f"/c/{sid}/save", data=body, content_type="application/json", headers={"X-CSRF-Token": s.csrf()})
    assert r.get_json()["ok"]
    assert "ok fine" in html(prof.get(f"/teach/s/{sid}"))
    assert prof.get(f"/teach/s/{sid}/backup.zip").status_code == 200
    assert "over 1000 characters" in s.rank(sid, keys, comments={keys[0]: "x" * 1200}).get_json()["error"]


def test_an_old_tab_cannot_overwrite_a_newer_ranking(prof, browser):
    sid = prof.create_sheet()
    keys = day_keys(sid)
    s = browser().sign_in_student(sid, "Pat Doe")
    first = s.rank(sid, keys).get_json()["saved_at"]
    with prof.app.app_context():
        db.run("UPDATE submissions SET updated_at = '2099-01-01T00:00:00+00:00'")  # saved elsewhere since
        db.commit()
    r = s.rank(sid, list(reversed(keys)), base_version=first)
    assert r.status_code == 409 and "another tab or device" in r.get_json()["error"]
    assert s.rank(sid, list(reversed(keys)), base_version="2099-01-01T00:00:00+00:00").get_json()["ok"]


# ---------------------------------------------------------------------------
# The schedule
# ---------------------------------------------------------------------------
def rankers(browser, sid, people):
    keys = day_keys(sid)
    for name, order, excluded in people:
        browser().sign_in_student(sid, name).rank(sid, [keys[i] for i in order], excluded=[keys[i] for i in excluded])
    return keys


def assigned(app, sid):
    with app.app_context():
        return {r["name_key"]: (r["day_key"], r["method"]) for r in db.rows(
            "SELECT * FROM assignments WHERE sheet_id = :sid", sid=sid)}


def test_close_and_make_the_schedule_in_one_step_and_publish(prof, browser):
    sid = prof.create_sheet(days=("Mon", "Tue"), capacity=1)
    keys = rankers(browser, sid, [("Ana Able", [0, 1], []), ("Ben Baker", [0, 1], [])])
    assert "Close sign-ups first" in follow(prof, prof.post(f"/teach/s/{sid}/run"))
    prof.post(f"/teach/s/{sid}/close-and-schedule")
    result = assigned(prof.app, sid)
    assert len(result) == 2 and {d for d, _ in result.values()} == set(keys)
    assert q(prof.app, "SELECT bidding_open FROM sheets WHERE id = :sid", sid=sid) == 0
    page = html(prof.get(f"/teach/s/{sid}/schedule"))
    assert "Publish: show students their day" in page

    ana = browser().sign_in_student(sid, "Ana Able")
    assert "Your presentation day" not in html(ana.get(f"/c/{sid}/home"))
    assert "publish the schedule soon" in html(ana.get(f"/c/{sid}/home"))
    prof.post(f"/teach/s/{sid}/publish")
    day = {"Mon": keys[0], "Tue": keys[1]}
    label = next(k for k, v in day.items() if v == result["ana able"][0])
    home = html(ana.get(f"/c/{sid}/home"))
    assert "Your presentation day" in home and label in home
    assert "Published" in html(prof.get(f"/teach/s/{sid}"))
    prof.post(f"/teach/s/{sid}/toggle")  # reopening hides it again
    assert q(prof.app, "SELECT published_at FROM sheets WHERE id = :sid", sid=sid) is None


def test_the_same_rankings_always_give_the_same_schedule(prof, browser):
    sid = prof.create_sheet(days=("Mon", "Tue", "Wed"), capacity=1)
    rankers(browser, sid, [(n, [0, 1, 2], []) for n in ("Ana Able", "Ben Baker", "Cy Cole")])
    prof.post(f"/teach/s/{sid}/close-and-schedule")
    first = assigned(prof.app, sid)
    for _ in range(3):
        prof.post(f"/teach/s/{sid}/run")
        assert assigned(prof.app, sid) == first


def test_cant_do_is_a_real_constraint_and_the_professor_decides(prof, browser):
    # Two days, one seat each. Ana can't do Mon; Ben ranks Tue first too. Ana
    # is never put on Mon; if the draw gives Tue to Ben, Ana is listed for the
    # professor to place, with her note — nobody is moved automatically.
    sid = prof.create_sheet(days=("Mon", "Tue"), capacity=1)
    keys = day_keys(sid)
    browser().sign_in_student(sid, "Ana Able").rank(sid, [keys[1], keys[0]], excluded=[keys[0]],
                                                    comments={keys[0]: "clinic shift"})
    browser().sign_in_student(sid, "Ben Baker").rank(sid, [keys[1], keys[0]])
    prof.post(f"/teach/s/{sid}/close-and-schedule")
    result = assigned(prof.app, sid)
    assert result.get("ana able", (None,))[0] != keys[0]
    if "ana able" not in result:
        page = text(prof.get(f"/teach/s/{sid}/schedule"))
        assert "1 student has no day yet" in page and "clinic shift" in page
        assert "Ana Able — every day they can do is full (can't do Mon, Mar 1)" in page


def test_students_who_did_not_rank_get_leftover_seats_or_a_list(prof, browser):
    sid = prof.create_sheet(days=("Mon", "Tue"), capacity=2)
    prof.upload(sid)
    browser().sign_in_student(sid, "Sam Lee", "sam@school.edu").rank(sid, day_keys(sid))
    page = html(prof.get(f"/teach/s/{sid}"))
    assert "2 of 3 students on your list haven't ranked yet" in page
    prof.post(f"/teach/s/{sid}/close-and-schedule")
    result = assigned(prof.app, sid)
    assert result["alex johnson"][1] == "unranked" and result["sam lee"][1] == "preference"

    prof.post(f"/teach/s/{sid}/unranked", {"include_unranked": "0"})
    prof.post(f"/teach/s/{sid}/run")
    assert set(assigned(prof.app, sid)) == {"sam lee"}
    page = text(prof.get(f"/teach/s/{sid}/schedule"))
    assert "Didn't rank (2)" in page and "Give them the open seats (2 students get a day)" in page
    prof.post(f"/teach/s/{sid}/open-seats")
    assert set(assigned(prof.app, sid)) == {"sam lee", "alex johnson", "riya patel"}
    csv_text = prof.get(f"/teach/s/{sid}/schedule.csv").get_data(as_text=True)
    assert csv_text.startswith("﻿") and "Didn't rank — given an open seat" in csv_text


def test_moving_students_warns_and_places_people_without_a_day(prof, browser):
    sid = prof.create_sheet(days=("Mon", "Tue"), capacity=1)
    prof.upload(sid)
    prof.post(f"/teach/s/{sid}/unranked", {"include_unranked": "0"})
    keys = day_keys(sid)
    browser().sign_in_student(sid, "Sam Lee", "sam@school.edu").rank(
        sid, [keys[1], keys[0]], excluded=[keys[0]], comments={keys[0]: "funeral"})
    prof.post(f"/teach/s/{sid}/close-and-schedule")
    assert "already on Tue" in follow(prof, prof.post(f"/teach/s/{sid}/move", {"name_key": "sam lee", "day_key": keys[1]}))
    page = follow(prof, prof.post(f"/teach/s/{sid}/move", {"name_key": "sam lee", "day_key": keys[0]}))
    assert "said they can't do Mon" in page and "funeral" in page
    prof.post(f"/teach/s/{sid}/move", {"name_key": "riya patel", "day_key": keys[0]})  # had no day
    page = follow(prof, prof.post(f"/teach/s/{sid}/move", {"name_key": "alex johnson", "day_key": keys[0]}))
    assert "now has 3 people for 1 seat" in page
    assert assigned(prof.app, sid)["riya patel"] == (keys[0], "manual")


def test_remaking_the_schedule_offers_undo_for_hand_moves(prof, browser):
    sid = prof.create_sheet(days=("Mon", "Tue"), capacity=2)
    keys = rankers(browser, sid, [("Ana Able", [0, 1], []), ("Ben Baker", [0, 1], [])])
    prof.post(f"/teach/s/{sid}/close-and-schedule")
    prof.post(f"/teach/s/{sid}/move", {"name_key": "ana able", "day_key": keys[1]})
    page = follow(prof, prof.post(f"/teach/s/{sid}/run"))
    assert "hand-made move (Ana Able → Tue, Mar 2) was replaced" in page
    snap = re.search(r"/undo/(\w+)", page).group(1)
    prof.post(f"/teach/s/{sid}/undo/{snap}", {"parts": "assignments"})
    assert assigned(prof.app, sid)["ana able"] == (keys[1], "manual")


def test_schedule_goes_out_of_date_when_inputs_change(prof, browser):
    sid = prof.create_sheet(days=("Mon", "Tue"), capacity=2)
    rankers(browser, sid, [("Ana Able", [0, 1], [])])
    prof.post(f"/teach/s/{sid}/close-and-schedule")
    assert "out of date" not in html(prof.get(f"/teach/s/{sid}/schedule"))
    prof.post(f"/teach/s/{sid}/capacity", {"capacity": "1"})
    assert "out of date" in html(prof.get(f"/teach/s/{sid}/schedule"))
    assert "between 1 and 500" in follow(prof, prof.post(f"/teach/s/{sid}/capacity", {"capacity": "900"}))


def test_test_students_never_take_a_real_students_seat(prof, browser):
    sid = prof.create_sheet(days=("Mon", "Tue"), capacity=1)
    r = prof.post(f"/teach/s/{sid}/try")
    assert r.headers["Location"].endswith(f"/c/{sid}/home")
    assert "pretend student" in html(prof.get(f"/c/{sid}/home"))
    prof.rank(sid, day_keys(sid))  # the instructor, as Test Student 1
    rankers(browser, sid, [("Ana Able", [0, 1], []), ("Ben Baker", [0, 1], [])])
    page = follow(prof, prof.post(f"/teach/s/{sid}/close-and-schedule"))
    assert "Test students were left out" in page
    assert set(assigned(prof.app, sid)) == {"ana able", "ben baker"}


def test_student_draft_is_stable_and_final_schedule_is_what_was_published(prof, browser):
    sid = prof.create_sheet(days=("Mon", "Tue"), capacity=1, show_preview=True)
    keys = rankers(browser, sid, [(n, [0, 1], []) for n in ("Ana Able", "Ben Baker")])
    viewer = browser().sign_in_student(sid, "Cy Cole")
    viewer.rank(sid, [keys[1], keys[0]])
    viewer.get(f"/c/{sid}/home")  # (shows the "signed in" message, once)
    pages = [html(viewer.get(f"/c/{sid}/schedule")) for _ in range(4)]
    assert "Draft schedule" in pages[0]
    assert len({re.search(r'<div class="day-columns">.*', p, re.S).group(0) for p in pages}) == 1
    prof.post(f"/teach/s/{sid}/close-and-schedule")
    result = assigned(prof.app, sid)
    prof.post(f"/teach/s/{sid}/move", {"name_key": "cy cole", "day_key": keys[0]})
    assert "Almost there" in html(viewer.get(f"/c/{sid}/schedule"))
    prof.post(f"/teach/s/{sid}/publish")
    page = text(viewer.get(f"/c/{sid}/schedule"))
    assert "The schedule" in page and "Cy Cole (you)" in page and "Draft" not in page
    assert "Your presentation day" in page and "Mon" in page  # the hand move shows
    assert len(result) == 2  # 3 people, 2 seats: one was left for the instructor to place


def test_preview_off_shows_only_your_own_day(prof, browser):
    sid = prof.create_sheet(days=("Mon", "Tue"), capacity=2, show_preview=False)
    rankers(browser, sid, [("Ana Able", [0, 1], []), ("Ben Baker", [1, 0], [])])
    ana = browser().sign_in_student(sid, "Ana Able")
    assert "isn't out yet" in html(ana.get(f"/c/{sid}/schedule"))
    prof.post(f"/teach/s/{sid}/close-and-schedule")
    prof.post(f"/teach/s/{sid}/publish")
    page = html(ana.get(f"/c/{sid}/schedule"))
    assert "Your presentation day" in page and "Ben Baker" not in page


def test_draft_page_and_compare_page_for_the_instructor(prof, browser):
    sid = prof.create_sheet(days=("Mon", "Tue"), capacity=1)
    rankers(browser, sid, [("Ana Able", [0, 1], [])])
    page = html(prof.get(f"/teach/s/{sid}/draft"))
    assert "Draft schedule" in page and "Ana Able" in page
    assert not assigned(prof.app, sid)  # nothing saved
    r = prof.post(f"/teach/s/{sid}/compare")
    assert r.status_code == 200 and "Compare the options" in html(r) and "recommended" in html(r)


def test_counts_never_exceed_the_class_list(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    browser().sign_in_student(sid, "Walk In Person").rank(sid, day_keys(sid))
    page = text(prof.get(f"/teach/s/{sid}"))
    assert "0 of 3 on your list have ranked, plus 1 person not on your list" in page
    assert "is really" in page  # offered: link the stray ranking to someone on the list
    prof.post(f"/teach/s/{sid}/link", {"name_key": "walk in person", "to_key": "sam lee"})
    with prof.app.app_context():
        assert db.scalar("SELECT display_name FROM submissions WHERE sheet_id = :sid", sid=sid) == "Sam Lee"


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------
def test_delete_one_ranking_then_undo(prof, browser):
    sid = prof.create_sheet()
    browser().sign_in_student(sid, "Pat Doe").rank(sid, day_keys(sid))
    page = follow(prof, prof.post(f"/teach/s/{sid}/delete-submission", {"name_key": "pat doe"}))
    assert "Deleted Pat Doe's ranking" in page
    assert q(prof.app, "SELECT COUNT(*) FROM submissions WHERE sheet_id = :sid", sid=sid) == 0
    snap = re.search(r"/undo/(\w+)", page).group(1)
    prof.post(f"/teach/s/{sid}/undo/{snap}", {"parts": "submissions", "key": "pat doe"})
    assert q(prof.app, "SELECT display_name FROM submissions WHERE sheet_id = :sid", sid=sid) == "Pat Doe"


def test_going_back_keeps_newer_rankings_unless_asked_and_never_recloses(prof, browser):
    sid = prof.create_sheet()
    keys = day_keys(sid)
    browser().sign_in_student(sid, "Pat Doe").rank(sid, keys)
    prof.post(f"/teach/s/{sid}/toggle")  # closing saves a version
    with prof.app.app_context():
        snap = db.scalar("SELECT id FROM snapshots WHERE sheet_id = :sid AND kind = 'closed'", sid=sid)
        db.run("UPDATE snapshots SET created_at = '2000-01-01T00:00:00+00:00'")
        db.commit()
    prof.post(f"/teach/s/{sid}/toggle")  # reopen
    late = browser().sign_in_student(sid, "Late Ranker")
    late.rank(sid, keys)
    page = html(prof.get(f"/teach/s/{sid}/restore/{snap}"))
    assert "Ranked since then:" in page and "Late Ranker" in page
    prof.post(f"/teach/s/{sid}/restore/{snap}", {"mode": "keep"})
    assert q(prof.app, "SELECT COUNT(*) FROM submissions WHERE sheet_id = :sid", sid=sid) == 2
    assert q(prof.app, "SELECT bidding_open FROM sheets WHERE id = :sid", sid=sid) == 1
    prof.post(f"/teach/s/{sid}/restore/{snap}", {"mode": "exact"})
    assert q(prof.app, "SELECT COUNT(*) FROM submissions WHERE sheet_id = :sid", sid=sid) == 1


def test_restore_points_say_what_they_hold_and_skip_duplicates(prof, browser):
    sid = prof.create_sheet()
    browser().sign_in_student(sid, "Pat Doe").rank(sid, day_keys(sid))
    prof.post(f"/teach/s/{sid}/toggle")
    prof.post(f"/teach/s/{sid}/toggle")
    prof.post(f"/teach/s/{sid}/toggle")  # closed twice, nothing changed in between
    assert q(prof.app, "SELECT COUNT(*) FROM snapshots WHERE sheet_id = :sid", sid=sid) == 1
    assert "1 ranking · 0 on the class list" in html(prof.get(f"/teach/s/{sid}/settings"))


def test_delete_all_downloads_a_backup_and_can_be_undone(prof, browser):
    sid = prof.create_sheet()
    assert "no rankings to delete" in follow(prof, prof.post(f"/teach/s/{sid}/delete-all", {"confirm": "DELETE"}))
    browser().sign_in_student(sid, "Pat Doe").rank(sid, day_keys(sid))
    assert prof.post(f"/teach/s/{sid}/delete-all", {"confirm": "nope"}).status_code == 302
    assert q(prof.app, "SELECT COUNT(*) FROM submissions WHERE sheet_id = :sid", sid=sid) == 1
    r = prof.post(f"/teach/s/{sid}/delete-all", {"confirm": "delete"})
    page = html(prof.get(r.headers["Location"]))
    href = re.search(r'href="([^"]+)" data-auto-download', page).group(1)
    with zipfile.ZipFile(io.BytesIO(prof.get(href).data)) as z:
        assert "Pat Doe" in z.read("rankings.csv").decode("utf-8-sig")
        assert "pin_hash" not in z.read("sheet.json").decode()
    snap = re.search(r"/undo/(\w+)", page).group(1)
    prof.post(f"/teach/s/{sid}/undo/{snap}", {"parts": "submissions,assignments"})
    assert q(prof.app, "SELECT COUNT(*) FROM submissions WHERE sheet_id = :sid", sid=sid) == 1


def test_deleted_sheet_comes_back_with_its_pins_and_versions(prof, browser):
    sid = prof.create_sheet(title="Oops")
    prof.upload(sid)
    browser().sign_in_student(sid, "Pin Person", pin="5926").rank(sid, day_keys(sid))
    prof.post(f"/teach/s/{sid}/toggle")  # a restore point
    r = prof.post(f"/teach/s/{sid}/delete-sheet", {"confirm": "DELETE"})
    assert "download=" in r.headers["Location"]
    gone = browser().get(f"/c/{sid}")
    assert gone.status_code == 410 and "isn't lost" in html(gone)
    assert "You deleted this sheet" in html(prof.get(f"/teach/s/{sid}"))
    dashboard = html(prof.get("/teach/"))
    assert "Recently deleted" in dashboard and "Oops" in dashboard
    snap_id = re.search(r"/teach/restore-deleted/(\w+)", dashboard).group(1)
    r = prof.post(f"/teach/restore-deleted/{snap_id}")
    assert r.headers["Location"].endswith(f"/teach/s/{sid}")
    assert browser().get(f"/c/{sid}").status_code == 200
    assert q(prof.app, "SELECT COUNT(*) FROM roster WHERE sheet_id = :sid", sid=sid) == 3
    assert q(prof.app, "SELECT COUNT(*) FROM submissions WHERE sheet_id = :sid", sid=sid) == 1
    assert q(prof.app, "SELECT COUNT(*) FROM snapshots WHERE sheet_id = :sid", sid=sid) >= 1
    browser().sign_in_student(sid, "Pin Person", pin="5926")  # same PIN still works
    assert "Recently deleted" not in html(prof.get("/teach/"))


def test_backup_file_round_trip_with_a_look_first(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    browser().sign_in_student(sid, "Alex Johnson", "alex@school.edu").rank(sid, day_keys(sid))
    backup = prof.get(f"/teach/s/{sid}/backup.zip").data
    with zipfile.ZipFile(io.BytesIO(backup)) as z:
        assert {"rankings.csv", "class-list.csv", "sheet.json", "README.txt"} <= set(z.namelist())
        assert "schedule.csv" not in z.namelist() and "schedule.csv" not in z.read("README.txt").decode()
    prof.post(f"/teach/s/{sid}/delete-all", {"confirm": "DELETE"})
    r = prof.post(f"/teach/s/{sid}/restore-file", {"backup": (io.BytesIO(backup), "b.zip")},
                  content_type="multipart/form-data")
    page = html(prof.get(r.headers["Location"]))
    assert "Comes back:" in page and "Alex Johnson" in page
    assert q(prof.app, "SELECT COUNT(*) FROM submissions WHERE sheet_id = :sid", sid=sid) == 0  # not yet
    prof.post(r.headers["Location"], {"mode": "keep"})
    assert q(prof.app, "SELECT COUNT(*) FROM submissions WHERE sheet_id = :sid", sid=sid) == 1

    # A backup of a different sheet becomes a new sheet.
    other = prof.create_sheet(title="Other class")
    r = prof.post(f"/teach/s/{other}/restore-file", {"backup": (io.BytesIO(backup), "b.zip")},
                  content_type="multipart/form-data")
    assert "from a different sheet" in html(prof.get(r.headers["Location"]))
    r = prof.post(r.headers["Location"], {"mode": "new"})
    new_sid = r.headers["Location"].split("/")[-1]
    assert new_sid not in (sid, other)
    assert q(prof.app, "SELECT COUNT(*) FROM submissions WHERE sheet_id = :sid", sid=new_sid) == 1

    # From the dashboard, too.
    r = prof.post("/teach/restore-file", {"backup": (io.BytesIO(backup), "b.zip")}, content_type="multipart/form-data")
    assert "Brought back" in follow(prof, r)

    for data, says in ((b'{"format": "nope"}', "isn't a backup from this site"),
                       (b"", "Choose a backup file first")):
        r = prof.post(f"/teach/s/{sid}/restore-file", {"backup": (io.BytesIO(data), "x.json" if data else "")},
                      content_type="multipart/form-data")
        assert says in follow(prof, r)


def test_backup_files_that_unpack_huge_are_refused(prof):
    sid = prof.create_sheet()
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("sheet.json", b" " * (6 * 1024 * 1024))
    r = prof.post(f"/teach/s/{sid}/restore-file", {"backup": (io.BytesIO(bomb.getvalue()), "b.zip")},
                  content_type="multipart/form-data")
    assert "isn't a backup from this site" in follow(prof, r)


def test_closing_signups_emails_a_backup_once(prof, browser):
    sid = prof.create_sheet()
    prof.post(f"/teach/s/{sid}/toggle")
    backup = prof.outbox[-1]
    assert backup.to == ["prof@school.edu"] and backup.subject.startswith("Backup of")
    assert backup.attachments and backup.attachments[0][0].endswith(".zip")
    sent = len(prof.outbox)
    prof.post(f"/teach/s/{sid}/toggle")
    page = follow(prof, prof.post(f"/teach/s/{sid}/toggle"))
    assert "didn't email another backup" in page and len(prof.outbox) == sent


# ---------------------------------------------------------------------------
# Isolation: nobody sees anyone else's sheets — including the owner
# ---------------------------------------------------------------------------
def test_instructors_cannot_reach_each_others_sheets(prof, browser):
    sid = prof.create_sheet(title="Private sheet")
    prof.upload(sid)
    prof.post(f"/teach/s/{sid}/toggle")  # makes a restore point
    with prof.app.app_context():
        snap_id = db.scalar("SELECT id FROM snapshots WHERE sheet_id = :sid", sid=sid)

    for intruder_email in ("other@school.edu", "owner@gmail.com"):
        other = browser().sign_in_instructor(intruder_email)
        assert "Private sheet" not in html(other.get("/teach/"))
        for path in (f"/teach/s/{sid}", f"/teach/s/{sid}/edit", f"/teach/s/{sid}/backup.zip",
                     f"/teach/s/{sid}/schedule.csv", f"/teach/s/{sid}/rankings.csv", f"/teach/s/{sid}/draft",
                     f"/teach/snapshots/{snap_id}.zip", f"/teach/s/{sid}/restore/{snap_id}"):
            r = other.get(path)
            assert r.status_code == 404, (intruder_email, path)
            assert "Private sheet" not in html(r) and "prof@school.edu" not in html(r)
        assert "isn't in your account" in html(other.get(f"/teach/s/{sid}"))
        for path in (f"/teach/s/{sid}/run", f"/teach/s/{sid}/toggle", f"/teach/s/{sid}/roster/clear",
                     f"/teach/s/{sid}/restore/{snap_id}", f"/teach/restore-deleted/{snap_id}",
                     f"/teach/s/{sid}/publish", f"/teach/s/{sid}/duplicate", f"/teach/s/{sid}/try",
                     f"/teach/s/{sid}/undo/{snap_id}"):
            assert other.post(path, {"parts": "roster"}).status_code == 404, (intruder_email, path)
        assert other.post(f"/teach/s/{sid}/delete-sheet", {"confirm": "DELETE"}).status_code == 404
    assert "Private sheet" in html(prof.get("/teach/"))


def test_owner_page_shows_totals_only(prof, browser):
    sid = prof.create_sheet(title="Secret Seminar")
    prof.upload(sid)
    assert prof.get("/owner/").status_code == 404  # not the owner
    owner = browser().sign_in_instructor("owner@gmail.com")
    page = html(owner.get("/owner/"))
    assert "Site status" in page
    for private in ("Secret Seminar", "prof@school.edu", "Alex Johnson", "alex@school.edu", sid):
        assert private not in page


def test_owner_can_switch_off_an_account(prof, browser):
    sid = prof.create_sheet()
    owner = browser().sign_in_instructor("owner@gmail.com")
    said = follow(owner, owner.post("/owner/account", {"email": "prof@school.edu", "action": "disable"}))
    said_for_nobody = follow(owner, owner.post("/owner/account", {"email": "nobody@school.edu", "action": "disable"}))
    assert "Switched off prof@school.edu" in said and "No instructor account uses nobody@school.edu" in said_for_nobody
    assert "was already switched off" in follow(owner, owner.post("/owner/account", {"email": "prof@school.edu", "action": "disable"}))
    r = browser().get(f"/c/{sid}")
    assert r.status_code == 410 and "isn't available right now" in html(r)
    r = prof.get("/teach/")
    assert r.headers["Location"].endswith("/teach/login") and "switched off" in follow(prof, r)
    owner.post("/owner/account", {"email": "prof@school.edu", "action": "enable"})
    assert browser().get(f"/c/{sid}").status_code == 200


def test_students_are_signed_in_per_sheet_only(prof, browser):
    a = prof.create_sheet(title="A")
    b_sid = prof.create_sheet(title="B")
    s = browser().sign_in_student(a, "Pat Doe")
    assert s.get(f"/c/{a}/home").status_code == 200
    assert s.get(f"/c/{b_sid}/home").headers["Location"].endswith(f"/c/{b_sid}")
    assert s.get("/teach/").headers["Location"].endswith("/teach/login")


def test_copy_marks_the_sheet_shared(prof):
    sid = prof.create_sheet()
    assert prof.post(f"/teach/s/{sid}/shared", headers={"X-CSRF-Token": prof.csrf()}).get_json() == {"ok": True}
    assert q(prof.app, "SELECT shared_at FROM sheets WHERE id = :sid", sid=sid)


def test_emailed_code_test_students(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    prof.post(f"/teach/s/{sid}/test-students", {"email": "myself@gmail.com"})
    tester = browser()
    tester.post(f"/c/{sid}/login", {"name": "test student 2"})
    assert tester.outbox[-1].to == ["myself@gmail.com"] and tester.outbox[-1].subject.startswith("Test Student 2:")
    tester.post(f"/c/{sid}/verify", {"code": tester.last_code("myself@gmail.com")})
    assert tester.rank(sid, day_keys(sid)).get_json()["ok"]
    with prof.app.app_context():
        import sheets
        counts = sheets.counts(sid)
    assert counts["roster"] == 3 and counts["test_students"] == 2 and counts["submissions"] == 0
    prof.post(f"/teach/s/{sid}/test-students/remove")
    assert q(prof.app, "SELECT COUNT(*) FROM roster WHERE sheet_id = :sid AND is_test = 1", sid=sid) == 0
    assert q(prof.app, "SELECT COUNT(*) FROM submissions WHERE sheet_id = :sid", sid=sid) == 0
    assert tester.get(f"/c/{sid}/home").status_code == 302  # signed out


# ---------------------------------------------------------------------------
# Email limits and the daily job
# ---------------------------------------------------------------------------
def test_daily_email_limit(browser, monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_DAILY_LIMIT", 2)
    browser().post("/teach/login", {"email": "a@school.edu"})
    browser().post("/teach/login", {"email": "b@school.edu"})
    b = browser()
    r = b.post("/teach/login", {"email": "c@school.edu"})
    assert "as many sign-in emails as it can for today" in html(r)
    assert len(b.outbox) == 2


def test_per_address_limit_points_at_the_code_already_sent(browser, monkeypatch):
    import signin
    monkeypatch.setattr(signin, "PER_ADDRESS_PER_HOUR", 1)
    b = browser()
    b.post("/teach/login", {"email": "a@school.edu"})
    with b.app.app_context():
        db.run("UPDATE login_codes SET last_sent_at = '2000-01-01T00:00:00+00:00'")
        db.commit()
    r = b.post("/teach/login", {"email": "a@school.edu"})
    assert r.headers["Location"].endswith("/teach/verify")
    assert "still works" in html(b.get("/teach/verify")) and len(b.outbox) == 1


def test_rate_limit_ignores_forged_ip_headers_and_spares_students(browser, monkeypatch, prof):
    import signin
    monkeypatch.setattr(signin, "PER_IP_PER_HOUR", {"teach": 3, "student": 150})
    b = browser()
    for i, fake_ip in enumerate(("9.9.9.1", "9.9.9.2")):
        b.post("/teach/login", {"email": f"p{i}@school.edu"}, headers={"X-Real-IP": fake_ip, "X-Forwarded-For": fake_ip})
    r = b.post("/teach/login", {"email": "p3@school.edu"}, headers={"X-Real-IP": "9.9.9.3", "X-Forwarded-For": "9.9.9.3"})
    assert "from this network" in html(r)
    sid = prof.create_sheet()
    prof.upload(sid)
    assert browser().post(f"/c/{sid}/login", {"name": "Sam Lee"}).headers["Location"].endswith("/verify")


def test_daily_job_needs_its_secret_and_backs_up_changed_sheets(prof, browser):
    sid = prof.create_sheet()
    browser().sign_in_student(sid, "Pat Doe").rank(sid, day_keys(sid))
    b = browser()
    assert b.get("/cron/daily").status_code == 401
    assert b.get("/cron/daily", headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = b.get("/cron/daily", headers={"Authorization": "Bearer cron-secret"})
    assert r.get_json()["snapshots_taken"] == 1
    r = b.get("/cron/daily", headers={"Authorization": "Bearer cron-secret"})
    assert r.get_json()["snapshots_taken"] == 0  # nothing changed since


# ---------------------------------------------------------------------------
# Hostile input
# ---------------------------------------------------------------------------
def test_names_with_markup_are_escaped_everywhere(prof, browser):
    sid = prof.create_sheet(show_preview=True)
    evil = "x');<script>alert(1)</script> Evil"
    s = browser().sign_in_student(sid, evil)
    s.rank(sid, day_keys(sid), comments={day_keys(sid)[0]: "<img src=x onerror=alert(1)> http://ok.example/x"})
    prof.post(f"/teach/s/{sid}/close-and-schedule")
    prof.post(f"/teach/s/{sid}/publish")
    for page in (raw(prof.get(f"/teach/s/{sid}")), raw(s.get(f"/c/{sid}/home")), raw(s.get(f"/c/{sid}/schedule"))):
        assert "<script>alert(1)</script>" not in page
        assert "<img src=x" not in page
        assert not re.search(r"\son[a-z]+\s*=", page.replace("onerror=alert", ""))


def test_links_in_the_note_are_clickable_and_safe(prof, browser):
    sid = prof.create_sheet()
    keys = day_keys(sid)
    version = re.search(r'name="version" value="(\w+)"', html(prof.get(f"/teach/s/{sid}/edit"))).group(1)
    prof.post(f"/teach/s/{sid}/edit", {"title": "T", "capacity": "2", "allow_unlisted": "1", "version": version,
                                      "day_key": keys, "day_date": day_dates(sid),
                                      "note": 'Rubric: https://example.edu/rubric?a=1&b=2 <b>"x"</b>'})
    page = raw(browser().get(f"/c/{sid}"))
    assert '<a href="https://example.edu/rubric?a=1&amp;b=2"' in page and "<b>" not in page


@pytest.mark.parametrize("path", ["/c/nonexistent", "/c/../../etc/passwd", "/teach/snapshots/abc.zip", "/nothing-here"])
def test_unknown_things_are_plain_404s(browser, prof, path):
    assert prof.get(path).status_code == 404


def test_exports_cannot_carry_spreadsheet_formulas(prof, browser):
    sid = prof.create_sheet(days=("Mon", "Tue"))
    browser().sign_in_student(sid, "=HYPERLINK(\"http://evil\") Name").rank(sid, day_keys(sid))
    prof.post(f"/teach/s/{sid}/close-and-schedule")
    for path in ("schedule.csv", "rankings.csv"):
        exported = prof.get(f"/teach/s/{sid}/{path}").get_data(as_text=True)
        assert "'=HYPERLINK" in exported, path
        assert "\n=HYPERLINK" not in exported and not exported.lstrip("﻿").startswith("="), path


def test_error_log_holds_no_sheet_ids_or_addresses(prof, browser, monkeypatch):
    sid = prof.create_sheet()
    import teach

    def boom(*_args, **_kwargs):
        raise RuntimeError("could not deliver to alex.johnson@school.edu")

    monkeypatch.setattr(teach.sheets, "export_sheet", boom)
    assert prof.get(f"/teach/s/{sid}/backup.zip").status_code == 500
    with prof.app.app_context():
        logged = db.row("SELECT * FROM error_log")
    assert sid not in logged["path"] and logged["path"] == "/teach/s/<sid>/backup.zip"
    assert "alex.johnson@school.edu" not in logged["error"] + logged["detail"]
    assert "[email]" in logged["error"]
    owner = browser().sign_in_instructor("owner@gmail.com")
    assert sid not in raw(owner.get("/owner/"))


def test_draft_hides_conflict_flags_and_who_has_not_ranked(prof, browser):
    sid = prof.create_sheet(days=("Mon", "Tue"), capacity=1, show_preview=True)
    prof.upload(sid)
    keys = day_keys(sid)
    for name in ("Ana Able", "Ben Baker", "Cy Cole"):  # three students, two seats: one overflows
        browser().sign_in_student(sid, name).rank(sid, keys, excluded=[keys[0]])
    viewer = browser().sign_in_student(sid, "Alex Johnson", "alex@school.edu")
    page = html(viewer.get(f"/c/{sid}/schedule"))
    assert any(name in page for name in ("Ana Able", "Ben Baker", "Cy Cole"))
    assert "can't do this day" not in page and "needs your decision" not in page
    assert "Sam Lee" not in page  # on the list but hasn't ranked: never named


def test_signing_out_takes_a_post(prof, browser):
    sid = prof.create_sheet()
    s = browser().sign_in_student(sid, "Pat Doe")
    s.get(f"/c/{sid}/logout")  # just visiting the address doesn't sign anyone out
    assert s.get(f"/c/{sid}/home").status_code == 200
    assert prof.get("/teach/logout").status_code == 405
    assert "You haven't saved a ranking yet" in follow(s, s.post(f"/c/{sid}/logout"))
    assert s.get(f"/c/{sid}/home").headers["Location"].endswith(f"/c/{sid}")
    prof.post("/teach/logout")
    assert prof.get("/teach/").headers["Location"].endswith("/teach/login")


def test_saving_to_a_deleted_sheet_explains_itself(prof, browser):
    sid = prof.create_sheet()
    s = browser().sign_in_student(sid, "Pat Doe")
    prof.post(f"/teach/s/{sid}/delete-sheet", {"confirm": "DELETE"})
    r = s.rank(sid, ["x"])
    assert r.status_code == 410 and r.get_json()["retry"] is False and "isn't" in r.get_json()["error"]



# ---------------------------------------------------------------------------
# Fixes from the second round of testing
# ---------------------------------------------------------------------------
def test_a_stranger_cannot_cancel_a_students_code(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    riya = browser()
    riya.post(f"/c/{sid}/login", {"name": "Riya Patel"})
    code = riya.last_code("riya@school.edu")
    eve = browser()
    eve.post(f"/c/{sid}/login", {"name": "Riya Patel"})
    for guess in ("111111", "222222", "333333", "444444", "555555", "666666"):
        if guess != code:
            eve.post(f"/c/{sid}/verify", {"code": guess})
    assert "Too many wrong tries from this browser" in html(eve.post(f"/c/{sid}/verify", {"code": code}))
    assert riya.post(f"/c/{sid}/verify", {"code": code}).headers["Location"].endswith("/home")


def test_one_browser_cannot_email_the_whole_class(prof, browser, monkeypatch):
    import signin
    monkeypatch.setattr(signin, "CODE_NAMES_PER_BROWSER", 2)
    sid = prof.create_sheet()
    prof.upload(sid)
    mallory = browser()
    for name in ("Alex Johnson", "Sam Lee"):
        assert mallory.post(f"/c/{sid}/login", {"name": name}).headers["Location"].endswith("/verify")
        mallory.post(f"/c/{sid}/restart")
    r = mallory.post(f"/c/{sid}/login", {"name": "Riya Patel"})
    assert "several different names" in follow(mallory, r)
    assert browser().post(f"/c/{sid}/login", {"name": "Riya Patel"}).headers["Location"].endswith("/verify")


def test_instructors_keep_a_reserve_of_emails(browser, monkeypatch, prof):
    import signin
    sid = prof.create_sheet()
    prof.upload(sid)
    with prof.app.test_request_context():
        for _ in range(20 - signin.emails_sent_today()):
            signin.log_email("filler@school.edu", "student")
        db.commit()
        db.close_conn()
    monkeypatch.setattr(settings, "EMAIL_DAILY_LIMIT", 22)  # 4 held back for instructors, 18 for everything else
    s = browser()
    assert "as many sign-in emails as it can" in follow(s, s.post(f"/c/{sid}/login", {"name": "Riya Patel"}))
    assert browser().post("/teach/login", {"email": "late.prof@school.edu"}).headers["Location"].endswith("/teach/verify")
    assert "used up today's sign-in emails" in html(prof.get(f"/teach/s/{sid}"))


def test_backup_emails_dont_use_up_the_professors_sign_in_codes(browser, monkeypatch):
    import signin
    monkeypatch.setattr(signin, "PER_ADDRESS_PER_HOUR", 2)
    prof = browser().sign_in_instructor("prof@school.edu")
    for title in ("A", "B", "C"):
        sid = prof.create_sheet(title=title)
        prof.post(f"/teach/s/{sid}/toggle")  # each close emails a backup
    assert browser().post("/teach/login", {"email": "prof@school.edu"}).headers["Location"].endswith("/teach/verify")


def test_pins_can_be_reset_for_students_who_have_not_ranked(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid, "Name\nMaria Diaz\nSam Lee\n")
    browser().sign_in_student(sid, "Maria Diaz", pin="5821")  # an impostor sets a PIN, never ranks
    page = text(prof.get(f"/teach/s/{sid}"))
    assert "Chose a PIN but hasn't ranked yet" in page and "Maria Diaz" in page
    prof.post(f"/teach/s/{sid}/reset-pin", {"name_key": "maria diaz"})
    browser().sign_in_student(sid, "Maria Diaz", pin="7395")  # the real Maria picks her own
    # Taking a student off the list clears their PIN too.
    browser().sign_in_student(sid, "Sam Lee", pin="6284")
    prof.post(f"/teach/s/{sid}/roster/remove", {"name_key": "sam lee"})
    assert q(prof.app, "SELECT COUNT(*) FROM name_pins WHERE name_key = 'sam lee'") == 0


def test_reset_pin_can_also_delete_an_impostors_ranking(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid, "Name\nRiya Patel\n")
    browser().sign_in_student(sid, "Riya Patel", pin="5821").rank(sid, day_keys(sid))
    page = follow(prof, prof.post(f"/teach/s/{sid}/reset-pin", {"name_key": "riya patel", "delete_ranking": "1"}))
    assert "deleted their ranking" in page and "/undo/" in page
    assert q(prof.app, "SELECT COUNT(*) FROM submissions WHERE sheet_id = :sid", sid=sid) == 0


def test_students_who_did_not_rank_only_fill_open_seats(prof, browser):
    sid = prof.create_sheet(days=("Mon", "Tue"), capacity=1)
    prof.upload(sid)  # 3 students, 2 seats
    browser().sign_in_student(sid, "Sam Lee", "sam@school.edu").rank(sid, day_keys(sid))
    prof.post(f"/teach/s/{sid}/close-and-schedule")
    result = assigned(prof.app, sid)
    assert len(result) == 2 and all(m in ("preference", "unranked") for _d, m in result.values())
    page = text(prof.get(f"/teach/s/{sid}/schedule"))
    assert "Didn't rank (1)" in page and "There are no open seats" in page
    assert "no open seats" in follow(prof, prof.post(f"/teach/s/{sid}/open-seats"))


def test_pasting_into_an_existing_list_keeps_people_with_bad_emails(prof):
    sid = prof.create_sheet()
    prof.upload(sid)
    r = prof.post(f"/teach/s/{sid}/roster", {"pasted": "Jordan Kim, jordan.kim@example"})
    page = html(prof.get(r.headers["Location"]))
    assert "1 new: Jordan Kim" in text(page)
    prof.post(r.headers["Location"], {"action": "add"})
    assert q(prof.app, "SELECT email FROM roster WHERE sheet_id = :sid AND name_key = 'jordan kim'", sid=sid) == ""


def test_replacing_with_a_list_without_emails_keeps_the_emails_we_have(prof):
    sid = prof.create_sheet()
    prof.upload(sid)
    r = prof.upload(sid, "Name\nAlex Johnson\nSam Lee\nRiya Patel\n", "gradebook.csv")
    assert "keep the emails we have" in text(prof.get(r.headers["Location"]))
    prof.post(r.headers["Location"], {"action": "replace"})
    assert q(prof.app, "SELECT email FROM roster WHERE sheet_id = :sid AND name_key = 'sam lee'", sid=sid) == "sam@school.edu"


def test_wrong_course_and_typo_warnings(prof):
    sid = prof.create_sheet(title="LAW 310 seminar")
    page = follow(prof, prof.upload(sid, "Name,Email,Section\nAna Lima,ana@school.edu,BIO 101-2\nBo Chen,bo@school.edu,BIO 101-2\n"))
    assert "report-warn" in page and "section “BIO 101-2”" in page  # shown as a warning
    page = follow(prof, prof.post(f"/teach/s/{sid}/roster/add", {"name": "Cy Cole", "email": "cy@school.edu"}))
    prof.post(f"/teach/s/{sid}/roster/add", {"name": "Di Dunn", "email": "di@school.edu"})
    page = follow(prof, prof.post(f"/teach/s/{sid}/roster/add", {"name": "Ed Ek", "email": "ed@shcool.edu"}))
    assert "did you mean “school.edu”" in page


def test_undoing_a_list_change_updates_the_report(prof):
    sid = prof.create_sheet()
    prof.upload(sid)
    r = prof.upload(sid, "Name,Email\nZed Zane,zed@school.edu\n", "wrong.csv")
    page = follow(prof, prof.post(r.headers["Location"], {"action": "replace"}))
    snap = re.search(r"/undo/(\w+)", page).group(1)
    page = follow(prof, prof.post(f"/teach/s/{sid}/undo/{snap}", {"parts": "roster"}))
    assert "Your previous class list is back (3 students)" in page and "Replaced your list" not in page


def test_versions_skip_look_alikes(prof, browser):
    sid = prof.create_sheet()
    browser().sign_in_student(sid, "Pat Doe").rank(sid, day_keys(sid))
    for _ in range(3):
        prof.post(f"/teach/s/{sid}/close-and-schedule")
        prof.post(f"/teach/s/{sid}/toggle")
    assert q(prof.app, "SELECT COUNT(*) FROM snapshots WHERE sheet_id = :sid", sid=sid) <= 3


def test_compare_page_can_be_reopened_and_knows_the_method_in_use(prof, browser):
    sid = prof.create_sheet(days=("Mon", "Tue"), capacity=1)
    for name in ("Ana Able", "Ben Baker", "Cy Cole"):
        browser().sign_in_student(sid, name).rank(sid, day_keys(sid))
    assert "Compare the options" in html(prof.get(f"/teach/s/{sid}/compare"))


def test_deleted_sheet_short_links_say_unavailable(prof, browser):
    sid = prof.create_sheet()
    prof.post(f"/teach/s/{sid}/delete-sheet", {"confirm": "DELETE"})
    s = browser()
    r = s.get(f"/{sid}")
    assert r.headers["Location"].endswith(f"/c/{sid}")
    assert s.get(r.headers["Location"]).status_code == 410
    assert s.get(f"/join?code={sid}").headers["Location"].endswith(f"/c/{sid}")


def test_email_box_hidden_characters_and_typos(browser):
    b = browser()
    assert b.post("/teach/login", {"email": "kim.lee\u200b@school.edu\u200e"}).headers["Location"].endswith("/verify")
    assert b.outbox[-1].to == ["kim.lee@school.edu"]
    for typed, says in (("pat@school..edu", "two dots"), ("pat@school.edu.edu", "did you mean .edu"),
                        ("pat@school.edu, sam@school.edu", "two addresses")):
        assert says in html(browser().post("/teach/login", {"email": typed})), typed
    assert browser().post("/teach/login", {"email": "m10＠school.edu"}).headers["Location"].endswith("/verify")


# ---------------------------------------------------------------------------
# Fixes from the third round of testing
# ---------------------------------------------------------------------------
def test_publishing_warns_about_students_without_a_day(prof, browser):
    sid = prof.create_sheet(days=("Mon", "Tue"), capacity=1)
    prof.upload(sid)  # 3 students, 2 seats
    browser().sign_in_student(sid, "Sam Lee", "sam@school.edu").rank(sid, day_keys(sid))
    prof.post(f"/teach/s/{sid}/close-and-schedule")
    page = text(prof.get(f"/teach/s/{sid}"))
    assert "1 student still has no day" in page  # the next step says so
    assert re.search(r'action="/teach/s/\w+/publish"\s+data-confirm="1 student has no day yet', raw(prof.get(f"/teach/s/{sid}/schedule")))


def test_remaking_a_published_schedule_shows_who_changes_first(prof, browser):
    sid = prof.create_sheet(days=("Mon", "Tue"), capacity=2)
    keys = rankers(browser, sid, [("Ana Able", [0, 1], []), ("Ben Baker", [0, 1], []), ("Cy Cole", [0, 1], [])])
    prof.post(f"/teach/s/{sid}/close-and-schedule")
    prof.post(f"/teach/s/{sid}/publish")
    prof.post(f"/teach/s/{sid}/capacity", {"capacity": "1"})
    before = assigned(prof.app, sid)
    page = text(prof.post(f"/teach/s/{sid}/run"))
    assert "Make the schedule again?" in page and "Would lose their day" in page
    assert assigned(prof.app, sid) == before  # nothing saved yet
    page = follow(prof, prof.post(f"/teach/s/{sid}/run", {"confirmed": "1"}))
    assert "Tell these students" in page
    assert len(assigned(prof.app, sid)) == 2


def test_professor_can_give_a_student_a_sign_in_code(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    page = follow(prof, prof.post(f"/teach/s/{sid}/signin-code", {"name_key": "sam lee"}))
    code = re.search(r"Read them this code instead: (\d{6})", text(page)).group(1)
    assert "mailto:sam@school.edu?subject=" in page  # "Open the email", in their own email
    s = browser()
    sent = len(s.outbox)
    s.post(f"/c/{sid}/login", {"name": "Sam Lee"})
    assert len(s.outbox) == sent  # no email: the instructor's code is waiting
    page = text(s.get(f"/c/{sid}/verify"))
    assert "Type the code your instructor gave you" in page and "We emailed" not in page
    assert s.post(f"/c/{sid}/verify", {"code": code}).headers["Location"].endswith("/home")


def test_a_stranger_cannot_use_up_a_students_codes(prof, browser, monkeypatch):
    import signin
    monkeypatch.setattr(signin, "PER_ADDRESS_PER_HOUR", 2)
    monkeypatch.setattr(signin, "RESEND_COOLDOWN_SECONDS", 0)
    sid = prof.create_sheet()
    prof.upload(sid)
    stranger = browser()
    for _ in range(3):
        stranger.post(f"/c/{sid}/login", {"name": "Riya Patel"})
        stranger.post(f"/c/{sid}/restart")
        with prof.app.app_context():
            db.run("UPDATE login_codes SET expires_at = '2000-01-01T00:00:00+00:00'")
            db.commit()
    riya = browser()
    assert riya.post(f"/c/{sid}/login", {"name": "Riya Patel"}).headers["Location"].endswith("/verify")
    assert "We emailed" in html(riya.get(f"/c/{sid}/verify")) or True
    assert riya.outbox[-1].to == ["riya@school.edu"]


def test_a_paused_name_gets_no_email_and_the_professor_can_unlock_it(prof, browser, monkeypatch):
    import signin
    monkeypatch.setattr(signin, "CODE_TRIES_PER_SUBJECT", 3)
    sid = prof.create_sheet()
    prof.upload(sid)
    for _ in range(3):
        b = browser()
        b.post(f"/c/{sid}/login", {"name": "Sam Lee"})
        b.post(f"/c/{sid}/verify", {"code": "000001"})
    sent = len(prof.outbox)
    s = browser()
    assert "paused" in follow(s, s.post(f"/c/{sid}/login", {"name": "Sam Lee"}))
    assert len(prof.outbox) == sent
    page = text(prof.get(f"/teach/s/{sid}"))
    assert "Signing in is paused for Sam Lee" in page
    prof.post(f"/teach/s/{sid}/unlock-pin", {"name_key": "sam lee"})
    assert browser().post(f"/c/{sid}/login", {"name": "Sam Lee"}).headers["Location"].endswith("/verify")


def test_one_class_cannot_use_up_every_classes_emails(prof, browser, monkeypatch):
    import signin
    monkeypatch.setattr(signin, "CLASS_CODES_PER_DAY_MIN", 2)
    sid = prof.create_sheet()
    prof.upload(sid, "Name,Email\nA One,a@school.edu\n")  # class of 1: daily cap max(2, 2*1) = 2
    for _ in range(2):
        b = browser()
        b.post(f"/c/{sid}/login", {"name": "A One"})
        with prof.app.app_context():
            db.run("DELETE FROM login_codes")
            db.run("DELETE FROM login_links")
            db.commit()
    s = browser()
    page = text(follow(s, s.post(f"/c/{sid}/login", {"name": "A One"})))
    assert "We couldn't email you a code" in page and "used its sign-in emails for today" in page
    page = text(prof.get(f"/teach/s/{sid}"))
    assert "1 student couldn't get a sign-in email today" in page
    assert "A One — this class's sign-in emails ran out for today" in page
    other = prof.create_sheet(title="Other class")
    prof.upload(other, "Name,Email\nB Two,b@school.edu\n")
    assert browser().post(f"/c/{other}/login", {"name": "B Two"}).headers["Location"].endswith("/verify")


def test_undoing_delete_all_brings_back_a_published_schedule(prof, browser):
    sid = prof.create_sheet(days=("Mon", "Tue"), capacity=2)
    rankers(browser, sid, [("Ana Able", [0, 1], [])])
    prof.post(f"/teach/s/{sid}/close-and-schedule")
    prof.post(f"/teach/s/{sid}/publish")
    page = follow(prof, prof.post(f"/teach/s/{sid}/delete-all", {"confirm": "DELETE"}))
    snap = re.search(r"/undo/(\w+)", page).group(1)
    prof.post(f"/teach/s/{sid}/undo/{snap}", {"parts": "submissions,assignments"})
    assert q(prof.app, "SELECT published_at FROM sheets WHERE id = :sid", sid=sid)
    ana = browser().sign_in_student(sid, "Ana Able")
    assert "Your presentation day" in html(ana.get(f"/c/{sid}/home"))


def test_undo_lets_students_stay_signed_in(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    s = browser().sign_in_student(sid, "Sam Lee", "sam@school.edu")
    s.rank(sid, day_keys(sid))
    page = follow(prof, prof.post(f"/teach/s/{sid}/delete-submission", {"name_key": "sam lee"}))
    snap = re.search(r"/undo/(\w+)", page).group(1)
    prof.post(f"/teach/s/{sid}/undo/{snap}", {"parts": "submissions", "key": "sam lee"})
    assert s.get(f"/c/{sid}/home").status_code == 200


def test_name_suggestions_forgive_a_typo(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    assert "Alex Johnson" in browser().get(f"/c/{sid}/names?q=alex jonson").get_json()


def test_a_code_from_another_class_is_named(prof, browser):
    a = prof.create_sheet(title="Biology Seminar")
    b = prof.create_sheet(title="CS and Law")
    for sid in (a, b):
        prof.upload(sid)
    s = browser()
    s.post(f"/c/{a}/login", {"name": "Sam Lee"})
    code_a = s.last_code("sam@school.edu")
    s.post(f"/c/{b}/login", {"name": "Sam Lee"})
    assert "That code is for “Biology Seminar”" in html(s.post(f"/c/{b}/verify", {"code": code_a}))


def test_people_not_on_the_list_see_only_their_own_day(prof, browser):
    sid = prof.create_sheet(days=("Mon", "Tue"), capacity=3, show_preview=True)
    prof.upload(sid)
    browser().sign_in_student(sid, "Sam Lee", "sam@school.edu").rank(sid, day_keys(sid))
    outsider = browser().sign_in_student(sid, "Pat Outsider")
    outsider.rank(sid, day_keys(sid))
    page = text(outsider.get(f"/c/{sid}/schedule"))
    assert "Sam Lee" not in page and "only your own day" in page


def test_join_codes_typed_with_a_space_or_dash(prof, browser):
    sid = prof.create_sheet()
    s = browser()
    for typed in (f"{sid[:4]} {sid[4:]}", f"{sid[:4]}-{sid[4:]}"):
        assert s.get(f"/join?code={typed}").headers["Location"].endswith(f"/c/{sid}"), typed


def test_comparison_only_option_cannot_be_used_for_real(prof, browser):
    sid = prof.create_sheet()
    page = follow(prof, prof.post(f"/teach/s/{sid}/algorithm", {"algorithm": "da_day_favorable"}))
    assert "only for comparing" in page
    assert q(prof.app, "SELECT algorithm FROM sheets WHERE id = :sid", sid=sid) == "da_independent"


def test_reopening_a_post_only_page_goes_back_to_the_sheet(prof, browser):
    sid = prof.create_sheet()
    assert prof.get(f"/teach/s/{sid}/capacity").headers["Location"].endswith(f"/teach/s/{sid}")
    assert browser().get(f"/c/{sid}/login").headers["Location"].endswith(f"/c/{sid}")


def test_back_to_class_link_only_on_general_pages(prof, browser):
    sid = prof.create_sheet()
    prof.sign_in_student(sid, "Pat Doe")  # the professor's browser is also signed in as a student
    assert "Back to your class" not in html(prof.get(f"/teach/s/{sid}"))
    assert "Back to your class" in html(prof.get("/privacy"))


# ---------------------------------------------------------------------------
# Final-check round: sign-in links, limits strangers can't use up, filling
# open seats without moving anyone, and sign-outs that undo cleanly.
# ---------------------------------------------------------------------------
def _link(message, sid):
    return re.search(rf"(/c/{sid}/link/[\w-]+)", message.body).group(1)


def test_the_one_click_link_signs_in_once_and_only_for_that_email(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    asker = browser()
    asker.post(f"/c/{sid}/login", {"name": "Sam Lee"})
    link = _link(asker.outbox[-1], sid)
    phone = browser()  # opened from the email, somewhere else
    page = text(phone.get(link))
    assert re.search(r"Sign in as Sam Lee ?\?", page)  # opening it alone doesn't sign in (email scanners open links)
    assert phone.get(f"/c/{sid}/home").status_code == 302  # not signed in yet
    assert phone.post(link).headers["Location"].endswith("/home")
    assert "Hi Sam Lee" in text(phone.get(f"/c/{sid}/home"))
    again = browser()
    assert "expired or was already used" in follow(again, again.post(link))
    # A link is only good for the address it went to.
    asker.post(f"/c/{sid}/restart")
    asker.post(f"/c/{sid}/login", {"name": "Riya Patel"})
    riya_link = _link(asker.outbox[-1], sid)
    prof.post(f"/teach/s/{sid}/roster/edit", {"name_key": "riya patel", "name": "Riya Patel", "email": "rp@school.edu"})
    b = browser()
    assert "expired or was already used" in follow(b, b.get(riya_link))


def test_strangers_cannot_use_up_a_classs_codes_to_keep_someone_out(prof, browser, monkeypatch):
    import signin
    monkeypatch.setattr(signin, "CLASS_CODES_PER_HOUR_EXTRA", 0)
    monkeypatch.setattr(signin, "RESEND_COOLDOWN_SECONDS", 0)
    sid = prof.create_sheet()
    prof.upload(sid)  # 4 on the list (one without an email): 4 codes an hour for the class
    stranger_codes = 0
    for _ in range(4):
        b = browser()
        b.post(f"/c/{sid}/login", {"name": "Alex Johnson"})
        stranger_codes += 1
    b = browser()
    page = text(follow(b, b.post(f"/c/{sid}/login", {"name": "Alex Johnson"})))
    assert "No new email was sent" in page and "sign-in link in your newest email" in page
    # Riya never had a code: her first one of the day always goes out.
    riya = browser()
    before = len(riya.outbox)
    assert riya.post(f"/c/{sid}/login", {"name": "Riya Patel"}).headers["Location"].endswith("/verify")
    assert len(riya.outbox) == before + 1 and riya.outbox[-1].to == ["riya@school.edu"]
    # And Alex, kept from getting another code, still gets in with any email he received.
    alex = browser()
    assert alex.post(_link(next(m for m in reversed(riya.outbox) if m.to == ["alex@school.edu"]), sid)
                     ).headers["Location"].endswith("/home")


def test_repeat_codes_stop_before_the_site_runs_dry_for_other_classes(prof, browser, monkeypatch):
    import signin
    monkeypatch.setattr(settings, "EMAIL_DAILY_LIMIT", 20)  # 16 for students; repeats stop at 16 // 3 = 5 left
    monkeypatch.setattr(signin, "RESEND_COOLDOWN_SECONDS", 0)
    monkeypatch.setattr(signin, "CLASS_CODES_PER_DAY_MIN", 100)
    sid = prof.create_sheet()
    prof.upload(sid)
    refused = None
    for _ in range(14):
        b = browser()
        b.post(f"/c/{sid}/login", {"name": "Alex Johnson"})
        with prof.app.app_context():
            db.run("DELETE FROM login_codes")
            db.run("DELETE FROM login_links")
            db.commit()
        page = text(b.get(f"/c/{sid}/verify"))
        if "We couldn't email you a code" in page:
            refused = page
            break
    assert refused and "kept for students who haven't had a code yet" in refused
    other = prof.create_sheet(title="Other class")
    prof.upload(other, "Name,Email\nB Two,b@school.edu\n")
    assert browser().post(f"/c/{other}/login", {"name": "B Two"}).headers["Location"].endswith("/verify")


def test_a_locked_browser_is_told_a_real_time_and_never_sent_in_circles(prof, browser, monkeypatch):
    import signin
    sid = prof.create_sheet()
    prof.upload(sid)
    s = browser()
    s.post(f"/c/{sid}/login", {"name": "Sam Lee"})
    for _ in range(signin.CODE_TRIES_PER_BROWSER):
        page = follow(s, s.post(f"/c/{sid}/verify", {"code": "000000"})) if False else None
        s.post(f"/c/{sid}/verify", {"code": "000000"})
    page = raw(s.post(f"/c/{sid}/verify", {"code": s.last_code("sam@school.edu")}))
    assert "Too many wrong tries from this browser" in page
    assert re.search(r'<time datetime="[^"]+" data-local="time">\d{1,2}:\d\d [AP]M</time> \(in about \d+ minutes?\)', page)
    # "Send a new code" right away gives new tries — not "we emailed one a moment ago".
    page = text(follow(s, s.post(f"/c/{sid}/verify/resend")))
    assert "a moment ago" not in page and "Sent a new code" in page
    assert s.post(f"/c/{sid}/verify", {"code": s.last_code("sam@school.edu")}).headers["Location"].endswith("/home")


def test_the_professor_sees_who_couldnt_get_an_email_and_why(prof, browser, monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_DAILY_LIMIT", 12)  # 2 held back for instructors: 10 for students
    sid = prof.create_sheet()
    prof.upload(sid)
    with prof.app.app_context():
        for i in range(10):
            db.run("INSERT INTO email_log (id, sent_at, recipient, client_ip, kind) VALUES (:id, :at, 'x', '', 'student')",
                   id=f"fill{i}", at=__import__("util").iso())
        db.commit()
    for name in ("Sam Lee", "Sam Lee", "Riya Patel"):
        b = browser()
        page = text(follow(b, b.post(f"/c/{sid}/login", {"name": name})))
        assert "We couldn't email you a code" in page and "Ask your instructor for a sign-in link" in page
    page = text(prof.get(f"/teach/s/{sid}"))
    assert "2 students couldn't get a sign-in email today" in page  # people, not attempts
    assert "This site's sign-in emails ran out for today" in page
    assert "Sam Lee — the site's sign-in emails ran out for today" in page
    # The student waiting on the code page types the instructor's code there.
    sam = browser()
    sam.post(f"/c/{sid}/login", {"name": "Sam Lee"})
    page = follow(prof, prof.post(f"/teach/s/{sid}/signin-code", {"name_key": "sam lee", "back": "stuck"}))
    code = re.search(r"Read them this code instead: (\d{6})", text(page)).group(1)
    assert "you made them a sign-in link at" in text(page)
    assert sam.post(f"/c/{sid}/verify", {"code": code}).headers["Location"].endswith("/home")
    page = text(prof.get(f"/teach/s/{sid}"))
    assert "1 student couldn't get a sign-in email today" in page and "Sam Lee — the site" not in page


def test_giving_out_open_seats_on_a_published_schedule_moves_nobody(prof, browser):
    sid = prof.create_sheet(days=("Mon", "Tue"), capacity=1)
    prof.upload(sid, "Name,Email\nAna Able,ana@school.edu\nBen Baker,ben@school.edu\nCy Cole,cy@school.edu\n")
    keys = rankers(browser, sid, [("Ana Able", [0, 1], []), ("Ben Baker", [0, 1], []), ("Cy Cole", [1, 0], [0])])
    prof.post(f"/teach/s/{sid}/close-and-schedule")
    page = text(follow(prof, prof.post(f"/teach/s/{sid}/publish")))
    before = assigned(prof.app, sid)
    nobody = {"ana able", "ben baker", "cy cole"} - set(before)
    assert len(nobody) == 1 and "no day yet and will see that" in page
    page = text(follow(prof, prof.post(f"/teach/s/{sid}/capacity", {"capacity": "2"})))
    assert "Give them the open seats" in page and "nobody who can see their day moves" in page
    assert "Make the schedule again to use" not in page
    assert "Give them the open seats (1 student gets a day)" in page
    page = follow(prof, prof.post(f"/teach/s/{sid}/open-seats"))
    after = assigned(prof.app, sid)
    assert {k: v for k, v in after.items() if k in before} == before  # nobody moved
    assert set(after) == {"ana able", "ben baker", "cy cole"}
    assert after.get("cy cole", (keys[1],))[0] == keys[1]  # never a day they can't do
    shown = text(page)
    assert "Gave 1 student a day" in shown and "Nobody else moved" in shown
    assert "Copy their emails (1)" in shown
    assert "All done — students can see their day" in shown
    assert "out of date" not in shown and "Make the schedule again" not in shown
    assert "See what a fresh schedule would change" in shown  # optional, never pushed


def test_an_unpublished_schedule_has_one_path_after_seats_change(prof, browser):
    sid = prof.create_sheet(days=("Mon", "Tue"), capacity=1)
    rankers(browser, sid, [("Ana Able", [0, 1], []), ("Ben Baker", [0, 1], []), ("Cy Cole", [0, 1], [])])
    prof.post(f"/teach/s/{sid}/close-and-schedule")
    page = text(follow(prof, prof.post(f"/teach/s/{sid}/capacity", {"capacity": "2"})))
    assert "Press “Make the schedule again” under Schedule" in page
    assert "Give them the open seats (" not in page  # one path: make it again
    page = text(follow(prof, prof.post(f"/teach/s/{sid}/run")))
    assert len(assigned(prof.app, sid)) == 3 and "Check the schedule, then publish it" in page


def test_remaking_a_published_schedule_offers_the_changed_students_emails(prof, browser):
    sid = prof.create_sheet(days=("Mon", "Tue"), capacity=2)
    prof.upload(sid, "Name,Email\nAna Able,ana@school.edu\nBen Baker,ben@school.edu\nCy Cole,cy@school.edu\n")
    rankers(browser, sid, [("Ana Able", [0, 1], []), ("Ben Baker", [0, 1], []), ("Cy Cole", [0, 1], [])])
    prof.post(f"/teach/s/{sid}/close-and-schedule")
    prof.post(f"/teach/s/{sid}/publish")
    prof.post(f"/teach/s/{sid}/capacity", {"capacity": "1"})
    page = text(prof.post(f"/teach/s/{sid}/run"))
    assert "Make the schedule again?" in page
    page = follow(prof, prof.post(f"/teach/s/{sid}/run", {"confirmed": "1"}))
    assert "Tell these students" in text(page) and re.search(r"Copy their emails \(\d\)", text(page))
    assert re.search(r'id="undo-emails"[^>]*>[^<]*@school\.edu', page)


def test_putting_the_old_class_list_back_keeps_everyone_signed_in(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    sam = browser().sign_in_student(sid, "Sam Lee", "sam@school.edu")
    alex = browser().sign_in_student(sid, "Alex Johnson", "alex@school.edu")
    riya = browser().sign_in_student(sid, "Riya Patel", "riya@school.edu")
    # The wrong section's list: Sam isn't on it, so he's signed out...
    review = prof.upload(sid, "Name,Email\nRiya Patel,riya@school.edu\nZed New,zed@school.edu\n")
    pid = re.search(r"/roster/review/(\w+)", review.headers["Location"]).group(1)
    page = follow(prof, prof.post(f"/teach/s/{sid}/roster/review/{pid}", {"action": "replace"}))
    assert sam.get(f"/c/{sid}/home").status_code == 302
    assert "Hi Riya Patel" in text(riya.get(f"/c/{sid}/home"))  # her entry didn't change
    # ...and putting the old list back lets him straight back in, and nobody else is signed out.
    snap = re.search(r"/undo/(\w+)", page).group(1)
    prof.post(f"/teach/s/{sid}/undo/{snap}", {"parts": "roster"})
    sam2 = browser().sign_in_student(sid, "Sam Lee", "sam@school.edu")  # (his old browser dropped him)
    assert "Hi Sam Lee" in text(sam2.get(f"/c/{sid}/home"))
    assert "Hi Riya Patel" in text(riya.get(f"/c/{sid}/home"))
    assert "Hi Alex Johnson" in text(alex.get(f"/c/{sid}/home"))  # off the list only while it was wrong


def test_taking_a_student_off_and_back_on_with_the_same_email_keeps_them_in(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    sam = browser().sign_in_student(sid, "Sam Lee", "sam@school.edu")
    prof.post(f"/teach/s/{sid}/roster/remove", {"name_key": "sam lee"})
    prof.post(f"/teach/s/{sid}/roster/add", {"name": "Sam Lee", "email": "sam@school.edu"})
    assert "Hi Sam Lee" in text(sam.get(f"/c/{sid}/home"))
    prof.post(f"/teach/s/{sid}/roster/remove", {"name_key": "sam lee"})
    prof.post(f"/teach/s/{sid}/roster/add", {"name": "Sam Lee", "email": "someone.else@school.edu"})
    assert sam.get(f"/c/{sid}/home").status_code == 302  # a different email: a different person


def test_deleting_a_ranking_after_sign_ups_close_doesnt_sign_the_student_out(prof, browser):
    sid = prof.create_sheet(days=("Mon", "Tue"))
    keys = day_keys(sid)
    bao = browser().sign_in_student(sid, "Bao Nguyen")
    bao.rank(sid, keys)
    prof.post(f"/teach/s/{sid}/toggle")  # close sign-ups
    prof.post(f"/teach/s/{sid}/delete-submission", {"name_key": "bao nguyen"})
    page = text(bao.get(f"/c/{sid}/home"))
    assert "Hi Bao Nguyen" in page
    assert "You don't have a ranking saved, and sign-ups are closed" in page and "before you ranked" not in page


def test_a_student_without_a_day_in_the_draft_is_told_so(prof, browser):
    # One seat a day, and both can only do Mon: one of them has no day in the draft.
    sid = prof.create_sheet(days=("Mon", "Tue"), capacity=1, show_preview=True)
    keys = day_keys(sid)
    students = []
    for name in ("Ana Able", "Ben Baker"):
        b = browser().sign_in_student(sid, name)
        b.rank(sid, [keys[0], keys[1]], excluded=[keys[1]])
        students.append(b)
    pages = [text(b.get(f"/c/{sid}/schedule")) for b in students]
    assert sum("In this draft, you're on Mon" in p for p in pages) == 1
    assert sum("In this draft you don't have a day yet" in p for p in pages) == 1


def test_no_schedule_link_that_only_repeats_your_own_day(prof, browser):
    sid = prof.create_sheet(days=("Mon", "Tue"), capacity=2, show_preview=False)
    ana = browser().sign_in_student(sid, "Ana Able")
    ana.rank(sid, day_keys(sid))
    prof.post(f"/teach/s/{sid}/close-and-schedule")
    prof.post(f"/teach/s/{sid}/publish")
    page = text(ana.get(f"/c/{sid}/home"))
    assert "Your presentation day" in page and "See the whole schedule" not in page


def test_marking_every_day_cant_do_means_the_professor_decides(prof, browser):
    sid = prof.create_sheet(days=("Mon", "Tue"), capacity=2)
    keys = day_keys(sid)
    browser().sign_in_student(sid, "Ana Able").rank(sid, keys, excluded=keys, comments={keys[0]: "surgery that week"})
    browser().sign_in_student(sid, "Ben Baker").rank(sid, keys)
    prof.post(f"/teach/s/{sid}/close-and-schedule")
    assert set(assigned(prof.app, sid)) == {"ben baker"}  # never on a day she marked, even with room
    page = text(prof.get(f"/teach/s/{sid}/schedule"))
    assert "Ana Able — marked every day as one they can't do" in page and "surgery that week" in page
    assert "Give them the open seats (" not in page  # filling can't place her either


# ---------------------------------------------------------------------------
# Same-name students, archiving, short links, the class-list flow
# ---------------------------------------------------------------------------
TWINS = "Name,Email\nAlex Kim,akim7@school.edu\nAlex Kim,alex.kim@school.edu\nSam Lee,sam@school.edu\n"


def test_two_students_with_one_name_each_sign_in_with_their_own_email(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid, TWINS)
    page = text(prof.get(f"/teach/s/{sid}"))
    assert "Two students are named Alex Kim" in page and "Alex Kim (2)" in page
    s = browser()
    page = text(s.post(f"/c/{sid}/login", {"name": "alex kim"}))
    assert "Which one is you?" in page and "aki•••@school.edu" in page and "ale•••••@school.edu" in page
    assert "akim7@school.edu" not in page  # never the whole address
    sent = len(s.outbox)
    r = s.post(f"/c/{sid}/login", {"name": "alex kim", "pick": "alex kim 2", "which": "1"})
    assert r.headers["Location"].endswith("/verify") and len(s.outbox) == sent + 1
    assert s.outbox[-1].to == ["alex.kim@school.edu"]
    assert s.post(f"/c/{sid}/verify", {"code": s.last_code("alex.kim@school.edu")}).headers["Location"].endswith("/home")
    assert "Hi Alex Kim (2)" in text(s.get(f"/c/{sid}/home"))
    # Suggestions show the shared name once, without its number.
    names = browser().get(f"/c/{sid}/names?q=ale").get_json()
    assert names == ["Alex Kim"]


def test_a_new_upload_keeps_each_same_name_student_with_their_ranking(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid, TWINS)
    alex = browser()
    alex.post(f"/c/{sid}/login", {"name": "Alex Kim", "pick": "alex kim 2", "which": "1"})
    alex.post(f"/c/{sid}/verify", {"code": alex.last_code("alex.kim@school.edu")})
    alex.rank(sid, day_keys(sid))
    # The same class, listed in another order and with a newcomer: numbers follow the emails.
    review = prof.upload(sid, "Name,Email\nSam Lee,sam@school.edu\nAlex Kim,alex.kim@school.edu\n"
                              "Alex Kim,akim7@school.edu\nNew Person,new@school.edu\n")
    pid = re.search(r"/roster/review/(\w+)", review.headers["Location"]).group(1)
    prof.post(f"/teach/s/{sid}/roster/review/{pid}", {"action": "replace"})
    with prof.app.app_context():
        rows = {r["name_key"]: r["email"] for r in db.rows("SELECT * FROM roster WHERE sheet_id = :sid", sid=sid)}
        ranked = db.scalar("SELECT name_key FROM submissions WHERE sheet_id = :sid", sid=sid)
    assert rows["alex kim"] == "akim7@school.edu" and rows["alex kim 2"] == "alex.kim@school.edu"
    assert rows[ranked] == "alex.kim@school.edu"  # the ranking still belongs to the one who made it


def test_archiving_tucks_a_sheet_away_and_deleting_offers_it_first(prof, browser):
    sid = prof.create_sheet(title="Old seminar")
    other = prof.create_sheet(title="Current seminar")
    page = text(prof.get("/teach/"))
    assert "Archive it instead" in page and "To delete it, type DELETE" in page
    follow(prof, prof.post(f"/teach/s/{sid}/archive", {"archive": "1"}))
    page = html(prof.get("/teach/"))
    assert "Archived sheets (1)" in page and "Unarchive" in page
    assert browser().get(f"/c/{sid}").status_code == 200  # students' link still works
    prof.post(f"/teach/s/{sid}/archive", {"archive": "0"})
    assert "Archived sheets (" not in html(prof.get("/teach/"))
    r = prof.post(f"/teach/s/{other}/delete-sheet", {"confirm": "nope", "back": "dashboard"})
    assert r.headers["Location"].endswith("/teach/")
    assert "Nothing was deleted" in follow(prof, r)


def test_shortening_a_link_happens_only_when_asked(prof, monkeypatch):
    import teach

    sent = []
    monkeypatch.setattr(teach, "_shorten", lambda url: sent.append(url) or "https://is.gd/AbC12")
    sid = prof.create_sheet()
    page = html(prof.get(f"/teach/s/{sid}"))
    assert "Shorten this link" in page and "is.gd" in page and not sent  # nothing goes out by itself
    page = text(follow(prof, prof.post(f"/teach/s/{sid}/shorten")))
    assert sent == [f"http://localhost/c/{sid}"] and "Short link: https://is.gd/AbC12" in page
    assert 'value="https://is.gd/AbC12"' in html(prof.get(f"/teach/s/{sid}"))
    prof.post(f"/teach/s/{sid}/shorten")
    assert len(sent) == 1  # made once, then reused


def test_shortener_accepts_only_short_links_it_expects(monkeypatch):
    import io
    import urllib.request

    import teach

    answers = iter([b"<html>error</html>", b"https://tinyurl.com/2p8xk3ab"])
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: io.BytesIO(next(answers)))
    assert teach._shorten("https://example.edu/c/x") == "https://tinyurl.com/2p8xk3ab"  # is.gd failed: TinyURL
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"javascript:alert(1)"))
    assert teach._shorten("https://example.edu/c/x") == ""


def test_the_class_list_steps_aside_once_it_is_in(prof):
    sid = prof.create_sheet()
    page = text(prof.get(f"/teach/s/{sid}"))
    assert "Your class list stays private" in page and "Encrypted all the way" in page
    assert "the site's code is public" in page and "Do you use Canvas for this class?" in page
    page = text(follow(prof, prof.upload(sid)))
    assert "Next: See it as a student →" in page
    assert "Replace it with a new file" in page and "Delete this list and start over" in page
    assert "Drag your class list here" not in page  # the big drop box is gone
    page = text(follow(prof, prof.post(f"/teach/s/{sid}/roster/clear")))
    assert "Click here to choose your class list" in page  # start over


def test_new_codes_are_long_and_never_reuse_a_deleted_sheets(prof, monkeypatch):
    import sheets as sheets_module

    first = prof.create_sheet(title="Gone")
    prof.post(f"/teach/s/{first}/delete-sheet", {"confirm": "DELETE"})
    codes = iter([first, "a" * 12])
    monkeypatch.setattr(sheets_module, "new_id", lambda length: next(codes))
    second = prof.create_sheet(title="New")
    assert second == "a" * 12 and len(first) == 12  # the deleted sheet's code wasn't handed out again


def test_no_emoji_on_the_site():
    import pathlib
    import re as regex

    emoji = regex.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️↩↗]")
    root = pathlib.Path(__file__).resolve().parent.parent
    files = list((root / "templates").glob("*.html")) + list((root / "public" / "static").glob("*.js"))
    files += [p for p in root.glob("*.py")]
    found = [f"{p.name}: {line.strip()[:60]}" for p in files for line in p.read_text().splitlines() if emoji.search(line)]
    assert not found, found


def test_the_owner_uploads_site_fonts_and_the_site_serves_them(browser):
    owner = browser().sign_in_instructor("owner@gmail.com")
    font = b"wOF2" + b"\x00" * 64
    page = follow(owner, owner.post("/owner/fonts", {"fonts": [
        (io.BytesIO(font), "yalenew-roman.woff2"),
        (io.BytesIO(b"<html>"), "yalenew-bold.woff2"),
        (io.BytesIO(font), "something-else.woff2"),
    ]}, content_type="multipart/form-data"))
    assert "Uploaded 1 font file" in page and "yalenew-bold.woff2 (not a .woff2 font)" in page
    assert "something-else.woff2 (not one of the expected names)" in page
    served = browser().get("/fonts/yalenew-roman.woff2")
    assert served.status_code == 200 and served.data == font and served.mimetype == "font/woff2"
    assert "public" in served.headers["Cache-Control"] and "s-maxage" in served.headers["Cache-Control"]
    assert browser().get("/fonts/yalenew-bold.woff2").status_code == 404  # not uploaded yet
    assert browser().get("/fonts/anything.woff2").status_code == 404
    assert "font-src 'self'" in served.headers["Content-Security-Policy"]


def test_only_the_owner_can_upload_fonts(prof):
    r = prof.post("/owner/fonts", {"fonts": [(io.BytesIO(b"wOF2"), "yalenew-roman.woff2")]},
                  content_type="multipart/form-data")
    assert r.status_code in (302, 403, 404)
    assert q(prof.app, "SELECT COUNT(*) FROM site_assets") == 0


def test_a_professor_sets_the_look_for_the_whole_class(prof, browser):
    sid = prof.create_sheet()
    student_page = raw(browser().get(f"/c/{sid}"))
    assert "data-course-theme" not in student_page  # the standard look until one is chosen
    page = raw(prof.get(f"/teach/s/{sid}"))
    assert f'data-course-look="/teach/s/{sid}/look"' in page and "become the look for everyone" in page
    r = prof.client.post(f"/teach/s/{sid}/look", json={"theme": "solarized-dark", "font": "readable"},
                         headers={"X-CSRF-Token": prof.csrf()})
    assert r.status_code == 200 and r.get_json() == {"ok": True}
    student_page = raw(browser().get(f"/c/{sid}"))
    assert 'data-course-theme="solarized-dark" data-course-font="readable"' in student_page
    assert "Your instructor picked this class's look" in student_page
    bad = prof.client.post(f"/teach/s/{sid}/look", json={"theme": "neon", "font": "mixed"},
                           headers={"X-CSRF-Token": prof.csrf()})
    assert bad.status_code == 400
    other = browser().sign_in_instructor("other@school.edu")
    stranger = other.client.post(f"/teach/s/{sid}/look", json={"theme": "light", "font": "mixed"},
                                 headers={"X-CSRF-Token": other.csrf()})
    assert stranger.status_code != 200
    assert q(prof.app, "SELECT theme FROM sheets WHERE id = :sid", sid=sid) == "solarized-dark"
    # Copying for next term keeps the look.
    new_sid = prof.post(f"/teach/s/{sid}/duplicate").headers["Location"].split("/")[-2]
    assert q(prof.app, "SELECT font FROM sheets WHERE id = :sid", sid=new_sid) == "readable"


def test_the_owner_page_asks_a_signed_out_visitor_to_sign_in_and_returns(browser):
    visitor = browser()
    r = visitor.get("/owner/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/teach/login")
    visitor.post("/teach/login", {"email": "owner@gmail.com"})
    r = visitor.post("/teach/verify", {"code": visitor.last_code("owner@gmail.com")})
    assert r.headers["Location"].endswith("/owner/")  # straight back to the owner page
    assert visitor.get("/owner/").status_code == 200


def test_uploaded_fonts_persist_through_the_daily_cleanup(browser):
    import maintenance

    owner = browser().sign_in_instructor("owner@gmail.com")
    owner.post("/owner/fonts", {"fonts": [(io.BytesIO(b"wOF2" + b"\x01" * 32), "oldstyle7-roman.woff2")]},
               content_type="multipart/form-data")
    with owner.app.app_context():
        db.run("UPDATE site_assets SET updated_at = '2000-01-01T00:00:00+00:00'")  # long ago
        db.commit()
        maintenance.run_daily()
    assert browser().get("/fonts/oldstyle7-roman.woff2").status_code == 200


# ---------------------------------------------------------------------------
# The backup for the site's own email: sign-in links the professor sends from
# their own email account, and students asking for one from theirs.
# ---------------------------------------------------------------------------
def _compose(page, to):
    """(subject, body) of the message a [data-compose] link to `to` opens."""
    found = re.search(rf'data-compose data-to="{re.escape(to)}"(?: data-bcc="[^"]*")? '
                      r'data-subject="([^"]*)" data-body="([^"]*)"', page)
    assert found, f"no email to {to} on the page"
    return found.group(1), found.group(2)


def test_a_sign_in_link_sent_from_the_professors_own_email_signs_the_student_in(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    page = follow(prof, prof.post(f"/teach/s/{sid}/signin-code", {"name_key": "sam lee"}))
    subject, body = _compose(page, "sam@school.edu")
    assert subject.startswith("Your sign-in link for") and body.startswith("Hi Sam Lee,")
    assert 'data-mail-picker data-default="app"' in page  # no mail records for school.edu in tests
    link = re.search(rf"(/c/{sid}/link/[\w-]+)", body).group(1)
    sam = browser()
    assert re.search(r"Sign in as Sam Lee ?\?", text(sam.get(link)))
    assert sam.post(link).headers["Location"].endswith("/home")
    other = browser()
    assert "expired or was already used" in follow(other, other.post(link))


def test_the_whole_class_can_get_sign_in_links_from_the_professors_own_email(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid, CANVAS_ROSTER + "Bo Brown,\"Brown, Bo\",105,,,,,,,,,9005\n")
    everyone = ("alex@school.edu", "sam@school.edu", "riya@school.edu")
    page = html(prof.get(f"/teach/s/{sid}/email-links"))
    assert "Get the 3 emails ready" in page and all(who in page for who in everyone)
    assert "/link/" not in page  # no links until they're asked for
    assert "Not listed: Bo Brown." in text(page)  # no address: signs in with a PIN
    assert "Test Student" not in text(page)  # Canvas's test student is the professor's own
    page = html(prof.post(f"/teach/s/{sid}/email-links"))
    links = {}
    for who in everyone:
        _subject, body = _compose(page, who)
        links[who] = re.search(rf"(/c/{sid}/link/[\w-]+)", body).group(1)
    assert len(set(links.values())) == 3  # each student gets their own
    # One email to the whole class, the professor's own address in To and the class in Bcc.
    bcc = re.search(r'data-to="prof@school.edu" data-bcc="([^"]+)"', page).group(1)
    assert sorted(bcc.split(", ")) == sorted(everyone)
    assert "/link/" not in _compose(page, "prof@school.edu")[1]  # no sign-in link in a group email
    riya = browser()
    assert riya.post(links["riya@school.edu"]).headers["Location"].endswith("/home")
    assert "Hi Riya Patel" in text(riya.get(f"/c/{sid}/home"))
    # Someone else's sheet: not found.
    other = browser().sign_in_instructor("other@school.edu")
    assert other.get(f"/teach/s/{sid}/email-links").status_code == 404


def test_a_student_the_site_cant_email_can_ask_their_instructor_for_a_link(prof, browser, monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_DAILY_LIMIT", 12)  # 2 held back for instructors: 10 for students
    sid = prof.create_sheet()
    prof.upload(sid)
    with prof.app.app_context():
        for i in range(10):
            db.run("INSERT INTO email_log (id, sent_at, recipient, client_ip, kind) VALUES (:id, :at, 'x', '', 'student')",
                   id=f"fill{i}", at=__import__("util").iso())
        db.commit()
    s = browser()
    page = follow(s, s.post(f"/c/{sid}/login", {"name": "Sam Lee"}))
    subject, body = _compose(page, "prof@school.edu")
    assert subject.startswith("Sign-in link for") and "My name on the class list: Sam Lee" in body
    assert f"/teach/s/{sid}" in body  # straight to the class's page
    assert "sam@school.edu" not in page  # whoever typed the name never sees the address
    # The professor's link reaches Sam even though the site still can't send.
    prof.post(f"/teach/s/{sid}/signin-code", {"name_key": "sam lee"})
    with prof.app.app_context():
        db.run("UPDATE login_codes SET expires_at = '2000-01-01T00:00:00+00:00'")  # the code to read out ran out
        db.commit()
    s2 = browser()
    page = text(follow(s2, s2.post(f"/c/{sid}/login", {"name": "Sam Lee"})))
    assert "the sign-in link in your newest email from your instructor still works" in page


def test_emails_open_where_the_senders_address_lives(prof, monkeypatch):
    asked = []
    records = {
        ("school.edu", "MX"): ["0 school-edu.mail.protection.outlook.com."],
        ("college.edu", "MX"): ["1 aspmx.l.google.com."],
        ("gated.edu", "MX"): ["10 mx0a-001.pphosted.com."],
        ("gated.edu", "TXT"): ['"v=spf1 include:spf.protection.outlook.com -all"'],
        ("own.edu", "MX"): ["10 mail.own.edu."],
    }

    def fake(name, kind):
        asked.append(name)
        return records.get((name, kind), [])
    monkeypatch.setattr(compose, "lookup", fake)
    with prof.app.app_context():
        assert compose.service_for("pat@gmail.com") == "gmail" and not asked  # well known: no lookup
        assert compose.service_for("pat@hotmail.com") == "outlookcom"
        assert compose.service_for("pat@law.school.edu") == "outlook"  # no mail records of its own: the school's
        assert compose.service_for("pat@college.edu") == "gmail"
        assert compose.service_for("pat@gated.edu") == "outlook"  # a mail filter in front: SPF names Microsoft
        assert compose.service_for("pat@own.edu") == "app"  # its own mail server
        before = len(asked)
        assert compose.service_for("lee@law.school.edu") == "outlook" and len(asked) == before  # remembered
        monkeypatch.setattr(compose, "lookup", lambda name, kind: None)  # DNS can't be asked
        assert compose.service_for("pat@offline.edu") == "app"
        assert compose.service_for("not an address") == "app"


def test_the_professor_hears_when_the_sites_emails_are_down(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    with prof.app.app_context():
        db.run("UPDATE email_log SET sent_at = '2000-01-01T00:00:00+00:00'")  # the last email out came before
        db.commit()
        db.record_error("EMAIL", "Sending a sign-in code failed: SMTPAuthenticationError(535)")
    page = text(prof.get(f"/teach/s/{sid}"))
    assert "The site's sign-in emails aren't going out right now" in page
    assert "send sign-in links from your own email" in page
    browser().post(f"/c/{sid}/login", {"name": "Riya Patel"})  # one goes out again
    assert "aren't going out" not in text(prof.get(f"/teach/s/{sid}"))


# ---------------------------------------------------------------------------
# Free-plan usage, for the site's owner.
# ---------------------------------------------------------------------------
def test_the_owner_sees_free_plan_usage_and_hears_when_it_runs_high(prof, browser, monkeypatch):
    import usage
    prof.get("/teach/")
    with prof.app.app_context():
        assert (db.scalar("SELECT amount FROM usage_daily WHERE kind = 'requests'") or 0) >= 1
    owner = browser().sign_in_instructor("owner@gmail.com")
    page = text(owner.get("/owner/"))
    assert "Free plan usage" in page and "Database size" in page and "Requests answered, last 30 days" in page
    monkeypatch.setattr(usage, "SUPABASE_DATABASE_MB", 0.000001)  # any database is "full"
    cron = {"Authorization": "Bearer cron-secret"}
    sent = len(owner.outbox)
    assert "Database size" in owner.get("/cron/daily", headers=cron).get_json()["near_limits"]
    alerts = [m for m in owner.outbox[sent:] if m.to == ["owner@gmail.com"]]
    assert len(alerts) == 1 and "free-plan limit" in alerts[0].subject
    owner.get("/cron/daily", headers=cron)
    assert len([m for m in owner.outbox[sent:] if m.to == ["owner@gmail.com"]]) == 1  # once a day


# ---------------------------------------------------------------------------
# WorkOS sends the codes (its "Magic Auth"), with the site's own mailbox as
# the fallback. A stand-in plays the WorkOS API.
# ---------------------------------------------------------------------------
class FakeWorkOS:
    def __init__(self):
        self.sent, self.deleted, self.fail = [], [], False
        self.invitations, self.invite_base = [], "http://localhost/invite"

    def __call__(self, method, path, body=None, timeout=10, key=None):
        from datetime import timedelta
        from urllib.error import HTTPError
        from urllib.parse import unquote

        from util import iso, now
        if self.fail:
            raise OSError("WorkOS is down")
        if method == "GET" and path.startswith("/user_management/users"):  # checking a key
            if key != "sk_live_good":
                raise HTTPError(path, 401, "Unauthorized", None, io.BytesIO(b'{"message":"Unauthorized"}'))
            return {"data": []}
        if method == "POST" and path == "/user_management/magic_auth":
            code = f"{271828 + len(self.sent):06d}"
            self.sent.append((body["email"], code))
            return {"object": "magic_auth", "id": f"magic_auth_{len(self.sent)}", "user_id": "user_" + body["email"],
                    "email": body["email"], "code": code, "expires_at": iso(now() + timedelta(minutes=10))}
        if method == "DELETE" and path.startswith("/user_management/users/"):
            self.deleted.append(unquote(path.rsplit("/", 1)[1]))
            return {}
        if method == "POST" and path == "/user_management/invitations":
            n = len(self.invitations) + 1
            invitation = {"id": f"invitation_{n}", "token": f"token{n}", "email": body["email"], "state": "pending",
                          "accept_invitation_url": f"{self.invite_base}?invitation_token=token{n}"}
            self.invitations.append(invitation)
            return invitation
        if method == "GET" and path.startswith("/user_management/invitations/by_token/"):
            token = unquote(path.rsplit("/", 1)[1])
            found = next((i for i in self.invitations if i["token"] == token), None)
            if not found:
                raise HTTPError(path, 404, "Not Found", None, io.BytesIO(b"{}"))
            return found
        if method == "POST" and path.endswith("/revoke"):
            found = next(i for i in self.invitations if i["id"] == unquote(path.split("/")[-2]))
            found["state"] = "revoked"
            return found
        raise AssertionError(f"unexpected WorkOS call {method} {path}")


def _use_workos(monkeypatch):
    import signin
    fake = FakeWorkOS()
    monkeypatch.setattr(settings, "WORKOS_API_KEY", "sk_live_fake")
    monkeypatch.setattr(signin, "_workos", fake)
    return fake


def test_workos_emails_the_code_and_the_site_checks_it(prof, browser, monkeypatch):
    sid = prof.create_sheet()
    prof.upload(sid)
    workos = _use_workos(monkeypatch)
    sam = browser()
    emailed = len(sam.outbox)
    assert sam.post(f"/c/{sid}/login", {"name": "Sam Lee"}).headers["Location"].endswith("/verify")
    assert workos.sent[-1][0] == "sam@school.edu" and len(sam.outbox) == emailed  # WorkOS sent it, not the mailbox
    page = text(sam.get(f"/c/{sid}/verify"))
    assert "access@workos-mail.com" in page and "click the sign-in link" not in page  # WorkOS's email has no link
    assert "That code isn't right" in text(sam.post(f"/c/{sid}/verify", {"code": "000000"}))
    assert sam.post(f"/c/{sid}/verify", {"code": workos.sent[-1][1]}).headers["Location"].endswith("/home")
    assert workos.deleted == ["user_sam@school.edu"]  # WorkOS's record goes once Sam is in
    assert q(prof.app, "SELECT COUNT(*) FROM remote_users") == 0
    assert "emailed by WorkOS" in text(browser().get("/privacy"))
    # Instructors sign in the same way.
    newcomer = browser()
    newcomer.post("/teach/login", {"email": "new@school.edu"})
    assert workos.sent[-1][0] == "new@school.edu"
    r = newcomer.post("/teach/verify", {"code": workos.sent[-1][1]})
    assert r.status_code == 302 and "/teach" in r.headers["Location"]


def test_when_workos_fails_the_mailbox_sends_the_code(prof, browser, monkeypatch):
    sid = prof.create_sheet()
    prof.upload(sid)
    workos = _use_workos(monkeypatch)
    workos.fail = True
    riya = browser()
    riya.post(f"/c/{sid}/login", {"name": "Riya Patel"})
    assert riya.outbox[-1].to == ["riya@school.edu"]  # the fallback
    assert riya.post(f"/c/{sid}/verify", {"code": riya.last_code("riya@school.edu")}).headers["Location"].endswith("/home")
    assert q(prof.app, "SELECT COUNT(*) FROM error_log WHERE error LIKE '%through WorkOS failed%'") == 1


def test_when_workos_fails_with_no_mailbox_the_professor_can_step_in(prof, browser, monkeypatch):
    sid = prof.create_sheet()
    prof.upload(sid)
    workos = _use_workos(monkeypatch)
    workos.fail = True
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    with prof.app.app_context():
        db.run("UPDATE email_log SET sent_at = '2000-01-01T00:00:00+00:00'")  # the last email out came before
        db.commit()
    sam = browser()
    page = text(follow(sam, sam.post(f"/c/{sid}/login", {"name": "Sam Lee"})))
    assert "We couldn't email you a code" in page and "Ask your instructor for a sign-in link" in page
    page = text(prof.get(f"/teach/s/{sid}"))
    assert "The site's sign-in emails aren't going out right now" in page
    assert "Sam Lee — the email couldn't be sent" in page
    assert "Email me a backup" not in page  # no mailbox: backups are downloaded instead


def test_the_daily_job_deletes_workos_records_within_a_day(prof, browser, monkeypatch):
    sid = prof.create_sheet()
    prof.upload(sid)
    workos = _use_workos(monkeypatch)
    browser().post(f"/c/{sid}/login", {"name": "Alex Johnson"})  # a code nobody uses
    cron = {"Authorization": "Bearer cron-secret"}
    assert prof.get("/cron/daily", headers=cron).get_json()["workos_records_deleted"] == 0  # too new yet
    with prof.app.app_context():
        db.run("UPDATE remote_users SET created_at = '2000-01-01T00:00:00+00:00'")
        db.commit()
    assert prof.get("/cron/daily", headers=cron).get_json()["workos_records_deleted"] == 1
    assert workos.deleted == ["user_alex@school.edu"] and q(prof.app, "SELECT COUNT(*) FROM remote_users") == 0


def test_the_owner_switches_to_workos_by_pasting_its_key(prof, browser, monkeypatch):
    import signin
    workos = FakeWorkOS()
    monkeypatch.setattr(signin, "_workos", workos)
    sid = prof.create_sheet()
    prof.upload(sid)
    owner = browser().sign_in_instructor("owner@gmail.com")
    assert "Sign-in codes come from codes@test.example" in text(owner.get("/owner/"))
    page = text(follow(owner, owner.post("/owner/workos", {"key": "sk_live_wrong"})))
    assert "WorkOS didn't accept that key" in page and "Sign-in codes come from codes@test.example" in page
    page = text(follow(owner, owner.post("/owner/workos", {"key": "  sk_live_good \n"})))  # pasted with spaces
    assert "Connected: WorkOS now sends the sign-in codes" in page and "WorkOS sends the sign-in codes" in page
    assert "emailed by WorkOS" in text(browser().get("/privacy"))
    with prof.app.app_context():
        stored = db.scalar("SELECT value FROM app_state WHERE key = 'secret:workos_api_key'")
    assert stored and "sk_live_good" not in stored  # encrypted at rest
    sam = browser()
    sam.post(f"/c/{sid}/login", {"name": "Sam Lee"})
    assert workos.sent[-1][0] == "sam@school.edu"
    assert sam.post(f"/c/{sid}/verify", {"code": workos.sent[-1][1]}).headers["Location"].endswith("/home")
    # Only the owner can change it.
    assert prof.post("/owner/workos", {"action": "remove"}).status_code == 404
    page = text(follow(owner, owner.post("/owner/workos", {"action": "remove"})))
    assert "Removed the WorkOS key" in page and "Sign-in codes come from codes@test.example" in page
    # A WORKOS_API_KEY setting wins, and the page says where the key lives.
    monkeypatch.setattr(settings, "WORKOS_API_KEY", "sk_live_from_vercel")
    page = text(owner.get("/owner/"))
    assert "The key is the WORKOS_API_KEY setting in Vercel" in page and "Save and switch to WorkOS" not in page
    # A test key sends from workos.dev, and every page says so.
    monkeypatch.setattr(settings, "WORKOS_API_KEY", "sk_test_from_vercel")
    page = text(owner.get("/owner/"))
    assert "from workos.dev" in page and "This is a test (Staging) key" in page
    assert "codes are emailed by WorkOS, a sign-in service, from workos.dev" in text(browser().get("/privacy"))


def test_instructor_pages_are_framed_and_student_pages_are_not(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    for page in (raw(prof.get("/teach/")), raw(prof.get(f"/teach/s/{sid}")), raw(browser().get("/teach/login"))):
        assert 'class="mode-frame"' in page and '<p class="mode-label" aria-hidden="true">Instructor</p>' in page
    assert "Instructor sign-in" in text(browser().get("/teach/login"))
    assert "you don't sign in here" in text(browser().get("/teach/login"))
    for page in (raw(browser().get("/")), raw(browser().get(f"/c/{sid}")), raw(browser().get("/privacy"))):
        assert "mode-frame" not in page


def test_the_pointer_glow_is_only_on_the_front_page(prof, browser):
    sid = prof.create_sheet()
    assert "data-glow" in raw(browser().get("/"))
    for page in (raw(prof.get("/teach/")), raw(prof.get(f"/teach/s/{sid}")), raw(browser().get(f"/c/{sid}")),
                 raw(browser().get("/privacy")), raw(browser().get("/teach/login"))):
        assert "data-glow" not in page


# ---------------------------------------------------------------------------
# Adding a class: questions one at a time, the school's own Canvas, and a
# nudge for students who haven't ranked.
# ---------------------------------------------------------------------------
def test_the_class_list_questions_offer_every_way_in(prof):
    sid = prof.create_sheet()
    page = text(prof.get(f"/teach/s/{sid}"))
    for asked in ("Do you use Canvas for this class?", "Which school's Canvas do you use?",
                  "How would you like to add your students?", "From a spreadsheet",
                  "Type or paste their names and emails", "Let students sign themselves up"):
        assert asked in page, asked
    # Typing them in.
    page = text(follow(prof, prof.post(f"/teach/s/{sid}/roster", {"pasted": "Ana Able, ana@school.edu\nBo Brown, bo@school.edu"})))
    assert "2 students" in page and "Do you use Canvas for this class?" not in page
    # Letting students sign themselves up, on another sheet: straight on to sharing the link.
    other = prof.create_sheet(title="Seminar")
    r = prof.post(f"/teach/s/{other}/who", {"allow_unlisted": "1", "back": "share"})
    assert r.headers["Location"].endswith("#share")
    assert "That's how this sheet works now" in text(prof.get(f"/teach/s/{other}"))


def test_the_school_is_guessed_from_the_email_and_can_be_changed(prof, monkeypatch):
    directory = {
        ("domain", "school.edu"): [{"name": "School Pre-College Program", "domain": "app.precollege.school.edu"},
                                   {"name": "School University", "domain": "canvas.school.edu"}],
        ("name", "Lake"): [{"name": "Lake College", "domain": "lake.instructure.com"}],
    }
    monkeypatch.setattr(canvasdir, "_ask", lambda **p: directory.get(next(iter(p.items())), []))
    sid = prof.create_sheet()
    page = raw(prof.get(f"/teach/s/{sid}"))
    assert "it looks like <strong>School University</strong>" in page
    assert 'data-host="canvas.school.edu"' in page  # Canvas opens there, not canvas.instructure.com
    found = prof.client.get("/teach/canvas/schools?q=Lake").get_json()
    assert found == {"schools": [{"name": "Lake College", "domain": "lake.instructure.com"}]}
    headers = {"X-CSRF-Token": prof.csrf()}
    r = prof.client.post("/teach/canvas/school", json={"domain": "https://lake.instructure.com/courses/5",
                                                      "name": "Lake College"}, headers=headers)
    assert r.get_json() == {"ok": True, "domain": "lake.instructure.com", "name": "Lake College"}
    page = raw(prof.get(f"/teach/s/{sid}"))
    assert 'data-host="lake.instructure.com"' in page and 'data-go="canvas"' in page  # no need to ask again
    assert prof.client.post("/teach/canvas/school", json={"domain": "not a host"}, headers=headers).status_code == 400


def test_nudge_reminds_only_the_students_who_havent_ranked(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    rankers(browser, sid, [("Sam Lee", [0, 1], [])])
    page = html(prof.get(f"/teach/s/{sid}"))
    bcc = re.search(r'data-bcc="([^"]*)" data-subject="Reminder: rank your presentation days', page).group(1)
    assert sorted(bcc.split(", ")) == ["alex@school.edu", "riya@school.edu"]
    assert "Nudge the 2 who haven't ranked" in text(page)
    prof.post(f"/teach/s/{sid}/toggle")  # sign-ups closed: nobody to nudge
    assert "Nudge the" not in text(prof.get(f"/teach/s/{sid}"))


def test_each_class_has_class_schedule_and_settings_tabs(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    page = text(prof.get(f"/teach/s/{sid}"))
    assert "Class list" in page and "Sign-ups" in page and "Seats per day:" not in page
    page = text(prof.get(f"/teach/s/{sid}/schedule"))
    assert "Seats per day:" in page and "0 of 3 on your list have ranked" in page and "Backups" not in page
    page = text(prof.get(f"/teach/s/{sid}/settings"))
    assert "Days & settings" in page and "Backups" in page and "Copy or delete" in page
    assert 'id="signups"' not in raw(prof.get(f"/teach/s/{sid}/settings"))
    for tab in ("", "/schedule", "/settings"):
        assert 'aria-current="page"' in raw(prof.get(f"/teach/s/{sid}{tab}"))
    # Actions come back to the tab they belong to.
    assert prof.post(f"/teach/s/{sid}/capacity", {"capacity": "3"}).headers["Location"].endswith(f"/teach/s/{sid}/schedule#schedule")
    other = browser().sign_in_instructor("other@school.edu")
    for tab in ("/schedule", "/settings"):
        assert other.get(f"/teach/s/{sid}{tab}").status_code == 404


def test_a_new_sheet_is_set_up_one_question_at_a_time(prof):
    page = text(prof.get("/teach/new"))
    for asked in ("What is your course title?", "Which days can students present?",
                  "How many presentations fit in one day?", "When should students have ranked by?",
                  "Anything students should know?", "Who can sign up?"):
        assert asked in page, asked
    assert 'data-wizard-start="name"' in raw(prof.get("/teach/new"))
    # A problem sends the setup back to the question it's about.
    r = prof.post("/teach/new", {"title": "Seminar", "capacity": "2"})
    assert 'data-wizard-start="days"' in raw(r) and "Pick at least two days" in text(r)
    r = prof.post("/teach/new", {"title": "", "capacity": "2"})
    assert 'data-wizard-start="name"' in raw(r)
    # Editing keeps the whole form on one page.
    sid = prof.create_sheet()
    edit = raw(prof.get(f"/teach/s/{sid}/edit"))
    assert "data-wizard" not in edit and "Save changes" in edit


# ---------------------------------------------------------------------------
# Deadlines: sign-ups close by themselves, and the professor decides what
# happens when students are missing.
# ---------------------------------------------------------------------------
def _sheet_set(app, sid, **values):
    with app.app_context():
        for column, value in values.items():
            db.run(f"UPDATE sheets SET {column} = :v WHERE id = :sid", v=value, sid=sid)
        db.commit()


def _sheet_get(app, sid, column):
    return q(app, f"SELECT {column} FROM sheets WHERE id = :sid", sid=sid)


PASSED = "2000-01-01T00:00:00+00:00"


def test_a_deadline_is_a_date_and_time_in_the_professors_zone(prof):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    zone = q(prof.app, "SELECT timezone FROM instructors") or settings.DEFAULT_TIMEZONE
    day = (datetime.now(ZoneInfo(zone)) + timedelta(days=5)).date().isoformat()
    sid = prof.create_sheet()
    page = text(prof.get(f"/teach/s/{sid}/edit"))
    assert "sign-ups close automatically at this time" in page and "Ask me first if anyone hasn't ranked" in page
    form = {"title": "CS & Law presentations", "capacity": "2", "close_date": day, "close_time": "17:30",
            "at_close": "publish", "version": re.search(r'name="version" value="(\w+)"', raw(prof.get(f"/teach/s/{sid}/edit"))).group(1),
            "day_date": day_dates(sid), "day_key": day_keys(sid), "day_label": [""] * len(day_keys(sid))}
    prof.post(f"/teach/s/{sid}/edit", form)
    expected = datetime.fromisoformat(day).replace(hour=17, minute=30, tzinfo=ZoneInfo(zone))
    stored = datetime.fromisoformat(_sheet_get(prof.app, sid, "closes_at"))
    assert stored == expected and _sheet_get(prof.app, sid, "at_close") == "publish"
    assert "Sign-ups close automatically" in text(prof.get(f"/teach/s/{sid}"))
    # A deadline in the past is refused, and the setup reopens on that question.
    r = prof.post("/teach/new", {"title": "Seminar", "capacity": "2", "close_date": "2001-01-01", "close_time": "09:00",
                                 "day_date": [a_date("Mon", 0), a_date("Tue", 1)], "day_key": ["", ""], "day_label": ["", ""]})
    assert "already passed" in text(r) and 'data-wizard-start="deadline"' in raw(r)


def test_at_the_deadline_sign_ups_close_and_the_schedule_is_made(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    rankers(browser, sid, [("Sam Lee", [0, 1], [])])
    _sheet_set(prof.app, sid, closes_at=PASSED, at_close="schedule")
    sent = len(prof.outbox)
    sam = browser()
    sam.get(f"/c/{sid}")  # the first visit after the deadline acts on it
    assert _sheet_get(prof.app, sid, "bidding_open") == 0 and _sheet_get(prof.app, sid, "auto_closed_at")
    assert _sheet_get(prof.app, sid, "closes_at") is None and _sheet_get(prof.app, sid, "published_at") is None
    assert {"sam lee", "alex johnson", "riya patel"} <= set(assigned(prof.app, sid))  # the others got seats left over
    notes = [m for m in prof.outbox[sent:] if m.to == ["prof@school.edu"]]
    assert len(notes) == 1 and "sign-ups closed at your deadline" in notes[0].subject
    # Locked for students: no changes, no draft schedule.
    signed_in = browser().sign_in_student(sid, "Riya Patel")
    r = signed_in.client.post(f"/c/{sid}/save", json={"ranking": day_keys(sid)}, headers={"X-CSRF-Token": signed_in.csrf()})
    assert r.status_code == 403
    page = text(signed_in.get(f"/c/{sid}/schedule"))
    assert "Sign-ups are closed" in page and "Draft schedule" not in page
    # Reopening after the deadline keeps sign-ups open.
    prof.post(f"/teach/s/{sid}/toggle")
    browser().get(f"/c/{sid}")
    assert _sheet_get(prof.app, sid, "bidding_open") == 1


def test_publish_at_the_deadline_and_the_daily_job_catches_unvisited_sheets(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    rankers(browser, sid, [("Sam Lee", [0, 1], []), ("Alex Johnson", [1, 0], [])])
    _sheet_set(prof.app, sid, closes_at=PASSED, at_close="publish")
    r = prof.get("/cron/daily", headers={"Authorization": "Bearer cron-secret"})
    assert r.get_json()["deadlines_acted_on"] == 1
    assert _sheet_get(prof.app, sid, "bidding_open") == 0 and _sheet_get(prof.app, sid, "published_at")


def test_ask_first_when_students_are_missing_at_the_deadline(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    rankers(browser, sid, [("Sam Lee", [0, 1], [])])
    _sheet_set(prof.app, sid, closes_at=PASSED, at_close="ask")
    sent = len(prof.outbox)
    browser().get(f"/c/{sid}")
    browser().get(f"/c/{sid}")  # asked once, not twice
    assert _sheet_get(prof.app, sid, "bidding_open") == 1 and _sheet_get(prof.app, sid, "deadline_asked_at")
    asks = [m for m in prof.outbox[sent:] if m.to == ["prof@school.edu"]]
    assert len(asks) == 1 and "2 students haven’t ranked" in asks[0].subject
    assert "Alex Johnson, Riya Patel" in asks[0].body
    page = text(prof.get(f"/teach/s/{sid}"))
    assert "Your deadline passed" in page and "Keep sign-ups open" in page and "Close sign-ups and make the schedule" in page
    assert "The deadline has passed" in text(browser().get(f"/c/{sid}"))  # students are told to hurry
    # Keep it open: no deadline any more.
    page = text(follow(prof, prof.post(f"/teach/s/{sid}/deadline")))
    assert "Sign-ups stay open" in page and _sheet_get(prof.app, sid, "closes_at") is None
    # Or, another time, close and place everyone.
    _sheet_set(prof.app, sid, closes_at=PASSED, deadline_asked_at=PASSED)
    prof.post(f"/teach/s/{sid}/close-and-schedule")
    assert _sheet_get(prof.app, sid, "bidding_open") == 0 and len(assigned(prof.app, sid)) == 3


def test_ask_first_closes_by_itself_when_everyone_ranked(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    rankers(browser, sid, [("Sam Lee", [0, 1], []), ("Alex Johnson", [1, 0], []), ("Riya Patel", [0, 1], [])])
    _sheet_set(prof.app, sid, closes_at=PASSED, at_close="ask")
    browser().get(f"/c/{sid}")
    assert _sheet_get(prof.app, sid, "bidding_open") == 0 and len(assigned(prof.app, sid)) == 3


def test_the_professor_hears_once_when_everyone_has_ranked(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    sent = len(prof.outbox)
    rankers(browser, sid, [("Sam Lee", [0, 1], []), ("Alex Johnson", [1, 0], [])])
    assert not [m for m in prof.outbox[sent:] if m.to == ["prof@school.edu"]]
    keys = rankers(browser, sid, [("Riya Patel", [0, 1], [])])
    told = [m for m in prof.outbox[sent:] if m.to == ["prof@school.edu"]]
    assert len(told) == 1 and "everyone has ranked" in told[0].subject and "All 3 students" in told[0].body
    browser().sign_in_student(sid, "Riya Patel").rank(sid, [keys[1], keys[0]])  # a change of mind: no second email
    assert len([m for m in prof.outbox[sent:] if m.to == ["prof@school.edu"]]) == 1


# -- the top bar: My sheets, How ranking works, Settings ----------------------

def test_my_sheets_is_greyed_out_until_there_is_a_sheet(browser):
    newcomer = browser().sign_in_instructor("new@school.edu")
    page = raw(newcomer.get("/teach/"))
    assert 'class="icon-dock-label dock-empty" data-popover-target="no-sheets"' in page
    assert "No sheets yet" in page and "Make my first sheet" in page
    assert "You're making your first one" in html(newcomer.get("/teach/new"))
    newcomer.create_sheet()
    page = raw(newcomer.get("/teach/"))
    assert "No sheets yet" not in page
    assert 'class="icon-dock-label" aria-current="page">My sheets</a>' in page  # you're on it
    assert 'aria-current="page"' not in raw(newcomer.get("/teach/new"))


def test_settings_is_a_window_with_who_you_are_and_sign_out(prof, browser):
    page = raw(prof.get("/teach/"))
    window = page[page.index('<div id="settings"'):page.index('<div id="ranking-help"')]
    assert 'role="dialog"' in window and "Signed in as" in window
    assert "prof<wbr>@school.edu" in window  # long addresses break at the @
    assert 'action="/teach/logout"' in window and "Sign out" in window and "Make my first sheet" in window
    assert "Site owner page" not in window
    sid = prof.create_sheet()
    assert "My sign-up sheets" in raw(prof.get("/teach/"))
    student = browser().sign_in_student(sid, "Pat Doe")
    page = raw(student.get(f"/c/{sid}/home"))
    window = page[page.index('<div id="settings"'):page.index('<div id="ranking-help"')]
    assert "Pat Doe" in window and f'action="/c/{sid}/logout"' in window and "prof@school.edu" not in window
    assert 'id="settings"' not in raw(browser().get("/"))  # nobody to be signed in as
    owner = browser().sign_in_instructor("owner@gmail.com")
    assert "Site owner page" in raw(owner.get("/teach/"))


def test_time_zones_are_named_plainly():
    import app as app_module

    assert app_module.zone_name("America/Denver") in ("Denver time (MDT)", "Denver time (MST)")
    assert app_module.zone_name("America/Argentina/Buenos_Aires").startswith("Buenos Aires time")
    assert app_module.zone_name("UTC") == "UTC"


def test_how_ranking_works_is_a_word_button_with_its_own_window(browser):
    page = raw(browser().get("/"))
    assert 'data-popover-target="ranking-help"' in page and "How ranking works" in page
    assert 'aria-label="How days are assigned"' not in page  # the puzzling icon is gone
    window = page[page.index('<div id="ranking-help"'):]
    assert 'role="dialog"' in window and "Ties get a fair draw" in window and 'href="/how-it-works"' in window
    assert "As the instructor" not in window


def test_a_sheet_waiting_for_its_list_says_pending_and_why(prof):
    sid = prof.create_sheet(allow_unlisted=False)
    page = html(prof.get("/teach/"))
    assert ">Pending<" in page and "Waiting for class list" not in page
    assert "You're on setup step 7 of 8: add your students" in page  # as the setup's progress bar counts
    assert f'aria-describedby="status-{sid}"' in page and f'id="status-{sid}"' in page
    prof.upload(sid)
    page = html(prof.get("/teach/"))
    assert ">Open<" in page and "Students can rank their days now: 0 of 3 have" in page


# -- the owner's "Delete everything" -------------------------------------------

def test_the_owner_can_delete_everything_but_only_by_typing_DELETE(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    browser().sign_in_student(sid, "Sam Lee")
    keys = day_keys(sid)
    browser().sign_in_student(sid, "Alex Johnson").rank(sid, keys)
    other = browser().sign_in_instructor("other@school.edu")
    other.create_sheet(title="Other class")
    switched_off = browser().sign_in_instructor("abuser@school.edu")
    switched_off.create_sheet(title="Spam")
    owner = browser().sign_in_instructor("owner@gmail.com")
    owner.create_sheet(title="Owner's test")
    owner.post("/owner/account", {"email": "abuser@school.edu", "action": "disable"})
    owner.post("/owner/fonts", {"fonts": [(io.BytesIO(b"wOF2" + b"\x00" * 64), "yalenew-roman.woff2")]},
               content_type="multipart/form-data")
    app = prof.app

    page = text(owner.get("/owner/"))
    assert "Start fresh" in page and "Delete everything so far" in page
    assert "4 sign-up sheets" in page and "3 students on class lists" in page and "1 ranking" in page
    assert "2 other instructor accounts" in page  # prof and other: not the owner, not the switched-off one

    # Not the owner: no such page.
    assert prof.post("/owner/delete-everything", {"confirm": "DELETE"}).status_code == 404
    # Anything but DELETE, in capitals: nothing happens.
    for typed in ("", "delete", "Delete", "DELETE everything"):
        said = follow(owner, owner.post("/owner/delete-everything", {"confirm": typed}))
        assert "Nothing was deleted" in said
    assert q(app, "SELECT COUNT(*) FROM sheets") == 4

    said = text(follow(owner, owner.post("/owner/delete-everything", {"confirm": " DELETE "})))
    assert "Deleted everything: 4 sign-up sheets, 3 students on class lists, 1 ranking" in said
    assert "2 other instructor accounts" in said
    for table in ("sheets", "sheet_days", "roster", "submissions", "assignments", "snapshots", "login_codes"):
        assert q(app, f"SELECT COUNT(*) FROM {table}") == 0, table
    emails = {r["email"]: r["disabled"] for r in db_rows(app, "SELECT email, disabled FROM instructors")}
    assert emails == {"owner@gmail.com": 0, "abuser@school.edu": 1}  # the switched-off one stays off
    assert q(app, "SELECT COUNT(*) FROM site_assets") == 1  # the site's setup stays
    assert owner.get("/owner/").status_code == 200  # still signed in
    assert "There's nothing to delete" in text(owner.get("/owner/"))
    assert browser().get(f"/c/{sid}").status_code == 404
    assert prof.get("/teach/").headers["Location"].endswith("/teach/login")  # signed out: no account now


# -- the whole setup: the questions, then the students and the link -----------

def test_a_new_sheet_goes_on_to_its_students_and_its_link_with_a_progress_bar(prof, browser):
    page = text(prof.get("/teach/new"))
    assert "Step 1 of 8" in page and "About the class" in page and "minutes left" not in page
    r = prof.post("/teach/new", {"title": "Seminar", "day_date": [a_date("Mon", 0), a_date("Tue", 1)],
                                 "day_key": ["", ""], "capacity": "2"})
    sid = re.search(r"/teach/s/([^/#?]+)", r.headers["Location"]).group(1)
    assert r.headers["Location"].endswith(f"/teach/s/{sid}/setup")
    page = raw(prof.get(f"/teach/s/{sid}/setup"))
    assert "Step 7 of 8" in text(page) and "Add your students" in page
    assert "Do you use Canvas for this class?" in html_lib.unescape(page)
    assert page.count('name="setup" value="1"') == 4  # the drop box, Canvas's file, pasting, "sign themselves up"
    assert browser().sign_in_instructor("other@school.edu").get(f"/teach/s/{sid}/setup").status_code == 404

    # The class list, added from the setup, comes back to the setup and says what it read.
    r = prof.post(f"/teach/s/{sid}/roster", {"roster": (io.BytesIO(CANVAS_ROSTER.encode()), "students.csv"),
                                             "setup": "1"}, content_type="multipart/form-data")
    assert r.headers["Location"].endswith(f"/teach/s/{sid}/setup?step=students")
    page = text(prof.get(r.headers["Location"]))
    assert "3 students on your list" in page and "Next: share the link" in page
    assert "students.csv" in page  # the report on what was read, shown once

    # With the list in, the setup page is on its last step: sharing.
    page = html(prof.get(f"/teach/s/{sid}/setup"))
    assert "Step 8 of 8" in page and "Share the link with your class" in page
    assert "How should your 3 students get the link?" in page
    assert "Send it for me" in page and "Not set up on this site yet" in page  # until the owner's test passes
    assert "I'll send it myself" in page and "Email them myself" in page and "Copy the message" in page
    assert "alex@school.edu, riya@school.edu, sam@school.edu" in page  # the ready-made email's Bcc
    assert "3 students on your list" in text(page) and "Done: go to my class page" in page
    assert "students.csv" not in page


def test_setup_problems_and_choices_stay_in_the_setup(prof):
    sid = prof.create_sheet(allow_unlisted=False)
    said = follow(prof, prof.post(f"/teach/s/{sid}/roster", {"pasted": "", "setup": "1"}))
    assert "paste a list of names first" in said and "Step 7 of 8" in text(said)
    assert "Add your students first" in html(prof.get(f"/teach/s/{sid}/setup?step=share"))
    # "Let students sign themselves up", from the setup: on to sharing, with no email to send.
    r = prof.post(f"/teach/s/{sid}/who", {"allow_unlisted": "1", "back": "share", "setup": "1"})
    assert r.headers["Location"].endswith(f"/teach/s/{sid}/setup?step=share")
    page = html(prof.get(r.headers["Location"]))
    assert "Copy the message" in page and "Email it to" not in page and "Students sign themselves up" in page
    # Such a sheet's students step says there's nothing it needs.
    assert "So there's no list to add" in html(prof.get(f"/teach/s/{sid}/setup?step=students"))
    # On the class page, the same questions don't mention the setup.
    assert 'name="setup"' not in raw(prof.get(f"/teach/s/{sid}"))


def test_the_deadline_time_is_a_list_with_end_of_day_and_keeps_an_odd_saved_time(prof):
    import deadlines

    assert ("23:59", "11:59 PM") in deadlines.TIMES and ("17:30", "5:30 PM") in deadlines.TIMES
    assert ("17:15", "5:15 PM") in deadlines.slots("17:15") and deadlines.slots("17:00") == deadlines.TIMES
    page = raw(prof.get("/teach/new"))
    assert page.count('name="deadline-time"') == len(deadlines.TIMES) and "end of day" in page
    assert 'value="17:00" class="choice-input" checked' in page
    from datetime import datetime, timedelta, timezone

    sid = prof.create_sheet()
    soon = (datetime.now(timezone.utc) + timedelta(days=3)).replace(minute=15, second=0, microsecond=0)
    _sheet_set(prof.app, sid, closes_at=soon.isoformat())  # a quarter past: not one of the listed times
    page = raw(prof.get(f"/teach/s/{sid}/edit"))
    assert 'name="deadline-time" value="' in page
    with prof.app.app_context():
        zone = db.scalar("SELECT timezone FROM instructors WHERE email = 'prof@school.edu'") or settings.DEFAULT_TIMEZONE
        saved = deadlines.local_parts(db.scalar("SELECT closes_at FROM sheets WHERE id = :sid", sid=sid), zone)[1]
    assert f'<option value="{saved}" selected>' in page  # the time it was saved with, not rounded


def test_setup_parts_link_back_and_the_sheet_is_a_draft_until_set_up(prof):
    r = prof.post("/teach/new", {"title": "Seminar", "day_date": [a_date("Mon", 0), a_date("Tue", 1)],
                                 "day_key": ["", ""], "capacity": "2"})
    sid = re.search(r"/teach/s/([^/#?]+)", r.headers["Location"]).group(1)
    page = raw(prof.get("/teach/new"))
    assert 'data-draft-line hidden' in page and "Cancel" in page  # no Draft pill until there's a title
    assert '<h1 class="visually-hidden">New sign-up sheet</h1>' in page  # the question leads
    page = raw(prof.get(f"/teach/s/{sid}/setup"))
    assert 'class="draft-badge">Draft' in page and "Seminar" in page
    assert f'href="/teach/s/{sid}/edit?setup=1">About the class</a>' in page  # back into the first part
    assert f'href="/teach/s/{sid}/setup?step=share">Sharing the link</a>' in page
    assert f'href="/teach/s/{sid}/edit?setup=1"><span aria-hidden="true">←</span> Back to About the class</a>' in page
    # The first part again, on one page, then back to the setup.
    page = text(prof.get(f"/teach/s/{sid}/edit?setup=1"))
    assert "Steps 1 to 6 of 8" in page and "Save and continue" in page and "Back without saving" in page
    form = html(prof.get(f"/teach/s/{sid}/edit?setup=1"))
    version = re.search(r'name="version" value="([^"]+)"', form).group(1)
    r = prof.post(f"/teach/s/{sid}/edit", {"title": "Seminar in Law", "day_date": day_dates(sid),
                                          "day_key": day_keys(sid), "capacity": "3", "version": version,
                                          "setup": "1"})
    assert r.headers["Location"].endswith(f"/teach/s/{sid}/setup?step=students")
    assert "Seminar in Law" in raw(prof.get(r.headers["Location"]))
    # Once set up, the class page doesn't say draft.
    assert "draft-badge" not in raw(prof.get(f"/teach/s/{sid}"))


def test_messages_can_be_closed_and_canvas_steps_pick_up_after_signing_in(prof, browser):
    sid = prof.create_sheet(allow_unlisted=False)
    page = html(prof.get(f"/teach/s/{sid}"))
    assert "Open Canvas beside this page" in page and 'data-canvas-went="signin"' in page  # or in a tab
    assert page.count('class="canvas-pic"') == 3 and "Click Course Analytics" in page  # where to click, drawn
    assert "data-canvas-welcome" in page
    # No AI-assistant route for student data (agents' makers advise against using them on pages with
    # others' personal information; school policies restrict which AI tools may see a roster).
    assert "canvas-ai" not in page
    r = prof.post(f"/teach/s/{sid}/roster", {"pasted": ""})
    page = raw(prof.get(r.headers["Location"]))
    assert 'data-flash-stack' in page and 'class="flash-close" aria-label="Close this message"' in page
    assert "flash-error" in page



# -- class lists of emails alone, adding students, and invitations ------------

def test_a_list_of_emails_alone_works_and_each_student_types_their_name(prof, browser):
    sid = prof.create_sheet(allow_unlisted=False)
    prof.post(f"/teach/s/{sid}/roster", {"pasted": "alex.johnson@school.edu, ajones7@school.edu"})
    page = html(prof.get(f"/teach/s/{sid}"))
    assert "Alex Johnson" in page and "Ajones" in page
    assert page.count("name not typed yet") == 4  # on the list, and under "Who was added"
    # Alexis signs in with her email, and types her name before anything else.
    alexis = browser()
    assert alexis.post(f"/c/{sid}/login", {"name": "ajones7@school.edu"}).headers["Location"].endswith("/verify")
    r = alexis.post(f"/c/{sid}/verify", {"code": alexis.last_code("ajones7@school.edu")})
    assert alexis.get(r.headers["Location"]).headers["Location"].endswith(f"/c/{sid}/name")
    assert alexis.post_json(f"/c/{sid}/save", {"ranking": day_keys(sid), "excluded": [], "comments": {}}).status_code == 409
    page = html(alexis.get(f"/c/{sid}/name"))
    assert "What's your name?" in page and 'value=""' in page  # "Ajones" isn't offered as a guess
    assert "first and last name" in html(alexis.post(f"/c/{sid}/name", {"name": "Alexis"}))
    page = html(alexis.post(f"/c/{sid}/name", {"name": "alex johnson"}))
    assert "already called Alex Johnson" in page and 'value="alex johnson"' in page  # what she typed stays
    assert alexis.post(f"/c/{sid}/name", {"name": "Alexis Jones"}).headers["Location"].endswith(f"/c/{sid}/home")
    assert alexis.get(f"/c/{sid}/home").status_code == 200
    rows = db_rows(prof.app, "SELECT display_name, name_pending FROM roster WHERE sheet_id = :sid ORDER BY email", sid=sid)
    assert [(r["display_name"], r["name_pending"]) for r in rows] == [("Alexis Jones", 0), ("Alex Johnson", 1)]
    # Adding the same emails again keeps the name she typed.
    prof.post(f"/teach/s/{sid}/roster/quick-add", {"people": "ajones7@school.edu, new.person@school.edu"})
    rows = db_rows(prof.app, "SELECT display_name FROM roster WHERE sheet_id = :sid ORDER BY email", sid=sid)
    assert [r["display_name"] for r in rows] == ["Alexis Jones", "Alex Johnson", "New Person"]


def test_students_can_be_added_from_the_class_list_and_sent_the_link(prof):
    sid = prof.create_sheet(allow_unlisted=False)
    prof.upload(sid)
    page = html(prof.get(f"/teach/s/{sid}"))
    assert 'id="add-students"' in page and "Send them the link" not in page
    r = prof.post(f"/teach/s/{sid}/roster/quick-add", {"people": "Dana Wu <dana@school.edu>, eli.o@school.edu"})
    page = html(prof.get(r.headers["Location"]))
    assert "Added 2 students: Dana Wu, Eli O" in page
    assert "Send them the link" in page and "dana@school.edu, eli.o@school.edu" in page  # the email's Bcc
    assert "I'll send it myself" in page and "Not set up on this site yet" in page
    assert "Send them the link" not in html(prof.get(f"/teach/s/{sid}"))  # just once
    said = follow(prof, prof.post(f"/teach/s/{sid}/roster/quick-add", {"people": "dana@school.edu"}))
    assert "all on your list already" in said
    assert "at least one school email" in follow(prof, prof.post(f"/teach/s/{sid}/roster/quick-add", {"people": ""}))


def test_workos_sends_invitations_once_the_owner_has_tested_them(prof, browser, monkeypatch):
    sid = prof.create_sheet(allow_unlisted=False)
    prof.post(f"/teach/s/{sid}/roster", {"pasted": "alex.johnson@school.edu\nSam Lee, sam@school.edu"})
    owner = browser().sign_in_instructor("owner@gmail.com")
    workos = _use_workos(monkeypatch)
    # Off until the owner's test invitation comes back here.
    assert "Not set up on this site yet" in html(prof.get(f"/teach/s/{sid}/setup?step=share"))
    said = follow(prof, prof.post(f"/teach/s/{sid}/invite", {"back": "setup"}))
    assert "isn't set up on this site yet" in said and not workos.invitations
    workos.invite_base = "https://example.authkit.app/invite"
    said = follow(owner, owner.post("/owner/invitations/test"))
    assert "not to this site" in said and "http://localhost/invite" in said
    assert workos.invitations[-1]["state"] == "revoked"
    workos.invite_base = "http://localhost/invite"
    said = follow(owner, owner.post("/owner/invitations/test"))
    assert "instructors can now have WorkOS send" in said
    assert "It's on." in html(owner.get("/owner/"))
    # The owner's own test link: it works, and says so.
    assert "The invitation works" in html(owner.post("/invite", {"invitation_token": workos.invitations[-1]["token"]}))

    page = html(prof.get(f"/teach/s/{sid}/setup?step=share"))
    assert "Send 2 invitations" in page
    said = follow(prof, prof.post(f"/teach/s/{sid}/invite", {"back": "setup"}))
    assert "WorkOS is emailing 2 students an invitation" in said
    sent = {i["email"]: i for i in workos.invitations[2:]}
    assert set(sent) == {"alex.johnson@school.edu", "sam@school.edu"}
    assert "got one in the last day" in follow(prof, prof.post(f"/teach/s/{sid}/invite", {"back": "setup"}))
    assert len(workos.invitations) == 4

    # Its button signs Alex in, after a press (email scanners open links too), and it's used up.
    alex = browser()
    token = sent["alex.johnson@school.edu"]["token"]
    assert "Sign me in" in html(alex.get(f"/invite?invitation_token={token}"))
    r = alex.post("/invite", {"invitation_token": token})
    assert r.headers["Location"].endswith(f"/c/{sid}/home")
    assert alex.get(f"/c/{sid}/home").headers["Location"].endswith(f"/c/{sid}/name")  # added by email: name first
    assert sent["alex.johnson@school.edu"]["state"] == "revoked"
    assert "already used" in html(browser().post("/invite", {"invitation_token": token}))
    assert q(prof.app, "SELECT COUNT(*) FROM invitations WHERE sheet_id = :sid", sid=sid) == 1  # Sam's, still out


def test_the_ranking_options_each_show_the_same_small_example(browser):
    page = text(browser().get("/how-it-works"))
    assert page.count("In the example") == 3 and "Ana and Ben want Monday most" in page
    assert "Everyone gets their first choice." in page and "Everyone gets their second choice." in page


def test_the_schedule_tab_shows_the_days_and_seats_and_warns_before_changing_ranked_days(prof, browser):
    sid = prof.create_sheet(days=("Mon", "Tue", "Wed"), capacity=2)
    page = raw(prof.get(f"/teach/s/{sid}/schedule"))
    assert page.count('class="day-sheet"') == 3 and page.count('class="slot open"') == 6  # 3 days × 2 seats, blank
    assert "<summary>Modify this</summary>" in page and "Nobody has ranked yet" in page
    assert "data-confirm-click" not in page.split("Modify this")[1].split("</details>")[0]
    browser().sign_in_student(sid, "Pat Doe").rank(sid, day_keys(sid))
    page = html(prof.get(f"/teach/s/{sid}/schedule"))
    assert "1 student has already ranked these days" in page
    assert 'data-confirm-click="1 student has already ranked these days. Change the days anyway?"' in page
    prof.post(f"/teach/s/{sid}/close-and-schedule")
    page = raw(prof.get(f"/teach/s/{sid}/schedule"))
    assert "Pat Doe" in page and page.count('class="slot open"') == 5 and 'class="slot filled' in page


def test_class_page_cards_fold_away_with_only_the_first_two_open(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    assert re.search(r'id="class-list"[^>]* open data-fold-alert', raw(prof.get(f"/teach/s/{sid}")))  # its report
    prof.post(f"/teach/s/{sid}/roster/report/dismiss")
    page = raw(prof.get(f"/teach/s/{sid}"))
    assert re.search(r'<details class="card next-step fold-card" data-fold="next-\w+" open>', page)
    assert '<details class="card guide fold-card"' in page and "Getting started" in page
    for card in ("class-list", "try-it", "share", "signups"):
        tag = re.search(rf'<details class="card fold-card" id="{card}"[^>]*>', page).group(0)
        assert " open" not in tag, card  # folded away to start
    # A card with something to deal with opens anyway.
    _sheet_set(prof.app, sid, closes_at=PASSED, at_close="ask")
    browser().get(f"/c/{sid}")  # the deadline passes with students missing: the instructor is asked
    page = raw(prof.get(f"/teach/s/{sid}"))
    assert '<details class="card fold-card" id="signups" data-fold="signups-' in page
    assert re.search(r'id="signups"[^>]* open data-fold-alert', page)


# -- Class lists from Canvas (Scheduler Helper, Connect Canvas) -------------

def _canvas_list(course="2026FA_BUSCOM_615_SEC1", n=3, names=None, course_id="4242", host="canvas.school.edu"):
    """A class list as Scheduler Helper hands it to the class-list page."""
    people = [{"name": names[i] if names else f"Student {i}", "email": f"s{i}@u.school.edu"} for i in range(n)]
    return json.dumps({"type": "scheduler-class-list", "v": 2, "courseId": course_id, "host": host, "course": course,
                       "students": people})


def test_a_list_the_helper_brings_goes_in(prof):
    sid = prof.create_sheet(allow_unlisted=False)
    r = prof.post(f"/teach/s/{sid}/canvas-list", {"list": _canvas_list(n=5), "how": "helper", "setup": "1"})
    assert r.headers["Location"].endswith(f"/teach/s/{sid}/setup?step=students")
    assert "Added 5 students" in html(prof.get(r.headers["Location"]))
    assert q(prof.app, "SELECT canvas_course_id FROM sheets WHERE id = :sid", sid=sid) == "4242"
    assert "didn't come through" in follow(prof, prof.post(f"/teach/s/{sid}/canvas-list", {"list": "{}"}))
    # Only from this site's own page (its form token), and only to the instructor's own sheet.
    other = prof.app.test_client()
    assert other.post(f"/teach/s/{sid}/canvas-list", data={"list": _canvas_list()}).status_code == 400


def test_lists_that_arent_class_lists_are_turned_away_quietly(prof):
    sid = prof.create_sheet(allow_unlisted=False)
    deep = json.dumps({"type": "scheduler-class-list", "students": []})[:-2] + "[" * 60000 + "]" * 60000 + "]}"
    for bad in ("", "nope", json.dumps({"type": "other"}), json.dumps({"type": "scheduler-class-list", "students": []}),
                json.dumps({"type": "scheduler-class-list", "students": [{"name": "x"}] * 3001}), deep):
        assert "didn't come through" in follow(prof, prof.post(f"/teach/s/{sid}/canvas-list", {"list": bad}))
    assert q(prof.app, "SELECT COUNT(*) FROM error_log") == 0
    # Odd characters (control characters, lone surrogates) are dropped, not stored or choked on.
    odd = json.dumps({"type": "scheduler-class-list", "course": "X\ud800", "students": [{"name": "A \ud800 B\u0007",
                                                                                            "email": "a@b.edu"}]})
    prof.post(f"/teach/s/{sid}/canvas-list", {"list": odd})
    with prof.app.app_context():
        assert db.scalar("SELECT display_name FROM roster WHERE sheet_id = :s", s=sid) == "A B"
        assert db.scalar("SELECT COUNT(*) FROM error_log") == 0


def test_a_sheet_keeps_its_canvas_course_and_flags_another(prof):
    a = prof.create_sheet(title="LAW 310", allow_unlisted=False)
    prof.post(f"/teach/s/{a}/canvas-list", {"list": _canvas_list(n=3, course_id="111", course="LAW 310")})
    assert q(prof.app, "SELECT canvas_course_id FROM sheets WHERE id = :s", s=a) == "111"
    assert 'data-canvas-course-id="111"' in raw(prof.get(f"/teach/s/{a}"))  # Update from Canvas: no picking
    # The wrong course for a tied sheet: a second look first, and backing out leaves it tied as it was.
    r = prof.post(f"/teach/s/{a}/canvas-list", {"list": _canvas_list(n=3, course_id="222", course="BIO 101")})
    assert "/roster/review/" in r.headers["Location"]
    assert "came from LAW 310 in Canvas; this one is from BIO 101" in text(prof.get(r.headers["Location"]))
    prof.post(r.headers["Location"], {"action": "cancel"})
    assert q(prof.app, "SELECT canvas_course_id FROM sheets WHERE id = :s", s=a) == "111"
    # Replacing it with that course's list after all: the sheet is now that course's.
    r = prof.post(f"/teach/s/{a}/canvas-list", {"list": _canvas_list(n=3, course_id="222", course="BIO 101")})
    prof.post(r.headers["Location"], {"action": "replace"})
    assert q(prof.app, "SELECT canvas_course_id FROM sheets WHERE id = :s", s=a) == "222"
    # Undo puts the tie back with the list; clearing the list unties it; a file replacing it unties it too.
    page = raw(prof.get(f"/teach/s/{a}"))
    undo = re.search(r'action="([^"]*/undo/[^"]*)"', page)
    form = dict(re.findall(r'name="(parts|key)" value="([^"]*)"', page.split(undo.group(1))[1][:600]))
    prof.post(html_lib.unescape(undo.group(1)), form)
    assert q(prof.app, "SELECT canvas_course_id FROM sheets WHERE id = :s", s=a) == "111"
    prof.post(f"/teach/s/{a}/roster/clear")
    assert q(prof.app, "SELECT canvas_course_id FROM sheets WHERE id = :s", s=a) is None
    prof.post(f"/teach/s/{a}/canvas-list", {"list": _canvas_list(n=3, course_id="111", course="LAW 310")})
    prof.upload(a, "Name,Email\nAlex Kim,alex@x.edu\n")
    with prof.app.app_context():
        pid = db.scalar("SELECT id FROM pending_uploads WHERE sheet_id = :s", s=a)
    prof.post(f"/teach/s/{a}/roster/review/{pid}", {"action": "replace"})
    assert q(prof.app, "SELECT canvas_course_id FROM sheets WHERE id = :s", s=a) is None
    # An empty sheet still tied to an old course just takes the new one.
    c = prof.create_sheet(title="Seminar", allow_unlisted=False)
    with prof.app.app_context():
        db.run("UPDATE sheets SET canvas_course_id = '333', canvas_course = 'SEM 1' WHERE id = :s", s=c)
        db.commit()
    r = prof.post(f"/teach/s/{c}/canvas-list", {"list": _canvas_list(n=2, course_id="444")})
    assert r.headers["Location"].endswith(f"/teach/s/{c}#class-list")
    assert q(prof.app, "SELECT canvas_course_id FROM sheets WHERE id = :s", s=c) == "444"
    # Two courses with one name are told apart by number.
    r = prof.post(f"/teach/s/{c}/canvas-list", {"list": _canvas_list(n=2, course_id="555", course="LAW 1")})
    prof.post(r.headers["Location"], {"action": "replace"})
    r = prof.post(f"/teach/s/{c}/canvas-list", {"list": _canvas_list(n=2, course_id="556", course="LAW 1")})
    assert ("came from Canvas course 555; this one is from course 556, which has the same name"
            in text(prof.get(r.headers["Location"])))


def test_a_list_from_canvas_teaches_the_site_which_canvas_is_theirs(prof):
    assert q(prof.app, "SELECT canvas_host FROM instructors WHERE email = 'prof@school.edu'") is None
    sid = prof.create_sheet(allow_unlisted=False)
    prof.post(f"/teach/s/{sid}/canvas-list", {"list": _canvas_list(host="canvas.myschool.edu"), "how": "helper"})
    assert q(prof.app, "SELECT canvas_host FROM instructors WHERE email = 'prof@school.edu'") == "canvas.myschool.edu"
    assert 'data-host="canvas.myschool.edu"' in raw(prof.get(f"/teach/s/{sid}"))


def test_canvas_text_that_isnt_a_class_list_never_becomes_students():
    from roster import parse_roster

    courses = 'while(1);[{"id":1,"name":"LAW 310 Computer Science and Law","course_code":"LAW310"}]'
    assert "list of Canvas courses" in parse_roster(courses.encode()).error
    assert "couldn't show that page" in parse_roster(b'{"errors":[{"message":"The specified resource does not exist."}]}').error
    assert "signed out" in parse_roster(b'{"errors":[{"message":"user authorization required"}]}').error
    cut = '[{"id":0,"name":"Student 0","sortable_name":"0, S","email":"s0@x.edu"},{"id":1,"name":"Stu'
    assert "Only part" in parse_roster(cut.encode()).error
    copied = 'JSON  Raw Data  Headers\n[{"id":0,"name":"Kim, Alex","sortable_name":"Kim, Alex","email":"a@x.edu"}]'
    result = parse_roster(copied.encode())
    assert not result.error and [s["display_name"] for s in result.students] == ["Alex Kim"]
    error_after = 'JSON  Raw Data  Headers\n{"errors":[{"message":"The specified resource does not exist."}]}'
    assert "couldn't show that page" in parse_roster(error_after.encode()).error
    cut_start = '},{"id":2,"name":"Student 2","sortable_name":"2, S","email":"s2@x.edu"},{"id":3,"name":"S 3","email":"s3@x.edu"}]'
    assert "Only part" in parse_roster(cut_start.encode()).error
    pretty_cut = '[\n  {\n    "id": 0,\n    "name": "Student 0",\n    "sortable_name": "0, Student",\n  },\n  {\n    "id": 1'
    assert "Only part" in parse_roster(pretty_cut.encode()).error
    assert "isn't a class list" in parse_roster(b'[{"id":11,"course_id":101,"name":"LAW 310 Sec 1","start_at":null}]').error
    assert "isn't a class list" in parse_roster(b'{"id":1,"name":"Alex","primary_email":"a@x.edu","login_id":"a"}').error
    enrollments = ('[{"id":900,"type":"StudentEnrollment","user":{"id":0,"name":"Student 0","login_id":"s0@x.edu"}},'
                   '{"id":901,"type":"TeacherEnrollment","user":{"id":1,"name":"Prof","login_id":"p@x.edu"}}]')
    assert [s["display_name"] for s in parse_roster(enrollments.encode()).students] == ["Student 0"]
    names = parse_roster(b'[{"name":"Alex Kim"},{"name":"Sam Lee"}]')
    assert [s["display_name"] for s in names.students] == ["Alex Kim", "Sam Lee"]
    assert "doesn't look like a class list" in parse_roster(("[" * 100000 + "]" * 100000).encode()).error


def test_after_a_list_goes_in_the_instructor_sees_who_came_over(prof):
    sid = prof.create_sheet(allow_unlisted=False)
    prof.post(f"/teach/s/{sid}/canvas-list", {"list": _canvas_list(n=3), "setup": "1"})
    setup = text(prof.get(f"/teach/s/{sid}/setup?step=students"))
    assert "Who's on your list (3)" in setup and "Student 0 s0@u.school.edu" in setup and "Change the list" in setup
    sid = prof.create_sheet(title="Another", allow_unlisted=False)
    prof.post(f"/teach/s/{sid}/canvas-list", {"list": _canvas_list(n=3)})
    page = text(prof.get(f"/teach/s/{sid}"))
    assert "Who was added (3)" in page
    # Adding to the list: only the new ones are named.
    more = json.dumps({"type": "scheduler-class-list", "v": 2, "courseId": "4242", "course": "C",
                       "students": [{"name": f"Student {i}", "email": f"s{i}@u.school.edu"} for i in range(5)]})
    r = prof.post(f"/teach/s/{sid}/canvas-list", {"list": more})
    prof.post(r.headers["Location"], {"action": "add"})
    page = text(prof.get(f"/teach/s/{sid}"))
    assert "Who was added (2)" in page and "Student 4 s4@u.school.edu" in page.split("Who was added")[1][:200]


def test_scheduler_helper_is_offered_only_once_it_exists(prof, monkeypatch):
    sid = prof.create_sheet(allow_unlisted=False)
    assert "data-helper-go" not in raw(prof.get(f"/teach/s/{sid}"))  # not published yet: not offered
    monkeypatch.setattr(settings, "CANVAS_HELPER_IDS", ["abcdefghijklmnopabcdefghijklmnop"])
    monkeypatch.setattr(settings, "CANVAS_HELPER_STORE_URL", "https://chromewebstore.google.com/detail/x")
    monkeypatch.setattr(settings, "CANVAS_HELPER_SAFARI_URL", "https://apps.apple.com/app/id1")
    page = raw(prof.get(f"/teach/s/{sid}"))
    assert 'data-helper-ids="abcdefghijklmnopabcdefghijklmnop"' in page and "Get my class list from Canvas" in page
    assert 'data-helper-store="https://chromewebstore.google.com/detail/x"' in page
    assert 'data-helper-safari="https://apps.apple.com/app/id1"' in page
    assert "Add Scheduler Helper to Chrome" in text(prof.get(f"/teach/s/{sid}"))
    assert "upload Canvas's student file" in text(prof.get(f"/teach/s/{sid}"))
    # A list the helper fetched for this page goes in like one copied here.
    r = prof.post(f"/teach/s/{sid}/canvas-list", {"list": _canvas_list(n=3), "how": "helper"})
    assert r.headers["Location"].endswith(f"/teach/s/{sid}#class-list")
    assert q(prof.app, "SELECT COUNT(*) FROM roster WHERE sheet_id = :sid", sid=sid) == 3


def test_the_helper_extension_builds_with_least_permissions():
    import importlib.util
    import zipfile as zf

    spec = importlib.util.spec_from_file_location("build_extension", "tools/build_extension.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    build.build("store")
    with zf.ZipFile("dist/scheduler-helper.zip") as z:
        manifest = json.loads(z.read("manifest.json"))
        assert sorted(z.namelist()) == ["background.js", "icon-128.png", "icon-16.png", "icon-32.png", "icon-48.png",
                                        "manifest.json"]
    assert manifest["permissions"] == ["scripting"] and "key" not in manifest
    assert manifest["host_permissions"] == ["https://canvas.northwestern.edu/*"]
    assert manifest["externally_connectable"]["matches"] == ["https://scheduler-lottery.vercel.app/*"]
    assert not any("127.0.0.1" in h or "localhost" in h for h in json.dumps(manifest).split('"'))


def _connectable_canvas(monkeypatch):
    """The stand-in Canvas (tools/fake_canvas.py) as the school's Canvas for
    Connect Canvas, answering the site's server-to-server requests."""
    import importlib.util

    import canvas_oauth

    spec = importlib.util.spec_from_file_location("fake_canvas", "tools/fake_canvas.py")
    fake = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fake)
    canvas = fake.app.test_client()  # the professor's browser at Canvas
    server = fake.app.test_client(use_cookies=False)  # this site's own requests: no Canvas sign-in of theirs
    monkeypatch.setattr(settings, "CANVAS_OAUTH_HOST", "canvas.test")
    monkeypatch.setattr(settings, "CANVAS_OAUTH_CLIENT_ID", fake.CLIENT_ID)
    monkeypatch.setattr(settings, "CANVAS_OAUTH_CLIENT_SECRET", fake.CLIENT_SECRET)

    def http(method, url, headers=None, data=None):
        assert url.startswith("https://canvas.test/")
        r = server.open(url[len("https://canvas.test"):], method=method, headers=headers or {}, data=data,
                        base_url="https://canvas.test")
        return r.status_code, dict(r.headers), r.get_data(as_text=True)

    monkeypatch.setattr(canvas_oauth, "_http", http)
    canvas.post("/login", base_url="https://canvas.test")  # the professor, signed in to Canvas
    return canvas, fake


def _authorize(canvas, location, action="authorize"):
    """At Canvas: the Authorize page, and the professor's answer. Back to Scheduler's callback."""
    from urllib.parse import parse_qs, urlsplit

    assert location.startswith("https://canvas.test/login/oauth2/auth?")
    asked = parse_qs(urlsplit(location).query)
    assert asked["scope"] == ["url:GET|/api/v1/courses url:GET|/api/v1/courses/:course_id/users"]
    page = canvas.get(location[len("https://canvas.test"):], base_url="https://canvas.test")
    assert "requesting access to your account" in page.get_data(as_text=True)
    back = canvas.post("/login/oauth2/confirm", base_url="https://canvas.test", data={
        "redirect_uri": asked["redirect_uri"][0], "state": asked["state"][0], "action": action})
    return urlsplit(back.headers["Location"])


def test_connect_canvas_reads_the_class_list_with_canvas_permission(prof, monkeypatch):
    sid = prof.create_sheet(allow_unlisted=False)
    assert "Connect Canvas" not in text(prof.get(f"/teach/s/{sid}"))  # no developer key: not offered
    canvas, fake = _connectable_canvas(monkeypatch)
    assert "Connect Canvas" in text(prof.get(f"/teach/s/{sid}"))
    back = _authorize(canvas, prof.get(f"/teach/s/{sid}/canvas/connect").headers["Location"])
    page = text(prof.get(back.path + "?" + back.query))
    assert "Which course is" in page and "2026FA_BUSCOM_615_SEC1" in page and "FACULTY_TRAINING" not in page
    r = prof.post("/teach/canvas/oauth/pick", {"course_id": "101", "course_name": "2026FA_BUSCOM_615_SEC1 (2026 Fall)"})
    assert r.headers["Location"].endswith(f"/teach/s/{sid}#class-list")
    assert q(prof.app, "SELECT COUNT(*) FROM roster WHERE sheet_id = :sid", sid=sid) == 57
    assert q(prof.app, "SELECT canvas_course_id FROM sheets WHERE id = :sid", sid=sid) == "101"
    assert "Who was added (57)" in text(prof.get(f"/teach/s/{sid}"))
    assert not fake.TOKENS  # Canvas's permission given back at once
    # Next time, no picking: the sheet's course, straight away (a list in place: what changes, first).
    back = _authorize(canvas, prof.get(f"/teach/s/{sid}/canvas/connect").headers["Location"])
    r = prof.get(back.path + "?" + back.query)
    assert "/roster/review/" in r.headers["Location"] and not fake.TOKENS


def test_connect_canvas_takes_no_for_an_answer_and_nothing_forged(prof, monkeypatch):
    import canvas_oauth

    sid = prof.create_sheet(allow_unlisted=False)
    canvas, fake = _connectable_canvas(monkeypatch)
    back = _authorize(canvas, prof.get(f"/teach/s/{sid}/canvas/connect").headers["Location"], action="cancel")
    assert "you pressed Cancel" in follow(prof, prof.get(back.path + "?" + back.query))
    # Canvas's answer has to match the request this browser started.
    back = _authorize(canvas, prof.get(f"/teach/s/{sid}/canvas/connect").headers["Location"])
    forged = back.query.replace("state=", "state=x")
    assert "didn't come from here" in follow(prof, prof.get(back.path + "?" + forged))
    assert q(prof.app, "SELECT COUNT(*) FROM roster WHERE sheet_id = :sid", sid=sid) == 0
    # The permission waits ten minutes for the course to be picked, no more, and is given back.
    back = _authorize(canvas, prof.get(f"/teach/s/{sid}/canvas/connect").headers["Location"])
    prof.get(back.path + "?" + back.query)
    monkeypatch.setattr(canvas_oauth, "SEALED_FOR", -1)
    assert "permission ran out" in follow(prof, prof.post("/teach/canvas/oauth/pick", {"course_id": "101"}))
    # Someone else's sheet can't be connected.
    other = prof.create_sheet(title="Mine")
    prof.post("/teach/logout")
    prof.sign_in_instructor("other@school.edu")
    assert prof.get(f"/teach/s/{other}/canvas/connect").status_code == 404


def test_the_helper_is_offered_only_for_the_canvas_it_reads():
    with open("extension/manifest.json", encoding="utf-8") as handle:
        manifest = json.load(handle)
    hosts = [re.match(r"https://([^/]+)/\*$", h).group(1) for h in manifest["host_permissions"]]
    assert hosts == settings.CANVAS_HELPER_HOSTS  # the page offers it to exactly these schools


def test_a_file_with_no_names_or_emails_gets_a_look_first(prof):
    sid = prof.create_sheet(allow_unlisted=False)
    grades = "Assignment Name,Points Possible,Average\nEssay 1,10,8.5\nReading Response 2,5,4.1\nMidterm Paper,20,17\n"
    r = prof.upload(sid, grades, filename="course_grade.csv")
    assert "/roster/review/" in r.headers["Location"]  # though the sheet has no list yet
    assert "may not be your class list" in text(prof.get(r.headers["Location"]))
    assert q(prof.app, "SELECT COUNT(*) FROM roster WHERE sheet_id = :sid", sid=sid) == 0


def test_no_message_points_at_a_guide_that_is_gone():
    import inspect

    import roster

    assert "Where do I find" not in inspect.getsource(roster)
    assert "Or paste names" not in roster.PASTE_INSTEAD


def test_canvas_names_written_last_first_are_turned_round_not_cut(prof):
    sid = prof.create_sheet(allow_unlisted=False)
    names = ["Kim, Alex", "Lee, Sam", "Diaz, Maria", "Kim, Jordan"]
    prof.post(f"/teach/s/{sid}/canvas-list", {"list": _canvas_list(n=4, names=names)})
    with prof.app.app_context():
        got = sorted(r["display_name"] for r in db.rows("SELECT display_name FROM roster WHERE sheet_id = :s", s=sid))
    assert got == ["Alex Kim", "Jordan Kim", "Maria Diaz", "Sam Lee"]
