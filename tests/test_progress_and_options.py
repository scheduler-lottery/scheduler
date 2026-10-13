"""Emails to the instructor about how sign-ups are going, reminders for
students who haven't ranked, the ways of matching days, and the pieces of
the site around them (the Home button, the Canvas step's two options)."""

import json
import re

import db
import deadlines
from conftest import day_keys
from matching_engine import ALGORITHMS, _rank_max, build_student_prefs, compute_assignment
from test_app import follow, html, q, raw, text

NAMES = ["Ana Lima", "Bo Chen", "Cy Diaz", "Di Eng", "Ed Fox", "Flo Gray"]
SIX = "Name,Email\n" + "".join(f"{n},s{i}@school.edu\n" for i, n in enumerate(NAMES))


def progress_mail(prof):
    return [m for m in prof.outbox if "have ranked" in m.subject or "nobody has ranked" in m.subject
            or "close soon" in m.subject]


def rank(browser, sid, n, keys):
    for i in range(n):
        browser().sign_in_student(sid, NAMES[i], email=f"s{i}@school.edu").rank(sid, keys)


# ---------------------------------------------------------------------------
# Progress emails
# ---------------------------------------------------------------------------
def test_half_then_nine_in_ten_are_each_said_once(prof, browser):
    sid = prof.create_sheet(allow_unlisted=False)
    prof.upload(sid, SIX)
    keys = day_keys(sid)
    rank(browser, sid, 2, keys)
    assert not progress_mail(prof)
    browser().sign_in_student(sid, "Cy Diaz", email="s2@school.edu").rank(sid, keys)  # 3 of 6: half
    mails = progress_mail(prof)
    assert len(mails) == 1 and "3 of 6 students have ranked" in mails[0].subject
    assert "half of your students have ranked" in mails[0].body and "Still to rank (3): Di Eng, Ed Fox, Flo Gray" in mails[0].body
    assert "\n\n" in mails[0].body
    browser().sign_in_student(sid, "Di Eng", email="s3@school.edu").rank(sid, keys)
    assert len(progress_mail(prof)) == 1  # (4 of 6: nothing new to say)
    browser().sign_in_student(sid, "Ed Fox", email="s4@school.edu").rank(sid, keys)
    assert len(progress_mail(prof)) == 1  # (5 of 6 is under nine in ten)
    browser().sign_in_student(sid, "Flo Gray", email="s5@school.edu").rank(sid, keys)
    assert len(progress_mail(prof)) == 1  # everyone: "everyone has ranked" instead
    assert any("everyone has ranked" in m.subject for m in prof.outbox)


def test_progress_emails_can_be_turned_off(prof, browser):
    sid = prof.create_sheet(allow_unlisted=False)
    prof.upload(sid, SIX)
    page = text(prof.get(f"/teach/s/{sid}/settings"))
    assert "Email me how sign-ups are going" in page
    prof.post(f"/teach/s/{sid}/progress-emails", {})
    assert q(prof.app, "SELECT progress_emails FROM sheets WHERE id = :sid", sid=sid) == 0
    rank(browser, sid, 4, day_keys(sid))
    assert not progress_mail(prof)
    prof.post(f"/teach/s/{sid}/progress-emails", {"progress_emails": "1"})
    assert q(prof.app, "SELECT progress_emails FROM sheets WHERE id = :sid", sid=sid) == 1


def test_a_quiet_week_and_a_near_deadline_are_each_said_once(prof, browser):
    sid = prof.create_sheet(allow_unlisted=False)
    prof.upload(sid, SIX)
    with prof.app.test_request_context():
        from datetime import timedelta

        from util import iso, now
        db.run("UPDATE sheets SET shared_at = :at WHERE id = :sid", at=iso(now() - timedelta(days=8)), sid=sid)
        db.commit()
        assert deadlines.quiet_weeks() == 1 and deadlines.quiet_weeks() == 0
        # (a sheet left idle for months, or archived: nothing)
        other = prof.create_sheet(title="Old one")
        db.run("UPDATE sheets SET shared_at = '2020-01-02T00:00:00Z' WHERE id = :sid", sid=other)
        db.commit()
        assert deadlines.quiet_weeks() == 0
    mail = progress_mail(prof)[-1]
    assert "nobody has ranked yet" in mail.subject and "a week since you shared the link" in mail.body
    # Two days before the deadline, with who still hasn't ranked.
    rank(browser, sid, 1, day_keys(sid))
    with prof.app.test_request_context():
        from datetime import timedelta

        from util import iso, now
        db.run("UPDATE sheets SET closes_at = :at WHERE id = :sid", at=iso(now() + timedelta(hours=30)), sid=sid)
        db.commit()
        assert deadlines.deadline_soon() == 1 and deadlines.deadline_soon() == 0
    mail = progress_mail(prof)[-1]
    assert "close soon" in mail.subject and "Still to rank (5)" in mail.body


