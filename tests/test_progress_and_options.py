"""Emails to the instructor about how sign-ups are going, reminders for
students who haven't ranked, the ways of matching days, and the pieces of
the site around them (the Home button, the Canvas step's two options)."""

import json
import re

import db
import deadlines
from conftest import day_keys
from matching_engine import ALGORITHMS, _rank_max, build_student_prefs, compute_assignment
from test_app import follow, q, raw, text

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
    assert "half of your class" in mails[0].body and "Still to rank (3): Di Eng, Ed Fox, Flo Gray" in mails[0].body
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
        db.run("UPDATE sheets SET created_at = '2020-01-01T00:00:00Z', shared_at = '2020-01-02T00:00:00Z' "
               "WHERE id = :sid", sid=sid)
        db.commit()
        assert deadlines.quiet_weeks() == 1 and deadlines.quiet_weeks() == 0
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
    assert "Nudge the 5 who haven't ranked" in words
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
    page = raw(prof.get("/teach/"))
    assert re.search(r'<a href="/" class="icon-dock-btn" aria-label="Home"', page)


def test_the_canvas_step_offers_option_1_and_option_2_with_a_paste_box(prof):
    sid = prof.create_sheet(allow_unlisted=False)
    page = raw(prof.get(f"/teach/s/{sid}/setup?step=students"))
    words = text(page)
    assert "Option 1 · Easiest: Scheduler Helper does it for you" in words
    assert "Option 2 · Do it yourself: Canvas's file, or paste your own list" in words
    assert f'id="canvas-paste-{sid}"' in page and "Or paste your own list" in words
    r = prof.post(f"/teach/s/{sid}/roster", {"pasted": "Ana Lima, ana@school.edu", "setup": "1"})
    assert "Added 1 student" in follow(prof, r)