# ---------------------------------------------------------------------------
# Reminders for students who haven't ranked
# ---------------------------------------------------------------------------
def test_reminders_offer_a_friendly_one_a_last_call_and_sign_in_help(prof, browser):
    sid = prof.create_sheet(allow_unlisted=False)
    prof.upload(sid, SIX)
    rank(browser, sid, 1, day_keys(sid))
    page = raw(prof.get(f"/teach/s/{sid}"))
    words = text(page)
    assert "Remind the 5 who haven't ranked" in words
    assert "Friendly reminder" in words and "Trouble signing in?" in words
    assert "Last call" not in words  # (no deadline)
    bodies = re.findall(r'data-body="([^"]*)"', page)
    assert any("Check your spam or junk folder" in b for b in bodies)
    assert all("s0@school.edu" not in b for b in re.findall(r'data-bcc="([^"]*)"', page))  # (ranked: left out)


# ---------------------------------------------------------------------------
# Ways of matching days
# ---------------------------------------------------------------------------
def sub(key, ranking, first_at=""):
    return {"name_key": key, "display_name": key.title(), "ranking": json.dumps(list(ranking)),
            "excluded_days": "[]", "updated_at": "2026-10-11T12:00:00Z", "first_at": first_at}


def test_the_lottery_is_first_and_the_new_options_are_choosable(prof):
    assert list(ALGORITHMS)[0] == "da_single_lottery"
    sid = prof.create_sheet()
    assert q(prof.app, "SELECT algorithm FROM sheets WHERE id = :sid", sid=sid) == "da_single_lottery"
    page = text(prof.get(f"/teach/s/{sid}/schedule"))
    assert "How days are matched (most people never change this)" in page
    for label in ("Lottery (recommended): one random order", "Fair draw for each day", "Most first choices",
                  "First come, first served"):
        assert label in page
    for algorithm in ("rank_max", "first_come", "da_independent", "da_single_lottery"):
        follow(prof, prof.post(f"/teach/s/{sid}/algorithm", {"algorithm": algorithm}))
        assert q(prof.app, "SELECT algorithm FROM sheets WHERE id = :sid", sid=sid) == algorithm


def test_first_come_goes_by_who_ranked_first_and_says_so(prof):
    days = ["a", "b"]
    students = build_student_prefs([sub("late", "ab", "2026-10-11T10:00:00Z"),
                                    sub("early", "ab", "2026-10-11T09:00:00Z")], days)
    for seed in range(5):
        result = {k: d for k, _n, d, _m in compute_assignment(students, days, cap=1, algorithm="first_come", seed=seed)}
        assert result == {"early": "a", "late": "b"}
    sid = prof.create_sheet()
    prof.upload(sid)
    prof.post(f"/teach/s/{sid}/algorithm", {"algorithm": "first_come"})
    page = text(prof.get(f"/teach/s/{sid}/setup?step=share"))
    assert "Rank soon: if too many people want the same day, whoever ranked first gets it." in page


def test_most_first_choices_gives_the_most_first_choices_and_the_draw_breaks_ties():
    # Ana and Ben both want a first; Cleo can only do a. The lottery may give Cleo nothing;
    # most first choices still places everyone it can, then the most firsts.
    prefs = {"ana": ["a", "b"], "ben": ["b", "a"], "cleo": ["a"]}
    got = _rank_max(prefs, ["ana", "ben", "cleo"], {"a": 1, "b": 1})
    assert len(got) == 2 and sum(1 for k, d in got.items() if prefs[k][0] == d) == 2
    # A tie for one seat: whoever the draw put first.
    assert _rank_max({"x": ["a"], "y": ["a"]}, ["y", "x"], {"a": 1}) == {"y": "a"}
    assert _rank_max({"x": ["a"], "y": ["a"]}, ["x", "y"], {"a": 1}) == {"x": "a"}


def test_a_ranking_keeps_its_first_time_when_changed(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    keys = day_keys(sid)
    student = browser().sign_in_student(sid, "Sam Lee")
    student.rank(sid, keys)
    first = q(prof.app, "SELECT first_at FROM submissions WHERE sheet_id = :sid", sid=sid)
    student.rank(sid, keys[::-1])
    assert first and q(prof.app, "SELECT first_at FROM submissions WHERE sheet_id = :sid", sid=sid) == first


# ---------------------------------------------------------------------------
# Around the site
# ---------------------------------------------------------------------------
def test_the_top_bar_has_a_home_button(prof):
    prof.create_sheet()
    page = raw(prof.get("/teach/"))
    assert re.search(r'<a href="/" class="icon-dock-btn dock-tip" data-tip="Home" aria-label="Home"', page)
    for tip in ("My sheets", "New sign-up sheet", "Sign-ups at a glance", "How it works",
                "Appearance: contrast, colors, text size", "Settings and sign out"):
        assert re.search(f'data-tip="{re.escape(tip)}"[^>]*aria-label="{re.escape(tip)}"', page), tip


def test_sign_ups_at_a_glance_shows_each_open_sheet_and_whats_next(prof, browser):
    sid = prof.create_sheet(title="LAW 540 Evidence")
    prof.upload(sid, SIX)
    rank(browser, sid, 2, day_keys(sid))
    page = text(prof.get("/teach/stats"))
    assert "Sign-ups at a glance" in page and "LAW 540 Evidence" in page
    assert "2 of 6 ranked (33%)" in page and "4 still to rank" in page
    assert re.search(r'<progress class="stats-bar" max="6" value="2"', raw(prof.get("/teach/stats")))
    # On the sheet's own pages, the bar copies its link for students.
    assert 'data-tip="Copy the link for students"' in raw(prof.get(f"/teach/s/{sid}"))
    assert 'data-tip="Copy the link for students"' not in raw(prof.get("/teach/"))


def test_the_canvas_step_offers_option_1_and_option_2_with_a_paste_box(prof):
    sid = prof.create_sheet(allow_unlisted=False)
    page = raw(prof.get(f"/teach/s/{sid}/setup?step=students"))
    words = text(page)
    assert "Option 1 · Easiest: Scheduler Helper does it for you" in words
    assert "Option 2 · Do it yourself: Canvas's file, or paste your own list" in words
    assert f'id="canvas-paste-{sid}"' in page and "Or paste your own list" in words
    r = prof.post(f"/teach/s/{sid}/roster", {"pasted": "Ana Lima, ana@school.edu", "setup": "1"})
    assert "Added 1 student" in follow(prof, r)


def test_every_new_sheet_has_test_students_signing_in_with_the_professors_email(prof, browser):
    sid = prof.create_sheet(allow_unlisted=False)
    rows = [r for r in _roster_rows(prof, sid) if r[1]]
    assert sorted(r[0] for r in rows) == ["Test Student 1", "Test Student 2", "Test Student 3"]
    assert {r[2] for r in rows} == {"prof@school.edu"}
    assert "Test Student 1, Test Student 2, Test Student 3" in text(prof.get(f"/teach/s/{sid}"))
    # Not open to students until a real class list is in; the professor can try it already.
    assert "your instructor is still setting this up" in text(browser().get(f"/c/{sid}"))
    stranger = browser()
    assert "isn't ready yet" in follow(stranger, stranger.post(f"/c/{sid}/login", {"name": "Ana Lima"}))
    tester = browser()
    tester.post(f"/c/{sid}/login", {"name": "Test Student 2"})
    assert tester.outbox[-1].to == ["prof@school.edu"]
    # "See it as a student" isn't ticked off until they're used.
    assert q(prof.app, "SELECT tested_at FROM sheets WHERE id = :sid", sid=sid) is None


def _roster_rows(prof, sid):
    with prof.app.app_context():
        return [(r["display_name"], r["is_test"], r["email"]) for r in db.rows(
            "SELECT display_name, is_test, email FROM roster WHERE sheet_id = :sid", sid=sid)]


# ---------------------------------------------------------------------------
# From the third test round
# ---------------------------------------------------------------------------
def test_a_progress_email_that_cant_go_out_is_sent_once_it_can(prof, browser, monkeypatch):
    sid = prof.create_sheet(allow_unlisted=False)
    prof.upload(sid, SIX)
    mailbox = {"open": False}  # (no mailbox for notes yet, or the day's limit)
    real = deadlines._can_tell
    monkeypatch.setattr(deadlines, "_can_tell", lambda: mailbox["open"] and real())
    rank(browser, sid, 3, day_keys(sid))
    assert not progress_mail(prof)
    assert q(prof.app, "SELECT half_ranked_at FROM sheets WHERE id = :sid", sid=sid) is None
    mailbox["open"] = True
    browser().sign_in_student(sid, "Di Eng", email="s3@school.edu").rank(sid, day_keys(sid))
    assert len(progress_mail(prof)) == 1


def test_a_moved_deadline_gets_its_own_two_day_email(prof, browser):
    from datetime import timedelta

    from util import iso, now

    sid = prof.create_sheet(allow_unlisted=False)
    prof.upload(sid, SIX)
    with prof.app.test_request_context():
        db.run("UPDATE sheets SET closes_at = :at WHERE id = :sid", at=iso(now() + timedelta(hours=30)), sid=sid)
        db.commit()
        assert deadlines.deadline_soon() == 1 and deadlines.deadline_soon() == 0
        db.run("UPDATE sheets SET closes_at = :at, reminder_told_at = :told WHERE id = :sid",
               at=iso(now() + timedelta(hours=40)), told=iso(now() - timedelta(days=5)), sid=sid)
        db.commit()
        assert deadlines.deadline_soon() == 1 and deadlines.deadline_soon() == 0


def test_last_call_only_before_a_future_deadline_and_with_the_right_promise(prof, browser):
    from datetime import timedelta

    from util import iso, now

    sid = prof.create_sheet(allow_unlisted=False)
    prof.upload(sid, SIX)
    with prof.app.app_context():
        db.run("UPDATE sheets SET closes_at = :at, include_unranked = 0 WHERE id = :sid",
               at=iso(now() + timedelta(days=3)), sid=sid)
        db.commit()
    page = raw(prof.get(f"/teach/s/{sid}"))
    assert "Last call before the deadline" in page and "you won&#39;t get a day on the schedule" in page
    with prof.app.app_context():
        db.run("UPDATE sheets SET closes_at = :at WHERE id = :sid", at=iso(now() - timedelta(days=1)), sid=sid)
        db.commit()
    page = html(prof.get(f"/teach/s/{sid}"))  # (passed, the instructor deciding: no date that's gone by)
    assert "are about to close" in page and "close Mon" not in page and "close Tue" not in page


def test_an_older_ranking_keeps_its_place_when_edited_under_first_come(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    keys = day_keys(sid)
    student = browser().sign_in_student(sid, "Sam Lee")
    student.rank(sid, keys)
    with prof.app.app_context():  # (saved before first_at existed)
        db.run("UPDATE submissions SET first_at = NULL, updated_at = '2026-09-01T10:00:00Z' WHERE sheet_id = :sid",
               sid=sid)
        db.commit()
    student.rank(sid, keys[::-1])
    assert q(prof.app, "SELECT first_at FROM submissions WHERE sheet_id = :sid", sid=sid) == "2026-09-01T10:00:00Z"


def test_the_progress_email_choice_comes_along_on_a_copy(prof):
    sid = prof.create_sheet()
    prof.post(f"/teach/s/{sid}/progress-emails", {})
    prof.post(f"/teach/s/{sid}/duplicate")
    copy = q(prof.app, "SELECT id FROM sheets WHERE id <> :sid ORDER BY created_at DESC LIMIT 1", sid=sid)
    assert q(prof.app, "SELECT progress_emails FROM sheets WHERE id = :sid", sid=copy) == 0


def test_how_it_works_tells_a_first_come_class_that_ranking_early_helps(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    prof.post(f"/teach/s/{sid}/algorithm", {"algorithm": "first_come"})
    student = browser().sign_in_student(sid, "Sam Lee")
    page = text(student.get("/how-it-works"))
    assert "In this class, ranking early helps" in page and "Ranking early doesn't help" not in page
    assert "Ranking early doesn't help" in text(browser().get("/how-it-works"))


def test_how_it_works_headline_and_answers_follow_the_class_option(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    prof.post(f"/teach/s/{sid}/algorithm", {"algorithm": "first_come"})
    page = text(browser().sign_in_student(sid, "Sam Lee").get("/how-it-works"))
    assert "whoever ranks first gets first pick" in page and "the students who ranked first receive it" in page
    assert "Does ranking early help?" in page and "a fair random draw decides." not in page
    prof.post(f"/teach/s/{sid}/algorithm", {"algorithm": "rank_max"})
    page = text(browser().sign_in_student(sid, "Sam Lee").get("/how-it-works"))
    assert "as many students as it can their first choice, then their second" in page
    assert "Rarely, and it can backfire" in page and "Does ranking early help?" not in page
    page = text(browser().get("/how-it-works"))  # (no class: the standard lottery's wording)
    assert "ties are broken by a fixed random draw" in page and "Rarely, and it can backfire" not in page
