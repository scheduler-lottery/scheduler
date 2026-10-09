"""
Instructor pages, all under /teach.

Every route that touches a sheet goes through owned_sheet(), which answers
"not in your account" unless the signed-in instructor created that sheet.
There is no other way in, for anyone — including the site's owner.
"""

import hashlib
import json
import math
import os
import re
import unicodedata
from collections import Counter
from datetime import timedelta
from itertools import zip_longest

from flask import (
    Blueprint, Response, abort, flash, g, jsonify, make_response, redirect, render_template, request,
    session, url_for,
)

import auth
import compose
import db
import settings
import sheets
import signin
from auth import current_instructor, instructor_required
from matching_engine import (
    ALGORITHMS, CHOOSABLE, COMPARE_TRIALS, DEFAULT_ALGORITHM, MANUAL, compare_algorithms,
    compute_assignment, fill_open_seats, verdict,
)
from roster import describe, find_email, flip_last_first, parse_roster, _is_last_first
from util import (
    clean_name, clean_text, date_label, fold, iso, mask_email, new_id, normalize_name, now, parse_iso,
    plural, read_date, slugify, valid_email,
)

bp = Blueprint("teach", __name__, url_prefix="/teach")

MAX_UPLOAD_BYTES = 1024 * 1024
BACKUP_EMAIL_GAP_MINUTES = 10
TITLE_LIMIT = 100
NOTE_LIMIT = 2000
DAY_LABEL_LIMIT = 80
RANK_BY_LIMIT = 100
PERSONAL_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com", "icloud.com",
    "me.com", "aol.com", "proton.me", "protonmail.com", "msn.com",
}


def _zone():
    instructor = current_instructor()
    return (instructor and instructor.get("timezone")) or settings.DEFAULT_TIMEZONE


# ---------------------------------------------------------------------------
# Signing in
# ---------------------------------------------------------------------------
def email_allowed(email):
    if settings.OWNER_EMAIL and email == settings.OWNER_EMAIL:
        return True
    domain = email.rsplit("@", 1)[-1]
    for allowed in settings.INSTRUCTOR_EMAIL_DOMAINS:
        if allowed == "*":
            return True
        allowed = allowed.lstrip(".")
        if domain == allowed or domain.endswith("." + allowed):
            return True
    return False


def clean_email_input(raw):
    """The address in whatever was typed or pasted: "Pat Lee <pat@x.edu>",
    "mailto:pat@x.edu", a trailing period, stray spaces, a full-width ＠, or
    invisible characters picked up when copying from a web page."""
    text = unicodedata.normalize("NFKC", raw or "")
    text = "".join(ch for ch in text if unicodedata.category(ch) not in ("Cf", "Cc") or ch in " \t").strip()
    if "<" in text and ">" in text:
        text = text[text.index("<") + 1:text.rindex(">")]
    text = text.strip().strip("<>\"' ")
    if text.lower().startswith("mailto:"):
        text = text[7:]
    text = text.split("?")[0]
    return "".join(text.split()).rstrip(".,;").lower()


def email_problem(email, for_instructor=False):
    """What's wrong with a typed address, in plain words ('' if nothing)."""
    if not email:
        return "Type an email address."
    if "@" not in email:
        return "That's missing the @ — type the full address, like name@school.edu."
    if email.count("@") > 1:
        if "," in email or ";" in email:
            return "That looks like two addresses — type just one."
        return "That has two @ signs — check the address."
    local, domain = email.split("@")
    if not local:
        return "Something's missing before the @."
    if "," in domain:
        return "There's a comma in that address where a dot should be."
    if "." not in domain:
        return "That address is missing its ending, like .edu."
    if ".." in email or domain.startswith(".") or local.endswith("."):
        return "That address has two dots in a row — check it."
    ending = domain.rsplit(".", 1)[-1]
    if ending in ("ed", "eud", "deu", "eduu", "edi"):
        return f"That address ends in .{ending} — did you mean .edu?"
    parts = domain.split(".")
    if len(parts) >= 3 and parts[-1] == parts[-2]:
        return f"That address ends in .{parts[-2]}.{parts[-1]} — did you mean .{parts[-1]}?"
    if not valid_email(email):
        return "That doesn't look like a complete email address."
    if for_instructor and domain in PERSONAL_EMAIL_DOMAINS and not email_allowed(email):
        return f"{domain} is a personal address. Instructor accounts need your school address."
    return ""


def _instructor_code_email():
    subject = "{code} is your " + settings.APP_NAME + " sign-in code"
    body = (
        f"Your {settings.APP_NAME} sign-in code is:\n\n"
        "    {code}\n\n"
        f"Type it on the sign-in page. It works for {signin.CODE_EXPIRY_MINUTES} minutes.\n\n"
        "Didn't ask for this? You can ignore this email — nothing happens without the code.\n"
    )
    return subject, body


def _send_instructor_code(email):
    subject, body = _instructor_code_email()
    return signin.issue_code(
        scope=signin.INSTRUCTOR_SCOPE, subject=email, display_name=email, email=email,
        email_subject=subject, email_body=body, kind="teach",
    )


def _valid_zone(name):
    if not name or len(name) > 60:
        return ""
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(name)
        return name
    except Exception:
        return ""


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_instructor():
        return redirect(url_for("teach.dashboard"))
    email = clean_email_input(request.values.get("email"))
    if request.method == "POST":
        problem = email_problem(email, for_instructor=True)
        if problem:
            flash(problem, "error")
        elif not email_allowed(email):
            contact = settings.CONTACT_EMAIL
            flash(
                "Instructor accounts are for school email addresses"
                + (f" ending in {', '.join(settings.INSTRUCTOR_EMAIL_DOMAINS)}" if "*" not in settings.INSTRUCTOR_EMAIL_DOMAINS else "")
                + ". Use your school address"
                + (f" — or, if your school's addresses end differently, write to {contact} to have it added." if contact
                   else " — or, if your school's addresses end differently, ask whoever runs this site to add it."),
                "error",
            )
        elif db.scalar("SELECT disabled FROM instructors WHERE email = :e", e=email):
            flash(auth.switched_off_message(), "error")
        else:
            try:
                issued = _send_instructor_code(email)
            except signin.SendRefused as refused:
                flash(str(refused), "error")
            else:
                session["pending_teach"] = email
                session["pending_tz"] = _valid_zone(request.form.get("tz"))
                if issued.dev_code:
                    session["dev_code"] = issued.dev_code
                if issued.status == "recent":
                    flash(issued.note, "info")
                return redirect(url_for("teach.verify"))
    return render_template("teach_login.html", email=email, domains=settings.INSTRUCTOR_EMAIL_DOMAINS)


def _sign_in(email):
    at = iso()
    zone = session.pop("pending_tz", "") or ""
    found = db.row("SELECT id FROM instructors WHERE email = :e", e=email)
    if found:
        instructor_id = found["id"]
        db.run(
            "UPDATE instructors SET last_login_at = :at, timezone = CASE WHEN :tz <> '' THEN :tz ELSE timezone END "
            "WHERE id = :id",
            at=at, tz=zone, id=instructor_id,
        )
    else:
        instructor_id = new_id(12)
        db.run(
            "INSERT INTO instructors (id, email, disabled, timezone, created_at, last_login_at) "
            "VALUES (:id, :e, 0, :tz, :at, :at)",
            id=instructor_id, e=email, tz=zone, at=at,
        )
    db.commit()
    session["instructor_id"] = instructor_id
    session.permanent = True
    g.pop("instructor", None)


def _verify_page(email):
    record = signin.pending_code(signin.INSTRUCTOR_SCOPE, email)
    return render_template(
        "verify.html",
        for_student=False,
        who=None,
        shown_email=email,
        new_account=not db.scalar("SELECT 1 FROM instructors WHERE email = :e", e=email),
        sent_at=record["last_sent_at"] if record else None,
        expires_at=record["expires_at"] if record else None,
        expired=bool(record) and parse_iso(record["expires_at"]) <= now(),
        locked=False,
        verify_url=url_for("teach.verify"),
        resend_url=url_for("teach.resend"),
        restart_url=url_for("teach.restart"),
        restart_label="Wrong address? Change it",
        expiry=signin.CODE_EXPIRY_MINUTES,
        code_length=signin.CODE_LENGTH,
        dev_code=session.get("dev_code"),
        sender=signin.code_sender(),
    )


@bp.route("/verify", methods=["GET", "POST"])
def verify():
    email = session.get("pending_teach")
    if not email:
        return redirect(url_for("teach.login"))
    if request.method == "POST":
        check = signin.check_code(signin.INSTRUCTOR_SCOPE, email, request.form.get("code"))
        if check.status == "ok":
            session.pop("pending_teach", None)
            session.pop("dev_code", None)
            _sign_in(email)
            destination = session.pop("teach_next", None)
            if not (destination and destination.startswith(("/teach", "/owner"))):
                destination = url_for("teach.dashboard")
            return redirect(destination)
        if check.status != "missing":
            flash(signin.check_message(check, for_student=False), "error")
            return _verify_page(email)
    if not signin.pending_code(signin.INSTRUCTOR_SCOPE, email):
        session.pop("pending_teach", None)
        flash("That sign-in attempt expired — please start again.", "error")
        return redirect(url_for("teach.login", email=email))
    return _verify_page(email)


@bp.route("/verify/resend", methods=["POST"])
def resend():
    email = session.get("pending_teach")
    if not email:
        return redirect(url_for("teach.login"))
    try:
        issued = _send_instructor_code(email)
    except signin.SendRefused as refused:
        flash(str(refused), "error")
    else:
        if issued.dev_code:
            session["dev_code"] = issued.dev_code
        flash(issued.note if issued.status == "recent" else "Sent a new code — use the newest email.",
              "info" if issued.status == "recent" else "success")
    return redirect(url_for("teach.verify"))


@bp.route("/verify/restart", methods=["POST"])
def restart():
    email = session.pop("pending_teach", None) or ""
    session.pop("dev_code", None)
    return redirect(url_for("teach.login", email=email))


@bp.route("/logout", methods=["POST"])
def logout():
    for key in ("instructor_id", "pending_teach", "dev_code", "teach_next", "roster_report", "undo"):
        session.pop(key, None)
    auth.sign_out_all_students()  # including "see it as a student" sign-ins
    if request.form.get("next") == "login":
        flash("You're signed out. Sign in with the other email address.", "success")
        return redirect(url_for("teach.login"))
    flash("You're signed out.", "success")
    return redirect(url_for("landing"))


# ---------------------------------------------------------------------------
# Dashboard and creating sheets
# ---------------------------------------------------------------------------
def _not_in_account(sid):
    instructor = current_instructor()
    deleted = db.row(
        "SELECT id, created_at FROM snapshots WHERE sheet_id = :sid AND owner_id = :me AND kind = 'deleted' "
        "ORDER BY created_at DESC LIMIT 1",
        sid=(sid or "").lower(), me=instructor["id"],
    ) if instructor else None
    page = render_template("not_in_account.html", deleted=deleted)
    return make_response(page, 404)


def owned_sheet(sid):
    """The sheet, if the signed-in instructor owns it. Otherwise the same
    "not in your account" page whether it exists or not, so nothing leaks."""
    sheet = sheets.get_sheet(sid)
    instructor = current_instructor()
    if not sheet or not instructor or sheet["owner_id"] != instructor["id"]:
        abort(_not_in_account(sid))
    return sheet


def _own_snapshot(snap_id):
    snap = sheets.get_snapshot(snap_id, current_instructor()["id"])
    if not snap:
        abort(404)
    return snap


@bp.route("/")
@instructor_required
def dashboard():
    instructor = current_instructor()
    mine = db.rows(
        """
        SELECT s.id, s.title, s.bidding_open, s.allow_unlisted, s.published_at, s.created_at, s.archived_at,
            (SELECT COUNT(*) FROM roster r WHERE r.sheet_id = s.id AND r.is_test = 0) AS roster_count,
            (SELECT COUNT(*) FROM submissions x JOIN roster r
                ON r.sheet_id = x.sheet_id AND r.name_key = x.name_key AND r.is_test = 0
                WHERE x.sheet_id = s.id) AS listed_count,
            (SELECT COUNT(*) FROM submissions x WHERE x.sheet_id = s.id AND NOT EXISTS (
                SELECT 1 FROM roster r WHERE r.sheet_id = x.sheet_id AND r.name_key = x.name_key
            )) AS unlisted_count,
            (SELECT COUNT(*) FROM assignments a WHERE a.sheet_id = s.id) AS assigned_count,
            (SELECT label FROM sheet_days d WHERE d.sheet_id = s.id ORDER BY sort_order LIMIT 1) AS first_day,
            (SELECT label FROM sheet_days d WHERE d.sheet_id = s.id ORDER BY sort_order DESC LIMIT 1) AS last_day
        FROM sheets s WHERE s.owner_id = :me
        ORDER BY s.created_at DESC
        """,
        me=instructor["id"],
    )
    titles = [fold(s["title"]) for s in mine]
    for s in mine:
        s["same_title"] = titles.count(fold(s["title"])) > 1
    download = request.args.get("download", "")
    download_url = (
        url_for("teach.snapshot_zip", snap_id=download)
        if download and sheets.get_snapshot(download, instructor["id"]) else None
    )
    return render_template(
        "teach_dashboard.html", sheets=[s for s in mine if not s["archived_at"]],
        archived=[s for s in mine if s["archived_at"]], deleted=sheets.deleted_sheets(instructor["id"]),
        download_url=download_url,
    )


def _blank_form():
    return {
        "title": "",
        "days": [],
        "capacity": 4,
        "note": "",
        "rank_by": "",
        "allow_unlisted": False,
        "show_preview": False,
    }


def _read_days(form):
    """The days from the form: dates picked on the calendar (YYYY-MM-DD) —
    each checked to be a real date — plus any day saved before days were
    picked on a calendar, kept by its key. Returns (days, problems)."""
    keys, dates, labels = form.getlist("day_key"), form.getlist("day_date"), form.getlist("day_label")
    picked, undated, problems = {}, [], []
    for key, raw, label in zip_longest(keys, dates, labels, fillvalue=""):
        key, raw = (key or "").strip()[:20], (raw or "").strip()
        if raw:
            day = read_date(raw)
            if day is None:
                problems.append(f"“{raw[:20]}” isn't a real date. Pick the days on the calendar.")
            else:
                picked.setdefault(day, key)
        elif key and (label or "").strip():
            undated.append({"key": key, "label": clean_text(label, DAY_LABEL_LIMIT + 50), "date": None})
    today = now().date()
    outside = [d for d in picked if not (today - timedelta(days=366) <= d <= today + timedelta(days=3 * 366))]
    if outside:
        problems.append(f"{date_label(outside[0], True)} is too far from today — pick days within the next "
                        "three years.")
    with_year = len({d.year for d in picked}) > 1
    days = [{"key": key, "label": date_label(day, with_year), "date": day.isoformat()}
            for day, key in sorted(picked.items())] + undated
    return days, problems


def _read_sheet_form(form):
    """Returns (values, errors). Values are shaped like _blank_form() so the
    page can be shown again with everything the instructor typed."""
    filled, day_problems = _read_days(form)
    title = clean_text(form.get("title"), TITLE_LIMIT + 50)
    note = clean_text(form.get("note"), NOTE_LIMIT + 50, keep_newlines=True)
    rank_by = clean_text(form.get("rank_by"), RANK_BY_LIMIT + 50)
    values = {
        "title": title,
        "days": filled,
        "capacity": (form.get("capacity") or "").strip(),
        "note": note,
        "rank_by": rank_by,
        "allow_unlisted": form.get("allow_unlisted", "0") == "1",
        "show_preview": form.get("show_preview") == "1",
    }
    errors = []
    if not title:
        errors.append("Give your sign-up sheet a title — it's what your students will see.")
    elif len(title) > TITLE_LIMIT:
        errors.append(f"The title is {len(title)} characters; the most is {TITLE_LIMIT}.")
    if len(note) > NOTE_LIMIT:
        errors.append(f"The note to students is {len(note)} characters; the most is {NOTE_LIMIT}.")
    if len(rank_by) > RANK_BY_LIMIT:
        errors.append(f"The “rank by” date is too long — keep it under {RANK_BY_LIMIT} characters.")
    errors += day_problems
    if len(filled) < 2:
        errors.append("Pick at least two days on the calendar for students to choose between.")
    if len(filled) > sheets.MAX_DAYS:
        errors.append(f"That's {len(filled)} days — the most is {sheets.MAX_DAYS}. Please trim the list.")
    try:
        capacity = int(values["capacity"])
    except ValueError:
        errors.append("Seats per day should be a whole number, like 4.")
    else:
        if capacity < 1:
            errors.append("Seats per day needs to be at least 1.")
        elif capacity > 500:
            errors.append("Seats per day can be at most 500.")
        else:
            values["capacity"] = capacity
    return values, errors


@bp.route("/new", methods=["GET", "POST"])
@instructor_required
def new_sheet():
    if request.method == "POST":
        values, errors = _read_sheet_form(request.form)
        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("sheet_form.html", form=values, mode="new", max_days=sheets.MAX_DAYS)
        mine = [fold(r["title"]) for r in db.rows(
            "SELECT title FROM sheets WHERE owner_id = :me", me=current_instructor()["id"]
        )]
        sid = sheets.new_sheet_id()
        at = iso()
        db.run(
            """
            INSERT INTO sheets (id, owner_id, title, note, rank_by, capacity_per_day, algorithm, bidding_open,
                allow_unlisted, show_preview, include_unranked, lottery_seed, created_at, updated_at)
            VALUES (:sid, :owner, :title, :note, :rank_by, :capacity, :algo, 1, :unlisted, :preview, 1, :seed,
                :at, :at)
            """,
            sid=sid, owner=current_instructor()["id"], title=values["title"], note=values["note"],
            rank_by=values["rank_by"], capacity=values["capacity"], algo=DEFAULT_ALGORITHM,
            unlisted=int(values["allow_unlisted"]), preview=int(values["show_preview"]),
            seed=sheets.new_lottery_seed(), at=at,
        )
        sheets.save_days(sid, [("", d["label"], d["date"]) for d in values["days"]])
        db.commit()
        _warn_past_days(values["days"])
        flash("Your sign-up sheet is ready. Next: "
              + ("share the link with your class (or add your class list)." if values["allow_unlisted"]
                 else "add your class list."), "success")
        if fold(values["title"]) in mine:
            flash("You already have a sheet with this title — the link code under each title on your list "
                  "tells them apart.", "info")
        return redirect(url_for("teach.sheet", sid=sid) + "#class-list")
    return render_template("sheet_form.html", form=_blank_form(), mode="new", max_days=sheets.MAX_DAYS)


def _warn_past_days(days):
    today = now().date()
    past = [d["label"] for d in days if d["date"] and read_date(d["date"]) < today]
    if past:
        flash(f"Heads up: {', '.join(past[:3])}{' …' if len(past) > 3 else ''} "
              f"{'has' if len(past) == 1 else 'have'} already passed. If that's a mistake, change it under "
              "“Edit days & settings”.", "info")


def _form_version(sheet, days):
    """Changes whenever the sheet's editable settings do, so a stale Edit
    page left open in another tab can't put back what was changed since."""
    parts = [sheet["title"], sheet["note"], sheet["rank_by"], sheet["capacity_per_day"],
             sheet["allow_unlisted"], sheet["show_preview"], [[d["key"], d["label"], d["date"]] for d in days]]
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:16]


def _edit_context(sheet, days):
    counts = sheets.counts(sheet["id"])
    return {"sheet": sheet, "version": _form_version(sheet, days), "counts": counts, "max_days": sheets.MAX_DAYS}


@bp.route("/s/<sid>/edit", methods=["GET", "POST"])
@instructor_required
def edit_sheet(sid):
    sheet = owned_sheet(sid)
    days = sheets.get_days(sheet["id"])
    if request.method == "POST":
        values, errors = _read_sheet_form(request.form)
        # A date picked again is the same day as before (same rankings and
        # notes), whatever the page sent along with it.
        by_date = {d["date"]: d["key"] for d in days if d["date"]}
        for d in values["days"]:
            if d["date"]:
                d["key"] = by_date.get(d["date"], "")
        if request.form.get("version") != _form_version(sheet, days):
            flash(
                "This sheet was changed in another window or tab after you opened this page, so nothing was "
                "saved yet. Your changes are below — check them against the sheet, then press Save again.",
                "error",
            )
            return render_template("sheet_form.html", form=values, mode="edit", **_edit_context(sheet, days))
        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("sheet_form.html", form=values, mode="edit", **_edit_context(sheet, days))
        kept_keys = {d["key"] for d in values["days"]}
        removing = [d for d in days if d["key"] not in kept_keys]
        impact = sheets.day_impact(sheet["id"], [d["key"] for d in removing]) if removing else None
        affected = impact and (impact["ranked"] or impact["scheduled"] or impact["notes"])
        if affected and not request.form.get("confirm_remove"):
            return render_template(
                "sheet_form.html", form=values, mode="edit", removing=removing, impact=impact,
                **_edit_context(sheet, days),
            )
        old_labels = {d["key"]: d["label"] for d in days}
        renamed = [(old_labels[d["key"]], d["label"]) for d in values["days"]
                   if not d["date"] and d["key"] in old_labels and old_labels[d["key"]] != d["label"]]
        has_work = db.scalar("SELECT 1 FROM submissions WHERE sheet_id = :sid LIMIT 1", sid=sheet["id"]) or \
            db.scalar("SELECT 1 FROM assignments WHERE sheet_id = :sid LIMIT 1", sid=sheet["id"])
        if affected:
            sheets.take_snapshot(sheet, "edit", "Before removing " + ", ".join(d["label"] for d in removing))
        elif renamed and has_work:
            sheets.take_snapshot(sheet, "edit", "Before renaming " + ", ".join(old for old, _ in renamed))
        db.run(
            """
            UPDATE sheets SET title = :title, note = :note, rank_by = :rank_by, capacity_per_day = :capacity,
                allow_unlisted = :unlisted, show_preview = :preview, updated_at = :at
            WHERE id = :sid
            """,
            title=values["title"], note=values["note"], rank_by=values["rank_by"], capacity=values["capacity"],
            unlisted=int(values["allow_unlisted"]), preview=int(values["show_preview"]), at=iso(),
            sid=sheet["id"],
        )
        removed = sheets.save_days(sheet["id"], [(d["key"], d["label"], d["date"]) for d in values["days"]])
        db.commit()
        flash("Saved.", "success")
        _warn_past_days([d for d in values["days"] if d["key"] not in old_labels])
        if renamed and has_work:
            flash(
                "Renamed " + "; ".join(f"“{old}” → “{new}”" for old, new in renamed) + ". Students who ranked "
                + ("it keep it" if len(renamed) == 1 else "them keep them") + " in the same place on their list"
                + (", and anyone scheduled on " + ("it" if len(renamed) == 1 else "them") + " now sees the new name"
                   if sheet["published_at"] else "")
                + ". (A copy from before is saved under Backups.)",
                "info",
            )
        added = [d["label"] for d in values["days"] if d["key"] not in old_labels]
        has_rankings = db.scalar("SELECT 1 FROM submissions WHERE sheet_id = :sid LIMIT 1", sid=sheet["id"])
        if added and has_rankings:
            flash(
                "Added " + ", ".join(f"“{label}”" for label in added) + ". Students who already ranked will see "
                "it at the bottom of their list until they move it — their pages ask them to check.",
                "info",
            )
        if removed:
            flash(
                "Removed " + ", ".join(f"“{d['label']}”" for d in removed) + "."
                + (" A copy from before is saved under Backups." if affected else "")
                + (" Anyone who was scheduled on it needs a new day — see the Schedule section."
                   if impact and impact["scheduled"] else ""),
                "info",
            )
        if values["allow_unlisted"] is False and sheet["allow_unlisted"]:
            unlisted = sheets.counts(sheet["id"])["unlisted_submissions"]
            if unlisted:
                flash(
                    f"Only names on your list can sign in now. {plural(unlisted, 'person', 'people')} not on your "
                    "list already ranked — their rankings are still under Sign-ups; delete them if they shouldn't count.",
                    "info",
                )
        return redirect(url_for("teach.sheet", sid=sheet["id"]))
    form = {
        "title": sheet["title"],
        "days": days,
        "capacity": sheet["capacity_per_day"],
        "note": sheet["note"],
        "rank_by": sheet["rank_by"],
        "allow_unlisted": bool(sheet["allow_unlisted"]),
        "show_preview": bool(sheet["show_preview"]),
    }
    return render_template("sheet_form.html", form=form, mode="edit", **_edit_context(sheet, days))


@bp.route("/s/<sid>/duplicate", methods=["POST"])
@instructor_required
def duplicate_sheet(sid):
    sheet = owned_sheet(sid)
    new_sid = sheets.copy_sheet(sheet, current_instructor()["id"])
    db.commit()
    flash(
        f"Made a copy of “{sheet['title']}” with the same days and settings — but no class list or "
        "rankings. Change the title and dates for the new term, then press Save.",
        "success",
    )
    return redirect(url_for("teach.edit_sheet", sid=new_sid))


# ---------------------------------------------------------------------------
# The sheet page
# ---------------------------------------------------------------------------
def _sign_in_line(sheet, counts):
    if counts["roster"] and counts["roster_with_email"] == counts["roster"]:
        sender = signin.code_sender()
        line = (f"Type your name, then enter the 6-digit code emailed to your school address"
                f"{f' (it comes from {sender})' if sender else ''}.")
        if sheet["allow_unlisted"]:
            line += " If your name isn't on the list, you can still sign up: you'll choose a PIN (a number only you know)."
        return line
    if counts["roster_with_email"]:
        return ("Type your name. If you get an email with a 6-digit code, enter it; otherwise you'll choose a PIN "
                "(a number only you know). Remember your PIN — if you forget it, ask me to reset it.")
    return ("Type your full name and choose a PIN (a number only you know). Remember it — if you forget it, "
            "ask me to reset it.")


def _announcement(sheet, share_url, counts):
    if not sheet["allow_unlisted"] and not counts["roster"]:
        return ""
    lines = [
        f"Please rank the presentation days for {sheet['title']}:",
        share_url,
        "",
        _sign_in_line(sheet, counts),
        "",
        "Then put the days in your real order of preference, favorite at the top, and press “Save my ranking”. "
        "After that it saves as you go, and you can change it until sign-ups close."
        + (f" Please rank by {sheet['rank_by']}." if sheet["rank_by"] else ""),
    ]
    if sheet["algorithm"] != "da_day_favorable":
        lines.append("Ranking early doesn't help: if too many people want the same day, a fair random draw decides.")
    lines.append("Once I publish the schedule, you'll see your day on that same page.")
    if sheet["note"]:
        lines += ["", sheet["note"]]
    return "\n".join(lines)


def _publish_announcement(sheet, share_url, counts, without_day=0):
    if counts["roster"] and counts["roster_with_email"] == counts["roster"]:
        how = "Type your name, then enter the 6-digit code emailed to your school address."
    elif counts["roster_with_email"]:
        how = ("Type your name, then enter the 6-digit code from your email — or, if you signed up with a PIN, "
               "your PIN.")
    else:
        how = "Type your name and your PIN (or choose one, if you never signed in)."
    after = ("If the page says you don't have a day yet, or a day doesn't work for you, email me." if without_day
             else "If your day doesn't work for you, email me.")
    return (
        f"The presentation schedule for {sheet['title']} is ready. Open this link to see your day:\n{share_url}\n\n"
        f"{how} {after}"
    )


def _next_step(sheet, counts, has_schedule, stale, published, without_day=0, stuck=None, fillable=0,
               open_seats=0):
    """The one thing to do next, for the card at the top of the sheet page."""
    if not counts["roster"] and not sheet["allow_unlisted"]:
        return {"title": "Add your class list", "anchor": "class-list",
                "text": "Students sign in by finding their name on it. Drag in the file from Canvas — "
                        "“Where do I find this in Canvas?” shows you how."}
    if not sheet["bidding_open"] and not counts["submissions"] and not has_schedule:
        return {"title": "Reopen sign-ups so students can rank", "anchor": "signups",
                "text": "Sign-ups are closed and there are no rankings, so students who open the link can't do "
                        "anything yet."}
    if stuck:
        return {"title": f"{plural(len(stuck['people']), 'student')} couldn't get a sign-in email today",
                "anchor": "stuck", "text": stuck["summary"]}
    if not sheet["shared_at"] and not counts["submissions"]:
        return {"title": "Share the link with your class", "anchor": "share",
                "text": "Copy the ready-made message below and post it in Canvas."}
    if sheet["bidding_open"]:
        if counts["roster"] and counts["waiting"]:
            return {"title": f"Wait for rankings — {counts['listed_submissions']} of {counts['roster']} so far",
                    "anchor": "signups",
                    "text": "When everyone's in (or your deadline passes), close sign-ups and make the schedule."}
        return {"title": "Close sign-ups and make the schedule", "anchor": "schedule",
                "text": ("Everyone on your list has ranked." if counts["roster"] else
                         f"{plural(counts['submissions'], 'person has', 'people have')} ranked so far.")
                        + " When you're ready, one button does both."}
    if not has_schedule:
        return {"title": "Make the schedule", "anchor": "schedule",
                "text": "Sign-ups are closed. Make the schedule, check it, then publish it."}
    if stale and not published:
        return {"title": "Make the schedule again", "anchor": "schedule",
                "text": "Rankings or settings changed after you made it. Nobody has seen it yet, so making it "
                        "again is safe."}
    if without_day:
        if fillable:
            how = (f"Press “Give them the open seats” under Schedule: {plural(fillable, 'student')} "
                   f"{'gets' if fillable == 1 else 'get'} a day, and nobody else moves.")
        elif not open_seats:
            how = ("There are no open seats. Raise seats per day (or add a day)"
                   + (", then press “Give them the open seats”" if published else ", then make the schedule again")
                   + " — or move them by hand.")
        else:
            how = ("The only open seats are on days they said they can't do. Read their notes under Schedule "
                   "and move them by hand.")
        return {"title": f"{plural(without_day, 'student')} still {'has' if without_day == 1 else 'have'} no day",
                "anchor": "no-day",
                "text": ("They'd see “you don't have a presentation day” once you publish. " if not published else
                         "They can see they have no day. ") + how}
    if not published:
        return {"title": "Check the schedule, then publish it", "anchor": "schedule",
                "text": "Publishing shows each student their day on the class page."}
    return {"title": "All done — students can see their day", "anchor": "schedule",
            "text": "Everyone has a day. Download the schedule for your records. You can still move people; "
                    "they'll see changes right away."}


def _seat_warning(sheet, days, counts):
    """When there are more people to place than seats. Students who haven't
    ranked count only if they're set to get leftover seats."""
    if counts["roster"] and sheet["include_unranked"]:
        people = counts["roster"] + counts["unlisted_submissions"]
    else:
        people = counts["submissions"]
    seats = sheet["capacity_per_day"] * len(days)
    if not days or people <= seats:
        return None
    need = math.ceil(people / len(days))
    more_days = math.ceil((people - seats) / sheet["capacity_per_day"])
    return {"people": people, "seats": seats, "need": need, "more_days": more_days,
            "ranked": counts["submissions"], "counting_unranked": bool(counts["roster"] and sheet["include_unranked"])}


def _domain_typos(roster_rows):
    """Emails whose domain is a near-miss of the one most of the class uses
    ("exmaple.edu" among "example.edu"): {name_key: likely domain}."""
    from roster import _close

    domains = Counter(r["email"].split("@")[1] for r in roster_rows if r["email"] and not r["is_test"])
    if not domains:
        return {}
    common, count = domains.most_common(1)[0]
    if count < 3:
        return {}
    return {
        r["name_key"]: common for r in roster_rows
        if r["email"] and r["email"].split("@")[1] != common and _close(r["email"].split("@")[1], common)
    }


def _domain_typo_note(sheet, email):
    """A warning if a typed email's domain looks like a typo of the class's."""
    if not email:
        return ""
    rows = sheets.get_roster(sheet["id"])
    probe = rows + [{"name_key": "__new__", "email": email, "is_test": 0}]
    likely = _domain_typos(probe).get("__new__")
    if likely:
        return f" Check the email: it ends in “{email.split('@')[1]}” — did you mean “{likely}”?"
    return ""


def _submission_view(r, labels, order, roster_keys, tests, pins, locked):
    ranking = [d for d in json.loads(r["ranking"]) if d in labels]
    comments = json.loads(r["comments"])
    return {
        "key": r["name_key"],
        "name": r["display_name"],
        "ranking": [labels[d] for d in ranking],
        "unranked_days": [labels[d] for d in order if d not in ranking],
        "excluded": [labels[d] for d in order if d in set(json.loads(r["excluded_days"]))],
        "comments": [(labels[d], str(comments[d]).strip()) for d in order if str(comments.get(d, "")).strip()],
        "updated_at": r["updated_at"],
        "is_test": r["name_key"] in tests,
        "unlisted": r["name_key"] not in roster_keys,
        "has_pin": r["name_key"] in pins,
        "locked": r["name_key"] in locked,
    }


# Why a student couldn't get a sign-in email (signin.SendRefused reasons),
# for the instructor's list.
STUCK_REASONS = {
    "site-day": "the site's sign-in emails ran out for today",
    "site-busy": "the site was low on emails, so repeat codes were on hold",
    "class-day": "this class's sign-in emails ran out for today",
    "class-hour": "a lot of codes went out for this class within an hour",
    "address": "a lot of codes went to their address within an hour",
    "browser": "one browser asked for codes for several names",
    "network": "a lot of emails went out from one network",
    "paused": "signing in as them is paused after many wrong codes",
    "failed": "the email couldn't be sent",
}


def _stuck(sid, roster_rows, locked):
    """Students on the list who couldn't get a sign-in email today and
    haven't signed in since, for the sheet page — or None."""
    refusals = signin.refusals_today(sid) if signin.codes_live() else {}
    listed = {r["name_key"]: r for r in roster_rows}
    links = signin.instructor_links(sid) if refusals else {}
    people = [
        {"key": key, "name": listed[key]["display_name"], "email": listed[key]["email"], "kind": kind, "at": at,
         "reason": STUCK_REASONS.get(kind, "the email couldn't go out"), "paused": key in locked,
         "link_at": links.get(key) if links.get(key, "") >= at else None}
        for key, (kind, at) in sorted(refusals.items(), key=lambda item: item[1][1])
        if key in listed
    ]
    if not people:
        return None
    kinds = {p["kind"] for p in people}
    if "site-day" in kinds:
        why = "This site's sign-in emails ran out for today (they come back within a day)."
    elif "class-day" in kinds:
        why = "This class used up its sign-in emails for today (more tomorrow)."
    elif kinds == {"paused"}:
        why = "Signing in is paused for them after many wrong codes."
    else:
        why = "Some limits on sign-in emails got in the way."
    return {"people": people,
            "summary": why + " Help each one under Sign-ups: “Sign-in link” starts an email from your own "
                       "account with a link that signs them in"
                       + (", and “Unlock” lifts a pause." if "paused" in kinds else ".")}


def _fill_plan(sheet, inputs, assignment_rows, roster_rows):
    """Who “Give them the open seats” would place right now, and where:
    [(key, name, day, method)]. Nobody already on the schedule moves."""
    known = set(inputs.day_keys)
    existing = {a["name_key"]: a["day_key"] for a in assignment_rows if a["day_key"] in known}
    ranked = {s["key"] for s in inputs.students}
    unranked = [(r["name_key"], r["display_name"]) for r in roster_rows
                if not r["is_test"] and r["name_key"] not in existing and r["name_key"] not in ranked]
    return fill_open_seats(inputs.students, inputs.day_keys, sheet["capacity_per_day"], existing,
                           algorithm=inputs.algorithm, seed=sheet["lottery_seed"], unranked=unranked)


def _changed_emails(sheet, snap_id, roster_rows):
    """Emails of the students whose day is different from the one in a
    restore point — for “Copy their emails” after the schedule changed."""
    snap = sheets.get_snapshot(snap_id, sheet["owner_id"])
    if not snap:
        return []
    then = {a["name_key"]: a["day_key"] for a in json.loads(snap["data"]).get("assignments", [])}
    now_on = {a["name_key"]: a["day_key"] for a in sheets.get_assignments(sheet["id"])}
    changed = {k for k in set(then) | set(now_on) if then.get(k) != now_on.get(k)}
    return [r["email"] for r in roster_rows if r["name_key"] in changed and r["email"] and not r["is_test"]]


@bp.route("/s/<sid>")
@instructor_required
def sheet(sid):
    sheet = owned_sheet(sid)
    sid = sheet["id"]
    days = sheets.get_days(sid)
    labels = {d["key"]: d["label"] for d in days}
    order = [d["key"] for d in days]
    counts = sheets.counts(sid)
    roster_rows = sheets.get_roster(sid)
    roster_keys = {r["name_key"] for r in roster_rows}
    tests = {r["name_key"] for r in roster_rows if r["is_test"]}
    pins = {r["name_key"] for r in db.rows("SELECT name_key FROM name_pins WHERE sheet_id = :sid", sid=sid)}
    locked = signin.locked_names(sid)
    submission_rows = sheets.get_submissions(sid)
    submissions = [_submission_view(r, labels, order, roster_keys, tests, pins, locked) for r in submission_rows]
    real_submissions = [s for s in submissions if not s["is_test"]]
    submitted = {s["key"] for s in submissions}
    not_yet = [r for r in roster_rows if not r["is_test"] and r["name_key"] not in submitted]
    unlisted = [s for s in submissions if s["unlisted"]]
    link_choices = [r for r in roster_rows if not r["is_test"] and r["name_key"] not in submitted]
    from roster import match_name

    for s in unlisted:
        _row, suggestions = match_name(s["name"], link_choices)
        s["likely"] = suggestions[0]["name_key"] if suggestions else ""
    # People who chose a PIN but haven't ranked (or whose ranking was
    # deleted): they still need a way to have it reset.
    names = {r["name_key"]: r["display_name"] for r in roster_rows}
    pin_waiting = [
        {"key": key, "name": names.get(key) or key.title(), "listed": key in names, "locked": key in locked}
        for key in sorted(pins - submitted)
        if not (key in names and next(r for r in roster_rows if r["name_key"] == key)["email"])
    ]

    inputs = sheets.schedule_inputs(sheet)
    assignment_rows = sheets.get_assignments(sid)
    placed = sheets.schedule_rows(days, assignment_rows, submission_rows, roster_rows, sheet["capacity_per_day"])
    by_day = sheets.group_by_day(days, placed)
    rankers_without_day, didnt_rank = sheets.unplaced(
        assignment_rows, submission_rows, roster_rows, days, inputs.tests_counted
    )
    has_schedule = bool(placed)
    stale = has_schedule and sheet["schedule_signature"] != inputs.signature
    published = has_schedule and bool(sheet["published_at"])
    day_counts = {d["key"]: len(by_day[d["key"]]) for d in days}
    needs_day = sheets.needs_day(rankers_without_day, days, assignment_rows, sheet["capacity_per_day"],
                                 sheet["scheduled_at"])
    without_day = len(needs_day) + len(didnt_rank) if has_schedule else 0
    open_seats = sum(max(0, sheet["capacity_per_day"] - n) for n in day_counts.values())
    # "Give them the open seats" is the way to place people on a schedule
    # students can see; on a draft that's out of date, making it again is.
    fill_plan = _fill_plan(sheet, inputs, assignment_rows, roster_rows) if without_day else []
    can_fill = bool(fill_plan) and (published or not stale)

    share_url = url_for("student.signin", sid=sid, _external=True)
    report = session.get("roster_report")
    if not (report and report.get("sid") == sid):
        report = None
    undo = session.pop("undo", None)
    if undo and undo.get("sid") != sid:
        undo = None
    handoff = _pop_form("handoff", sid)
    if handoff:
        handoff["account"] = current_instructor()["email"]
        handoff["service"] = compose.service_for(handoff["account"])
    stuck = _stuck(sid, roster_rows, locked) if sheet["bidding_open"] or published else None
    if undo and undo.get("tell"):
        undo["emails"] = _changed_emails(sheet, undo["snap"], roster_rows)
    add_form = _pop_form("add_form", sid)

    steps = [
        {"title": "Create your sign-up sheet", "done": True, "anchor": "top"},
        {"title": "Add your class list", "done": counts["roster"] > 0, "anchor": "class-list",
         "optional": bool(sheet["allow_unlisted"]),
         "hint": "One link and one button in Canvas, then drag the file in."},
        {"title": "See it as a student", "done": bool(sheet["tested_at"] or sheet["shared_at"]), "anchor": "try-it",
         "optional": True, "hint": "Opens in a new tab; this page stays open."},
        {"title": "Share the link with your class", "done": bool(sheet["shared_at"]),
         "anchor": "share", "hint": "Copy a ready-made message for Canvas."},
        {"title": "Close sign-ups and make the schedule", "done": has_schedule and not sheet["bidding_open"],
         "anchor": "schedule", "hint": "When everyone's in."},
        {"title": "Publish the schedule", "done": published and not sheet["bidding_open"], "anchor": "schedule",
         "hint": "Each student sees their day."},
    ]
    # Once the list is in, point straight at what comes next.
    list_next = next((step for step in steps[2:] if not step["done"]), None) if counts["roster"] else None

    download = request.args.get("download", "")
    download_url = (
        url_for("teach.snapshot_zip", snap_id=download)
        if download and sheets.get_snapshot(download, current_instructor()["id"]) else None
    )
    manual_moves = [f"{r['name']} → {r['label']}" for r in placed if r["method"] == MANUAL]
    move_people = [{"key": r["key"], "name": r["name"], "now": r["label"]} for r in placed]
    move_people += [{"key": r["name_key"], "name": r["display_name"], "now": None} for r in rankers_without_day]
    move_people += [{"key": r["name_key"], "name": r["display_name"], "now": None} for r in didnt_rank]

    return render_template(
        "sheet_admin.html",
        sheet=sheet,
        days=days,
        counts=counts,
        roster=roster_rows,
        real_roster=[r for r in roster_rows if not r["is_test"]],
        test_roster=[r for r in roster_rows if r["is_test"]],
        submissions=submissions,
        real_submissions=real_submissions,
        not_yet=not_yet,
        unlisted=unlisted,
        link_choices=link_choices,
        pins=pins,
        locked=locked,
        by_day=by_day,
        day_counts=day_counts,
        placed=placed,
        has_schedule=has_schedule,
        stale=stale,
        published=published,
        cant_do=[r for r in placed if r["cant"]],
        over_days=[d for d in days if day_counts[d["key"]] > sheet["capacity_per_day"]],
        unlisted_on_schedule=[r for r in placed if r["unlisted"]],
        rankers_without_day=rankers_without_day,
        needs_day=needs_day,
        didnt_rank=didnt_rank,
        without_day=without_day,
        open_seats=open_seats,
        fill_count=len(fill_plan) if can_fill else 0,
        choosable=CHOOSABLE,
        tests_left_out=inputs.tests_left_out,
        tests_counted=inputs.tests_counted,
        move_people=move_people,
        seat_warning=_seat_warning(sheet, days, counts),
        share_url=share_url,
        announcement=_announcement(sheet, share_url, counts),
        publish_announcement=_publish_announcement(sheet, share_url, counts, without_day),
        pin_waiting=pin_waiting,
        typo_domains=_domain_typos(roster_rows),
        manual_moves=manual_moves,
        email_room=signin.daily_room("student") if signin.codes_live() else None,
        email_trouble=signin.sending_trouble() if sheet["bidding_open"] or published else "",
        edit_form=_pop_form("edit_form", sid),
        steps=steps,
        next_step=_next_step(sheet, counts, has_schedule, stale, published, without_day, stuck,
                             len(fill_plan) if can_fill else 0, open_seats),
        algorithms=ALGORITHMS,
        current_algorithm=sheet["algorithm"] if sheet["algorithm"] in ALGORITHMS else DEFAULT_ALGORITHM,
        snapshots=sheets.recent_snapshots(sid),
        report=report,
        undo=undo,
        handoff=handoff,
        stuck=stuck,
        list_next=list_next,
        course_look={"url": url_for("teach.set_look", sid=sid), "title": sheet["title"],
                     "theme": sheet["theme"] or settings.DEFAULT_THEME, "font": sheet["font"] or "mixed"},
        add_form=add_form,
        download_url=download_url,
        email_on=signin.smtp_ready(),
        test_email=current_instructor()["email"],
    )


def _pop_form(name, sid):
    """What was typed into a form that was refused, to show it again once."""
    found = session.pop(name, None)
    return found if found and found.get("sid") == sid else None


def _back(sheet, anchor=""):
    return redirect(url_for("teach.sheet", sid=sheet["id"]) + (f"#{anchor}" if anchor else ""))


def _offer_undo(sheet, snap_id, message, parts, key="", tell=False):
    """Show a one-click Undo on the sheet page, once. With `tell`, it also
    offers the emails of everyone whose day changed since the restore point,
    to copy into a message to them."""
    session["undo"] = {"sid": sheet["id"], "snap": snap_id, "message": message, "parts": ",".join(parts), "key": key,
                       "tell": tell}


@bp.route("/s/<sid>/undo/<snap_id>", methods=["POST"])
@instructor_required
def undo(sid, snap_id):
    """Put part of the sheet back from the restore point taken just before a
    change: one student's ranking, the rankings, the class list, or the
    schedule. Rankings made since are kept."""
    sheet = owned_sheet(sid)
    snap = _own_snapshot(snap_id)
    if snap["sheet_id"] != sheet["id"]:
        abort(404)
    data = json.loads(snap["data"])
    parts = {p for p in (request.form.get("parts") or "").split(",") if p}
    key = request.form.get("key", "")
    if key:
        # Just one student: bring back their ranking (and seat) if it's gone.
        sub = next((s for s in data["submissions"] if s["name_key"] == key), None)
        listed = next((r for r in data["roster"] if r["name_key"] == key), None)
        if "roster" in parts and listed and not db.scalar(
            "SELECT 1 FROM roster WHERE sheet_id = :sid AND name_key = :key", sid=sheet["id"], key=key
        ):
            db.run(
                "INSERT INTO roster (sheet_id, name_key, display_name, email, is_test) "
                "VALUES (:sid, :key, :name, :email, :test)",
                sid=sheet["id"], key=key, name=listed["display_name"], email=listed.get("email") or "",
                test=1 if listed.get("is_test") else 0,
            )
        if sub and not db.scalar(
            "SELECT 1 FROM submissions WHERE sheet_id = :sid AND name_key = :key", sid=sheet["id"], key=key
        ):
            db.run(
                "INSERT INTO submissions (sheet_id, name_key, display_name, ranking, excluded_days, comments, "
                "updated_at) VALUES (:sid, :key, :name, :ranking, :excluded, :comments, :at)",
                sid=sheet["id"], key=key, name=sub["display_name"], ranking=sub["ranking"],
                excluded=sub["excluded_days"], comments=sub["comments"], at=sub["updated_at"],
            )
            seat = next((a for a in data["assignments"] if a["name_key"] == key), None)
            known = {d["key"] for d in sheets.get_days(sheet["id"])}
            if seat and seat["day_key"] in known and not db.scalar(
                "SELECT 1 FROM assignments WHERE sheet_id = :sid AND name_key = :key", sid=sheet["id"], key=key
            ):
                db.run(
                    "INSERT INTO assignments (sheet_id, name_key, display_name, day_key, method, assigned_at) "
                    "VALUES (:sid, :key, :name, :day, :method, :at)",
                    sid=sheet["id"], key=key, name=seat["display_name"], day=seat["day_key"],
                    method=seat["method"], at=seat["assigned_at"],
                )
        name = (sub or listed or {}).get("display_name", "that student")
        # They were signed out by the change being undone; let that go too.
        db.run(
            "DELETE FROM student_signouts WHERE sheet_id = :sid AND name_key = :key "
            "AND reason IN ('ranking', 'removed', 'list') AND at >= :since",
            sid=sheet["id"], key=key, since=snap["created_at"],
        )
        sheets.touch(sheet["id"])
        db.commit()
        flash(f"Undone — {name} is back.", "success")
        return _back(sheet, "signups")
    allowed = parts & {"roster", "submissions", "assignments"}
    if not allowed:
        abort(400)
    what = {"roster": "putting the previous class list back", "assignments": "putting the schedule back"}
    sheets.take_snapshot(sheet, "undo", "Before " + (what.get(next(iter(allowed))) if len(allowed) == 1 else
                                                     "undoing a delete"))
    db.run(
        "DELETE FROM student_signouts WHERE sheet_id = :sid AND reason IN ('ranking', 'removed', 'list') "
        "AND at >= :since",
        sid=sheet["id"], since=snap["created_at"],
    )
    changed = sheets.restore(sheet["id"], sheet["owner_id"], data, keep_newer=True, parts=allowed)
    auth.end_student_sessions(sheet["id"], changed, reason="list")
    db.commit()
    if "roster" in allowed:
        listed = sum(1 for r in data["roster"] if not r.get("is_test"))
        session["roster_report"] = {
            "sid": sheet["id"], "message": f"Your previous class list is back ({plural(listed, 'student')}).",
            "tips": [], "warn": False, "undo": None,
        }
    flash("Undone.", "success")
    return _back(sheet, "class-list" if "roster" in allowed else "schedule" if allowed == {"assignments"} else "signups")


# ---------------------------------------------------------------------------
# Class list
# ---------------------------------------------------------------------------
def _store_pending(sheet, kind, payload):
    pid = new_id(12)
    db.run("DELETE FROM pending_uploads WHERE sheet_id = :sid AND kind = :kind", sid=sheet["id"], kind=kind)
    db.run(
        "INSERT INTO pending_uploads (id, owner_id, sheet_id, kind, created_at, data) "
        "VALUES (:id, :owner, :sid, :kind, :at, :data)",
        id=pid, owner=sheet["owner_id"], sid=sheet["id"], kind=kind, at=iso(), data=json.dumps(payload),
    )
    db.commit()
    return pid


def _get_pending(sheet, pid, kind):
    found = db.row(
        "SELECT * FROM pending_uploads WHERE id = :id AND sheet_id = :sid AND owner_id = :owner AND kind = :kind",
        id=pid, sid=sheet["id"], owner=sheet["owner_id"], kind=kind,
    )
    return json.loads(found["data"]) if found else None


def _section_mismatch(sheet, sections):
    """A Section/Course column that never mentions anything from the sheet's
    title (every row says "BIO 101-2" on a "LAW 310" sheet): likely the
    wrong course's list."""
    if not sections:
        return ""
    value, _count = Counter(sections).most_common(1)[0]
    words = lambda text: {w for w in re.findall(r"[a-z]+|\d+", fold(text)) if len(w) >= 2}  # noqa: E731
    if words(value) & words(sheet["title"]):
        return ""
    return (f"Every row says section “{value}”, but this sheet is “{sheet['title']}”. Is this the right "
            "course's list?")


def _roster_tips(sheet, students, tips, sections=None, changed=()):
    tips = list(tips)
    mismatch = _section_mismatch(sheet, sections or {})
    if mismatch:
        tips.insert(0, mismatch)
    keys = {s["name_key"] for s in students} | sheets.test_keys(sheet["id"])
    stray = [r["display_name"] for r in sheets.get_submissions(sheet["id"]) if r["name_key"] not in keys]
    if stray:
        shown = ", ".join(stray[:5]) + (" …" if len(stray) > 5 else "")
        tips.append(
            f"{plural(len(stray), 'person who has', 'people who have')} already ranked "
            f"{'is' if len(stray) == 1 else 'are'} not on this list: {shown}. A spelling difference? Under "
            "Sign-ups, use “… is really” and Link to connect each to the right student."
        )
    if changed and sheet["shared_at"]:
        tips.append(
            "Some students now sign in differently (for example, with an emailed code instead of a PIN). If you "
            "already posted the sign-up message, post the updated one under “Share with your class”."
        )
    return tips, bool(mismatch or stray)


def _apply_roster(sheet, students, mode, message, tips, warn, source_label, sections=None):
    """Replace or add to the class list, record what happened, offer Undo."""
    current = {r["name_key"]: r for r in sheets.get_roster(sheet["id"]) if not r["is_test"]}
    had_list = bool(current)
    snap_id = sheets.take_snapshot(sheet, "roster", f"Before the class list changed ({source_label})") if had_list else None
    if mode == "add":
        added, filled, changed = sheets.add_to_roster(sheet["id"], students)
        message = f"Added {plural(added, 'new student')} to your list" + (
            f" and filled in {plural(filled, 'missing email')}." if filled else "."
        )
    else:
        kept = sum(1 for s in students if not s["email"] and current.get(s["name_key"], {}).get("email"))
        changed = sheets.replace_roster(sheet["id"], students)
        if had_list:
            message = message.replace("Added", "Replaced your list with", 1)
        if kept:
            tips = [f"Kept the email we already had for {plural(kept, 'student')} the new list had no email for."] + list(tips)
    auth.end_student_sessions(sheet["id"], changed, reason="list")
    db.run("DELETE FROM pending_uploads WHERE sheet_id = :sid AND kind = 'roster'", sid=sheet["id"])
    db.commit()
    if mode == "add":
        # Notes about the file's own emails don't apply to students already on the list.
        known = {r["display_name"] for r in current.values()}
        tips = [t for t in tips if not any(t.startswith(n + "'s email") for n in known)]
        students = [r for r in sheets.get_roster(sheet["id"]) if not r["is_test"]]
    tips, problems = _roster_tips(sheet, students, tips, sections, changed)
    session["roster_report"] = {
        "sid": sheet["id"], "message": message, "tips": tips[:8], "warn": warn or problems, "undo": snap_id,
    }


@bp.route("/s/<sid>/roster", methods=["POST"])
@instructor_required
def upload_roster(sid):
    sheet = owned_sheet(sid)
    upload = request.files.get("roster")
    pasted = (request.form.get("pasted") or "").strip()
    if upload and upload.filename:
        data = upload.read(MAX_UPLOAD_BYTES + 1)
        source = os.path.basename(upload.filename)[:80]
        source_label = f"uploaded “{source}”"
    elif pasted:
        data, source, source_label = pasted.encode("utf-8"), "", "pasted list"
    else:
        flash("Choose a file to upload, or paste a list of names first.", "error")
        return _back(sheet, "class-list")
    if len(data) > MAX_UPLOAD_BYTES:
        flash("That file is over 1 MB — a class list is far smaller. Is it the right file?", "error")
        return _back(sheet, "class-list")

    result = parse_roster(data)
    if result.error:
        flash(result.error, "error")
        return _back(sheet, "class-list")
    message, tips = describe(result, source)
    if not source:
        message = message.replace(" from “”", "")
    warn = bool(result.duplicates or result.bad_emails or result.suspicious or result.garbled
                or result.email_only or result.skipped)
    sections = dict(result.sections)

    current = [r for r in sheets.get_roster(sheet["id"]) if not r["is_test"]]
    if not current:
        _apply_roster(sheet, result.students, "replace", message, tips, warn, source_label, sections)
        return _back(sheet, "class-list")
    pid = _store_pending(sheet, "roster", {
        "students": result.students, "message": message, "tips": tips, "warn": warn,
        "source": source, "source_label": source_label, "pasted": not source, "sections": sections,
    })
    return redirect(url_for("teach.review_roster", sid=sheet["id"], pid=pid))


@bp.route("/s/<sid>/roster/review/<pid>", methods=["GET", "POST"])
@instructor_required
def review_roster(sid, pid):
    """Before an uploaded list replaces the current one: what changes, and
    a choice to replace, add, or keep the current list."""
    sheet = owned_sheet(sid)
    pending = _get_pending(sheet, pid, "roster")
    if not pending:
        flash("That upload has expired — please drop the file again.", "error")
        return _back(sheet, "class-list")
    if request.method == "POST":
        action = request.form.get("action")
        if action == "cancel":
            db.run("DELETE FROM pending_uploads WHERE id = :id", id=pid)
            db.commit()
            flash("Kept your current class list. Nothing changed.", "success")
            return _back(sheet, "class-list")
        if action in ("replace", "add"):
            _apply_roster(sheet, pending["students"], action, pending["message"], pending["tips"],
                          pending["warn"], pending["source_label"], pending.get("sections"))
            return _back(sheet, "class-list")
    diff = sheets.compare_rosters(sheets.get_roster(sheet["id"]), pending["students"])
    ranked = {r["name_key"] for r in sheets.get_submissions(sheet["id"])}
    dropping_ranked = [r for r in diff["dropping"] if r["name_key"] in ranked]
    overlap = len(diff["same"]) / diff["current"] if diff["current"] else 1
    warnings = []
    if diff["current"] >= 4 and overlap < 0.5:
        warnings.append(
            ("None of the names in it are on your current list" if not diff["same"] else
             f"Only {len(diff['same'])} of your {diff['current']} current names are in it")
            + " — is it the right class (and the right section)?"
        )
    if dropping_ranked:
        warnings.append(
            f"{plural(len(dropping_ranked), 'student who has', 'students who have')} already ranked would "
            f"come off your list: {', '.join(r['display_name'] for r in dropping_ranked[:6])}"
            f"{' …' if len(dropping_ranked) > 6 else ''}. Their rankings stay, marked “not on your list”."
        )
    if diff["current"] >= 4 and diff["incoming"] < diff["current"] / 2:
        warnings.append(f"The new list is much shorter ({diff['incoming']} vs. {diff['current']}).")
    mismatch = _section_mismatch(sheet, pending.get("sections") or {})
    if mismatch:
        warnings.append(mismatch)
    if diff["keeps_email"]:
        warnings.append(
            f"The new list has no email for {plural(len(diff['keeps_email']), 'student')} who already "
            "had one. If you replace, we'll keep the emails we have for them."
        )
    return render_template(
        "roster_review.html", sheet=sheet, pid=pid, pending=pending, diff=diff, warnings=warnings,
        recommend="add" if pending.get("pasted") or warnings else "replace",
    )


@bp.route("/s/<sid>/roster/report/dismiss", methods=["POST"])
@instructor_required
def dismiss_report(sid):
    sheet = owned_sheet(sid)
    session.pop("roster_report", None)
    return _back(sheet, "class-list")


@bp.route("/s/<sid>/roster/clear", methods=["POST"])
@instructor_required
def clear_roster(sid):
    sheet = owned_sheet(sid)
    snap_id = sheets.take_snapshot(sheet, "roster", "Before the class list was removed")
    removed = sheets.clear_roster(sheet["id"])
    auth.end_student_sessions(sheet["id"], removed)
    db.commit()
    session.pop("roster_report", None)
    _offer_undo(sheet, snap_id, f"Removed the class list ({plural(len(removed), 'student')}). Rankings stay.", ["roster"])
    return _back(sheet, "class-list")


def _read_person(form):
    """(name, email, problem) from an add/edit form, pulling an email out of
    the name box and turning "Last, First" around."""
    raw_name = form.get("name") or ""
    raw_email = form.get("email") or ""
    if "@" in raw_name and not raw_email.strip():
        raw_email = find_email(raw_name) or raw_name
        raw_name = re.sub(re.escape(raw_email), " ", raw_name, flags=re.I)
    raw_name = raw_name.strip().strip(",;<>").strip()
    if _is_last_first(raw_name):
        raw_name = flip_last_first(raw_name)
    name = clean_name(raw_name)
    email = clean_email_input(raw_email)
    if not name:
        return name, email, "Type the student's name."
    if email:
        problem = email_problem(email)
        if problem:
            return name, email, problem
    return name, email, ""


@bp.route("/s/<sid>/roster/add", methods=["POST"])
@instructor_required
def add_student(sid):
    sheet = owned_sheet(sid)
    name, email, problem = _read_person(request.form)
    key = normalize_name(name)
    roster_rows = sheets.get_roster(sheet["id"])
    same_key = next((r for r in roster_rows if r["name_key"] == key), None)
    same_email = next((r for r in roster_rows if email and r["email"] == email), None)
    twin_note = ""
    second = (not problem and same_key and email and same_key["email"] and not same_email
              and not same_key["is_test"])
    if second and request.form.get("same_name") == "1":
        # A different student with the same name: they're told apart by email.
        from roster import numbered

        taken = {r["name_key"] for r in roster_rows}
        n = 2
        while normalize_name(numbered(name, n)) in taken:
            n += 1
        twin_note = (f" There's already a {name} on your list, so this one is “{numbered(name, n)}”. Each signs in "
                     "with a code sent to their own email.")
        name = numbered(name, n)
        key, same_key = normalize_name(name), None
    if not problem and same_key:
        problem = (
            f"{same_key['display_name']} is already on your list"
            + (f" ({mask_email(same_key['email'])})" if same_key["email"] else "")
            + ". If this is a different student, add a middle initial (like “Sam J. Lee”); to change their "
            "email, use Edit next to their name."
        )
    if not problem and same_email:
        problem = f"That email is already on your list for {same_email['display_name']}."
    if problem:
        flash(problem, "error")
        session["add_form"] = {"sid": sheet["id"], "name": name, "email": email, "twin": bool(second)}
        return _back(sheet, "add-student")
    db.run(
        "INSERT INTO roster (sheet_id, name_key, display_name, email, is_test) VALUES (:sid, :key, :name, :email, 0)",
        sid=sheet["id"], key=key, name=name, email=email,
    )
    db.run("UPDATE sheets SET roster_updated_at = :at, updated_at = :at WHERE id = :sid", at=iso(), sid=sheet["id"])
    # Put back with the email they had: their sign-ins carry on. Anyone who
    # claimed the name with a PIN in the meantime has to use the email now.
    db.run("DELETE FROM student_signouts WHERE sheet_id = :sid AND name_key = :key AND reason = 'removed' "
           "AND email = :email AND email <> ''", sid=sheet["id"], key=key, email=email.lower())
    if email and db.run("DELETE FROM name_pins WHERE sheet_id = :sid AND name_key = :key", sid=sheet["id"], key=key):
        auth.end_student_sessions(sheet["id"], [key], reason="list")
    already = db.scalar("SELECT 1 FROM submissions WHERE sheet_id = :sid AND name_key = :key", sid=sheet["id"], key=key)
    db.commit()
    message = f"Added {name} to the class list." + twin_note
    if already:
        message += " They had already signed up, so their ranking now counts as on your list."
    if not email and any(r["email"] for r in roster_rows if not r["is_test"]):
        message += " Without an email, they'll sign in with a PIN instead of an emailed code."
    typo = _domain_typo_note(sheet, email)
    flash(message + typo, "info" if typo else "success")
    return _back(sheet, "class-list")


@bp.route("/s/<sid>/roster/edit", methods=["POST"])
@instructor_required
def edit_student(sid):
    sheet = owned_sheet(sid)
    key = request.form.get("name_key", "")
    row = db.row(
        "SELECT * FROM roster WHERE sheet_id = :sid AND name_key = :key AND is_test = 0", sid=sheet["id"], key=key
    )
    if not row:
        flash("That student isn't on the list any more.", "error")
        return _back(sheet, "class-list")
    name, email, problem = _read_person(request.form)
    new_key = normalize_name(name)
    if not problem and new_key != key and db.scalar(
        "SELECT 1 FROM roster WHERE sheet_id = :sid AND name_key = :key", sid=sheet["id"], key=new_key
    ):
        problem = f"{name} is already on your list."
    if not problem and new_key != key and db.scalar(
        "SELECT 1 FROM submissions WHERE sheet_id = :sid AND name_key = :key", sid=sheet["id"], key=new_key
    ):
        problem = (f"Someone not on your list already signed up as “{name}”. To connect them, use “… is really” "
                   "and Link next to their ranking under Sign-ups.")
    if not problem and email and db.scalar(
        "SELECT 1 FROM roster WHERE sheet_id = :sid AND email = :email AND name_key <> :key",
        sid=sheet["id"], email=email, key=key,
    ):
        problem = "That email is already on your list for another student."
    if problem:
        flash(problem, "error")
        session["edit_form"] = {"sid": sheet["id"], "key": key, "name": name, "email": email}
        return _back(sheet, "class-list")
    if name == row["display_name"] and email == row["email"]:
        flash("Nothing changed.", "info")
        return _back(sheet, "class-list")
    sheets.take_snapshot(sheet, "roster", f"Before editing {row['display_name']}")
    db.run(
        "UPDATE roster SET name_key = :new, display_name = :name, email = :email WHERE sheet_id = :sid AND name_key = :key",
        new=new_key, name=name, email=email, sid=sheet["id"], key=key,
    )
    if new_key != key:
        sheets.rekey_student(sheet["id"], key, new_key, name)
    else:
        db.run(
            "UPDATE submissions SET display_name = :name WHERE sheet_id = :sid AND name_key = :key",
            name=name, sid=sheet["id"], key=key,
        )
        db.run(
            "UPDATE assignments SET display_name = :name WHERE sheet_id = :sid AND name_key = :key",
            name=name, sid=sheet["id"], key=key,
        )
    if email and email != row["email"]:
        db.run("DELETE FROM name_pins WHERE sheet_id = :sid AND name_key = :key", sid=sheet["id"], key=new_key)
    if email != row["email"] or new_key != key:
        auth.end_student_sessions(sheet["id"], [key, new_key], reason="list")
    sheets.touch(sheet["id"])
    db.commit()
    typo = _domain_typo_note(sheet, email) if email != row["email"] else ""
    flash(f"Updated {name}." + typo, "info" if typo else "success")
    return _back(sheet, "class-list")


@bp.route("/s/<sid>/roster/remove", methods=["POST"])
@instructor_required
def remove_student(sid):
    sheet = owned_sheet(sid)
    key = request.form.get("name_key", "")
    found = db.row(
        "SELECT display_name, email FROM roster WHERE sheet_id = :sid AND name_key = :key AND is_test = 0",
        sid=sheet["id"], key=key,
    )
    if not found:
        flash("That student was already off the list.", "info")
        return _back(sheet, "class-list")
    also_ranking = request.form.get("delete_ranking") == "1"
    snap_id = sheets.take_snapshot(sheet, "roster", f"Before taking {found['display_name']} off the class list")
    db.run("DELETE FROM roster WHERE sheet_id = :sid AND name_key = :key", sid=sheet["id"], key=key)
    db.run("DELETE FROM name_pins WHERE sheet_id = :sid AND name_key = :key", sid=sheet["id"], key=key)
    signin.clear_pin_failures(sheet["id"], key)
    if also_ranking:
        for table in ("submissions", "assignments"):
            db.run(f"DELETE FROM {table} WHERE sheet_id = :sid AND name_key = :key", sid=sheet["id"], key=key)
    auth.end_student_sessions(sheet["id"], [key], reason="removed", email=found["email"])
    sheets.touch(sheet["id"])
    db.commit()
    parts = ["roster", "submissions"] if also_ranking else ["roster"]
    _offer_undo(
        sheet, snap_id,
        f"Took {found['display_name']} off the class list" + (" and deleted their ranking." if also_ranking else "."),
        parts, key=key,
    )
    return _back(sheet, "class-list")


@bp.route("/s/<sid>/who", methods=["POST"])
@instructor_required
def set_who(sid):
    """Who can sign up: only names on the class list, or anyone with the link."""
    sheet = owned_sheet(sid)
    allow = request.form.get("allow_unlisted") == "1"
    db.run("UPDATE sheets SET allow_unlisted = :a, updated_at = :at WHERE id = :sid",
           a=int(allow), at=iso(), sid=sheet["id"])
    db.commit()
    if allow:
        flash("Anyone with the link can sign up now. People not on your list choose a PIN.", "success")
    else:
        flash("Only names on your class list can sign in now.", "success")
        unlisted = sheets.counts(sheet["id"])["unlisted_submissions"]
        if unlisted:
            flash(
                f"{plural(unlisted, 'person', 'people')} not on your list already ranked. Their rankings are still "
                "under Sign-ups — connect each to the right name with “… is really” and Link, or delete it.",
                "info",
            )
    return _back(sheet, "class-list")


# ---------------------------------------------------------------------------
# Trying it out, and sharing
# ---------------------------------------------------------------------------
@bp.route("/s/<sid>/try", methods=["POST"])
@instructor_required
def try_as_student(sid):
    """One click: see the student pages as a pretend student, in this same
    browser (no second window, no email)."""
    sheet = owned_sheet(sid)
    name = sheets.TEST_STUDENT_NAMES[0]
    key = normalize_name(name)
    existing = db.row("SELECT email FROM roster WHERE sheet_id = :sid AND name_key = :key", sid=sheet["id"], key=key)
    if not existing:
        sheets.add_test_students(sheet["id"], current_instructor()["email"], names=(name,))
    else:
        db.run("UPDATE sheets SET tested_at = :at WHERE id = :sid", at=iso(), sid=sheet["id"])
    db.commit()
    auth.sign_in_student(sheet["id"], name, key, "preview")
    return redirect(url_for("student.home", sid=sheet["id"]))


@bp.route("/s/<sid>/test-students", methods=["POST"])
@instructor_required
def add_test_students(sid):
    sheet = owned_sheet(sid)
    email = clean_email_input(request.form.get("email"))
    problem = email_problem(email)
    if problem:
        flash(f"Test students' codes: {problem}", "error")
        return _back(sheet, "try-it")
    sheets.add_test_students(sheet["id"], email)
    db.commit()
    flash(f"Added Test Student 1 and Test Student 2. Their sign-in codes go to {email}.", "success")
    return _back(sheet, "try-it")


@bp.route("/s/<sid>/test-students/remove", methods=["POST"])
@instructor_required
def remove_test_students(sid):
    sheet = owned_sheet(sid)
    removed = sheets.remove_test_students(sheet["id"])
    auth.end_student_sessions(sheet["id"], removed, reason="test")
    db.commit()
    if removed:
        flash("Removed the test students and everything they submitted.", "success")
    return _back(sheet, "try-it")


@bp.route("/s/<sid>/shared", methods=["POST"])
@instructor_required
def mark_shared(sid):
    """Called by the Copy buttons, to tick "share" off the checklist."""
    sheet = owned_sheet(sid)
    if not sheet["shared_at"]:
        db.run("UPDATE sheets SET shared_at = :at WHERE id = :sid", at=iso(), sid=sheet["id"])
        db.commit()
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# Students' sign-ins
# ---------------------------------------------------------------------------
def _person_name(sheet, key):
    for table in ("roster", "submissions"):
        found = db.row(f"SELECT display_name FROM {table} WHERE sheet_id = :sid AND name_key = :key",
                       sid=sheet["id"], key=key)
        if found:
            return found["display_name"]
    return None


@bp.route("/s/<sid>/reset-pin", methods=["POST"])
@instructor_required
def reset_pin(sid):
    """For a student who forgot the PIN they chose when signing in by name."""
    sheet = owned_sheet(sid)
    key = request.form.get("name_key", "")
    name = _person_name(sheet, key) or key.title()
    also_ranking = request.form.get("delete_ranking") == "1"
    snap_id = None
    if also_ranking:
        snap_id = sheets.take_snapshot(sheet, "delete-one", f"Before resetting {name}'s PIN and deleting the ranking")
    if not db.run("DELETE FROM name_pins WHERE sheet_id = :sid AND name_key = :key", sid=sheet["id"], key=key):
        flash(f"{name} doesn't have a PIN to reset.", "info")
        return _back(sheet, "signups")
    if also_ranking:
        for table in ("submissions", "assignments"):
            db.run(f"DELETE FROM {table} WHERE sheet_id = :sid AND name_key = :key", sid=sheet["id"], key=key)
    signin.clear_pin_failures(sheet["id"], key)
    auth.end_student_sessions(sheet["id"], [key], reason="" if also_ranking else "pin")
    sheets.touch(sheet["id"])
    db.commit()
    message = (
        f"Reset {name}'s PIN{' and deleted their ranking' if also_ranking else ''}, and signed out anyone "
        f"signed in as them. Tell {name} to sign in now — they'll choose a new PIN. (Until they do, anyone who "
        "types that name could choose it, so don't wait long.)"
    )
    if snap_id:
        _offer_undo(sheet, snap_id, message, ["submissions"], key=key)
    else:
        flash(message, "success")
    return _back(sheet, "signups")


@bp.route("/s/<sid>/unlock-pin", methods=["POST"])
@instructor_required
def unlock_pin(sid):
    sheet = owned_sheet(sid)
    key = request.form.get("name_key", "")
    signin.clear_pin_failures(sheet["id"], key)
    db.commit()
    flash(f"Unlocked {_person_name(sheet, key) or 'that name'} — they can sign in again now. Their PIN, if they "
          "have one, is unchanged.", "success")
    return _back(sheet, request.form.get("back") or "signups")


@bp.route("/s/<sid>/signin-code", methods=["POST"])
@instructor_required
def signin_code(sid):
    """The backup when the site's email can't reach a student (its emails
    ran out or failed, a typo in the address, a slow inbox): a sign-in link
    the instructor sends from their own email, and a code to read out."""
    sheet = owned_sheet(sid)
    key = request.form.get("name_key", "")
    row = db.row("SELECT * FROM roster WHERE sheet_id = :sid AND name_key = :key", sid=sheet["id"], key=key)
    if not row or not row["email"]:
        flash("That student signs in with a PIN, not a code — use Reset PIN if they're stuck.", "error")
        return _back(sheet, "class-list")
    code, until, token = signin.issue_code_for_instructor_handoff(sheet["id"], row)
    subject, body = _link_email(sheet, row["display_name"], token)
    session["handoff"] = {
        "sid": sheet["id"], "name": row["display_name"], "email": row["email"], "code": code, "until": until,
        "subject": subject, "body": body,
    }
    return _back(sheet, request.form.get("back") or "class-list")


def _link_email(sheet, name, token):
    """The email an instructor sends a student from their own account:
    (subject, body), with a link that signs in that one student."""
    link = url_for("student.link_sign_in", sid=sheet["id"], token=token, _external=True)
    return (
        f"Your sign-in link for {sheet['title']}",
        f"Hi {name},\n\n"
        f"Here's your link to sign in to “{sheet['title']}”. It works once, any time in the next "
        f"{signin.LINK_EXPIRY_HOURS} hours, on whatever phone or computer you open it on:\n\n"
        f"{link}\n\n"
        "It signs in as you, so please don't forward it. If it doesn't work, just reply to this email.\n",
    )


@bp.route("/s/<sid>/email-links", methods=["GET", "POST"])
@instructor_required
def email_links(sid):
    """The backup for the site's own email: everyone on the class list with
    an email address, each with a message already addressed and written,
    with a link that signs in that one student, opening in the
    instructor's own email. (One email per student: a link in a group
    email would let anyone in it sign in as anyone.) Pressing the button
    (POST) makes the links; the page only lists the class until then."""
    sheet = owned_sheet(sid)
    me = current_instructor()["email"]
    mail_default = compose.service_for(me)  # before any writes: it may save on its own connection
    roster_rows = [r for r in sheets.get_roster(sheet["id"]) if not r["is_test"]]
    stuck = signin.refusals_today(sheet["id"]) if signin.codes_live() else {}
    ranked = {s["name_key"] for s in sheets.get_submissions(sheet["id"])}
    ready = request.method == "POST"
    people = []
    for r in roster_rows:
        if not r["email"]:
            continue
        person = {"name": r["display_name"], "email": r["email"], "stuck": r["name_key"] in stuck,
                  "ranked": r["name_key"] in ranked}
        if ready:
            token = signin.new_link(sheet["id"], r["name_key"], r["email"], from_instructor=True)
            person["subject"], person["body"] = _link_email(sheet, r["display_name"], token)
        people.append(person)
    if ready:
        db.commit()
    # Who needs it most first; the class list's own order within each group.
    people.sort(key=lambda p: (not p["stuck"], p["ranked"]))
    share_url = url_for("student.signin", sid=sheet["id"], _external=True)
    return render_template(
        "email_links.html",
        sheet=sheet,
        people=people,
        ready=ready,
        no_email=[r["display_name"] for r in roster_rows if not r["email"]],
        me=me,
        mail_default=mail_default,
        link_hours=signin.LINK_EXPIRY_HOURS,
        heads_up={
            "subject": f"Your sign-in link for {sheet['title']} is coming from me",
            "body": (
                "Hi everyone,\n\n"
                "The sign-up site's own emails may not reach you right now, so I'm sending each of you your own "
                f"sign-in link from my email. Look for “Your sign-in link for {sheet['title']}”; it works once, "
                f"any time in the next {signin.LINK_EXPIRY_HOURS} hours.\n\n"
                f"The sign-up page is {share_url}\n"
            ),
        },
    )


@bp.route("/s/<sid>/link", methods=["POST"])
@instructor_required
def link_student(sid):
    """A ranking made under a name that isn't on the list ("Jon Smith"),
    connected to the right student on it ("Jonathan Smith")."""
    sheet = owned_sheet(sid)
    from_key = request.form.get("name_key", "")
    to_key = request.form.get("to_key", "")
    sub = db.row("SELECT display_name FROM submissions WHERE sheet_id = :sid AND name_key = :key",
                 sid=sheet["id"], key=from_key)
    target = db.row("SELECT display_name FROM roster WHERE sheet_id = :sid AND name_key = :key AND is_test = 0",
                    sid=sheet["id"], key=to_key)
    if not sub or not target or from_key == to_key:
        flash("Pick the student on your list this ranking belongs to.", "error")
        return _back(sheet, "signups")
    if db.scalar("SELECT 1 FROM submissions WHERE sheet_id = :sid AND name_key = :key", sid=sheet["id"], key=to_key):
        flash(f"{target['display_name']} already has a ranking. Delete one of the two first.", "error")
        return _back(sheet, "signups")
    snap_id = sheets.take_snapshot(sheet, "link", f"Before linking “{sub['display_name']}” to {target['display_name']}")
    sheets.rekey_student(sheet["id"], from_key, to_key, target["display_name"])
    if db.scalar("SELECT email FROM roster WHERE sheet_id = :sid AND name_key = :key", sid=sheet["id"], key=to_key):
        db.run("DELETE FROM name_pins WHERE sheet_id = :sid AND name_key = :key", sid=sheet["id"], key=to_key)
    auth.end_student_sessions(sheet["id"], [from_key], reason="linked")
    sheets.touch(sheet["id"])
    db.commit()
    _offer_undo(
        sheet, snap_id,
        f"Linked “{sub['display_name']}” to {target['display_name']}. The ranking now counts under that name; "
        f"they'll sign in as {target['display_name']} from now on.",
        ["submissions", "assignments"],
    )
    return _back(sheet, "signups")


# ---------------------------------------------------------------------------
# Sign-ups and the schedule
# ---------------------------------------------------------------------------
def email_backup(sheet, reason):
    """Email the instructor a backup of their sheet. Returns (sent, message)."""
    if not signin.smtp_ready():
        return False, "Email isn't set up here, so use “Download a backup” instead."
    if signin.emails_sent_today() >= settings.EMAIL_DAILY_LIMIT:
        return False, "The site has used up today's emails, so no backup was emailed — use “Download a backup”."
    zone = _zone()
    data = sheets.export_sheet(sheet)
    filename, blob = sheets.build_zip(data, reason, zone)
    body = (
        f"Attached is a backup of your sign-up sheet “{sheet['title']}” ({reason}).\n"
        f"Your sheet: {url_for('teach.sheet', sid=sheet['id'], _external=True)}\n\n"
        f"It holds: {sheets.describe_export(data)}. Inside are spreadsheets that open in Excel or Google "
        "Sheets — everyone's rankings and notes, the schedule (if you've made it), and your class list.\n\n"
        "If anything ever goes wrong with the site, this file is your copy. To put it back, open the sheet "
        "and use “Restore from a backup file” under Backups.\n"
    )
    try:
        signin.send_email(
            sheet["owner_email"], f"Backup of “{sheet['title']}” ({reason}, {sheets.file_time(iso(), zone)})", body,
            attachments=[(filename, blob, "application/zip")],
        )
    except Exception as exc:
        db.record_error("EMAIL", f"Sending a backup failed: {exc!r}")
        return False, "Couldn't email the backup just now — use “Download a backup” instead."
    signin.log_email(sheet["owner_email"], "backup")
    db.run("UPDATE sheets SET backup_emailed_at = :at WHERE id = :sid", at=iso(), sid=sheet["id"])
    db.commit()
    return True, f"Emailed a backup to {sheet['owner_email']}."


def _backup_is_current(sheet):
    """True if a backup was emailed in the last few minutes and no ranking
    changed since — so another would just be a duplicate."""
    if not sheet["backup_emailed_at"]:
        return False
    if (now() - parse_iso(sheet["backup_emailed_at"])).total_seconds() >= BACKUP_EMAIL_GAP_MINUTES * 60:
        return False
    newest = db.scalar("SELECT MAX(updated_at) FROM submissions WHERE sheet_id = :sid", sid=sheet["id"])
    return not newest or newest <= sheet["backup_emailed_at"]


def _close(sheet):
    db.run("UPDATE sheets SET bidding_open = 0, updated_at = :at WHERE id = :sid", at=iso(), sid=sheet["id"])
    sheets.take_snapshot(sheet, "closed", "When sign-ups were closed")
    db.commit()


def _after_close_backup(sheet):
    if not signin.smtp_ready():
        return
    if _backup_is_current(sheet):
        flash("We didn't email another backup — one went out a few minutes ago with the same rankings.", "info")
        return
    sent, message = email_backup(sheets.get_sheet(sheet["id"]), "sign-ups closed")
    flash(message + (" Keep it somewhere safe." if sent else ""), "info")


@bp.route("/s/<sid>/toggle", methods=["POST"])
@instructor_required
def toggle_bidding(sid):
    sheet = owned_sheet(sid)
    if sheet["bidding_open"]:
        _close(sheet)
        flash("Sign-ups are closed — rankings are locked in. Next: make the schedule.", "success")
        _after_close_backup(sheet)
        return _back(sheet, "schedule")
    was_published = bool(sheet["published_at"])
    db.run("UPDATE sheets SET bidding_open = 1, published_at = NULL, updated_at = :at WHERE id = :sid",
           at=iso(), sid=sheet["id"])
    db.commit()
    flash(
        "Sign-ups are open again."
        + (" The schedule is hidden from students until you publish it again." if was_published else ""),
        "success",
    )
    return _back(sheet, "signups")


def _schedule_changes(sheet, inputs, results):
    """How a new schedule would differ from the saved one, by student."""
    labels = {d["key"]: d["label"] for d in inputs.days}
    before = {a["name_key"]: a for a in sheets.get_assignments(sheet["id"])}
    after = {key: (name, day) for key, name, day, _method in results}
    lose, gain, move = [], [], []
    for key, a in before.items():
        if key not in after:
            lose.append(f"{a['display_name']} ({labels.get(a['day_key'], '?')})")
        elif after[key][1] != a["day_key"]:
            move.append(f"{a['display_name']}: {labels.get(a['day_key'], '?')} → {labels.get(after[key][1], '?')}")
    for key, (name, day) in after.items():
        if key not in before:
            gain.append(f"{name} ({labels.get(day, '?')})")
    return {"lose": sorted(lose, key=str.lower), "gain": sorted(gain, key=str.lower), "move": sorted(move, key=str.lower)}


def _names(items, limit=8, sep=", "):
    return sep.join(items[:limit]) + (" …" if len(items) > limit else "")


def _make_schedule(sheet, confirmed=False):
    if not sheet["lottery_seed"]:
        db.run("UPDATE sheets SET lottery_seed = :seed WHERE id = :sid", seed=sheets.new_lottery_seed(), sid=sheet["id"])
        sheet = sheets.get_sheet(sheet["id"])
    inputs = sheets.schedule_inputs(sheet)
    if not inputs.students:
        flash("No one has ranked the days yet, so there's nothing to schedule.", "error")
        return _back(sheet, "schedule")
    existing = sheets.get_assignments(sheet["id"])
    results = compute_assignment(
        inputs.students, inputs.day_keys, sheet["capacity_per_day"], algorithm=inputs.algorithm,
        seed=sheet["lottery_seed"], unranked=inputs.unranked,
    )
    changes = _schedule_changes(sheet, inputs, results) if existing else None
    if sheet["published_at"] and existing and not confirmed and (changes["lose"] or changes["move"]):
        # Students can already see their days: show exactly who would change
        # before anything is saved — and the way that changes nobody.
        fillable = _fill_plan(sheet, inputs, existing, sheets.get_roster(sheet["id"]))
        return render_template("rerun_confirm.html", sheet=sheet, changes=changes, fillable=len(fillable))
    manual = [a for a in existing if a["method"] == MANUAL]
    snap_id = sheets.take_snapshot(sheet, "rerun", "Before making the schedule again") if existing else None
    sheets.save_assignment(sheet["id"], results, inputs.algorithm, inputs.signature)
    db.commit()
    notes = []
    if manual and snap_id:
        labels = {d["key"]: d["label"] for d in inputs.days}
        moves = _names([f"{a['display_name']} → {labels.get(a['day_key'], '?')}" for a in manual], 4)
        notes.append(f"Your {plural(len(manual), 'hand-made move')} ({moves}) "
                     f"{'was' if len(manual) == 1 else 'were'} replaced by the new schedule.")
    if not sheet["published_at"]:
        flash("Your schedule is ready. Check it below, then publish it so students can see their day.", "success")
        if notes:
            _offer_undo(sheet, snap_id, " ".join(notes), ["assignments"])
    elif changes and (changes["lose"] or changes["move"] or changes["gain"]):
        parts = []
        if changes["move"]:
            parts.append("changed day: " + _names(changes["move"], sep="; "))
        if changes["lose"]:
            parts.append("now have no day: " + _names(changes["lose"]))
        if changes["gain"]:
            parts.append("now have a day: " + _names(changes["gain"]))
        notes.insert(0, "Made the schedule again — students see it right away. Tell these students: "
                     + " · ".join(parts) + ".")
        _offer_undo(sheet, snap_id, " ".join(notes), ["assignments"], tell=True)
    else:
        flash("Made the schedule again. Nobody's day changed.", "success")
        if notes:
            _offer_undo(sheet, snap_id, " ".join(notes), ["assignments"])
    if inputs.tests_left_out:
        flash("Test students were left out, since real students have ranked.", "info")
    elif inputs.tests_counted:
        flash("Test students got seats, because no real student has ranked yet. Once real students rank, "
              "test students are left out automatically.", "info")
    return _back(sheet, "schedule")


@bp.route("/s/<sid>/close-and-schedule", methods=["POST"])
@instructor_required
def close_and_schedule(sid):
    sheet = owned_sheet(sid)
    was_open = bool(sheet["bidding_open"])
    if was_open:
        _close(sheet)
        sheet = sheets.get_sheet(sheet["id"])
    response = _make_schedule(sheet)
    if was_open:
        _after_close_backup(sheet)
    return response


@bp.route("/s/<sid>/run", methods=["POST"])
@instructor_required
def run_assignment(sid):
    sheet = owned_sheet(sid)
    if sheet["bidding_open"]:
        flash("Close sign-ups first, so the schedule can't shift under you. (“Peek at a draft” shows how it "
              "would look right now.)", "error")
        return _back(sheet, "schedule")
    return _make_schedule(sheet, confirmed=request.form.get("confirmed") == "1")


@bp.route("/s/<sid>/draft")
@instructor_required
def draft(sid):
    """How the schedule would come out right now — shown, never saved."""
    sheet = owned_sheet(sid)
    inputs = sheets.schedule_inputs(sheet)
    results = compute_assignment(
        inputs.students, inputs.day_keys, sheet["capacity_per_day"], algorithm=inputs.algorithm,
        seed=sheet["lottery_seed"], unranked=inputs.unranked,
    )
    roster_rows = sheets.get_roster(sheet["id"])
    submission_rows = sheets.get_submissions(sheet["id"])
    fake_rows = [{"name_key": k, "display_name": n, "day_key": d, "method": m} for k, n, d, m in results]
    placed = sheets.schedule_rows(inputs.days, fake_rows, submission_rows, roster_rows, sheet["capacity_per_day"])
    rankers, didnt_rank = sheets.unplaced(fake_rows, inputs.rows, roster_rows, inputs.days, inputs.tests_counted)
    return render_template(
        "draft.html", sheet=sheet, days=inputs.days, by_day=sheets.group_by_day(inputs.days, placed),
        counts=sheets.counts(sheet["id"]),
        needs_day=sheets.needs_day(rankers, inputs.days, fake_rows, sheet["capacity_per_day"]),
        didnt_rank=didnt_rank if sheet["include_unranked"] else [],
        cant_do=[r for r in placed if r["cant"]],
    )


def _after_setting_change(sheet):
    """What a changed setting means for a schedule that's already made."""
    sheet = sheets.get_sheet(sheet["id"])
    assignment_rows = sheets.get_assignments(sheet["id"])
    if not assignment_rows:
        return ""
    inputs = sheets.schedule_inputs(sheet)
    if not sheet["published_at"]:
        if sheet["schedule_signature"] == inputs.signature:
            return " The schedule already matches."
        return (" Press “Make the schedule again” under Schedule to use it — nobody has seen the schedule yet, "
                "so that's safe.")
    roster_rows = sheets.get_roster(sheet["id"])
    rankers, didnt_rank = sheets.unplaced(assignment_rows, inputs.rows, roster_rows, inputs.days,
                                          inputs.tests_counted)
    waiting = len(rankers) + len(didnt_rank)
    plan = _fill_plan(sheet, inputs, assignment_rows, roster_rows) if waiting else []
    labels = {d["key"]: d["label"] for d in inputs.days}
    load = Counter(a["day_key"] for a in assignment_rows if a["day_key"] in labels)
    over = [labels[k] for k in labels if load[k] > sheet["capacity_per_day"]]
    note = " The schedule students see hasn't changed."
    if plan:
        note += (f" To give {plural(len(plan), 'student')} without a day a seat now, press “Give them the open "
                 "seats” under Schedule — nobody who can see their day moves.")
    elif not waiting:
        note += " Everyone already has a day, so there's nothing else to do."
    if over:
        note += (f" {_names(over)} now {'has' if len(over) == 1 else 'have'} more people than seats; nobody was "
                 "moved, so move someone by hand if you need to.")
    return note


@bp.route("/s/<sid>/capacity", methods=["POST"])
@instructor_required
def set_capacity(sid):
    sheet = owned_sheet(sid)
    try:
        capacity = int(request.form.get("capacity", ""))
    except ValueError:
        flash("Seats per day should be a whole number, like 4.", "error")
        return _back(sheet, "schedule")
    if not 1 <= capacity <= 500:
        flash("Seats per day must be between 1 and 500.", "error")
        return _back(sheet, "schedule")
    db.run("UPDATE sheets SET capacity_per_day = :c, updated_at = :at WHERE id = :sid",
           c=capacity, at=iso(), sid=sheet["id"])
    db.commit()
    flash(f"Seats per day: {capacity}." + _after_setting_change(sheet), "success")
    return _back(sheet, "schedule")


@bp.route("/s/<sid>/unranked", methods=["POST"])
@instructor_required
def set_unranked(sid):
    sheet = owned_sheet(sid)
    include = request.form.get("include_unranked") == "1"
    db.run("UPDATE sheets SET include_unranked = :v, updated_at = :at WHERE id = :sid",
           v=int(include), at=iso(), sid=sheet["id"])
    db.commit()
    flash(("Students who don't rank will get the seats that are left."
           if include else "Students who don't rank will be left off the schedule.")
          + _after_setting_change(sheet), "success")
    return _back(sheet, "schedule")


@bp.route("/s/<sid>/open-seats", methods=["POST"])
@instructor_required
def give_open_seats(sid):
    """Give the open seats to students without a day: each one who ranked
    gets the highest day on their own list that still has a seat (never one
    they said they can't do), then those who didn't rank get any open seat.
    Nobody already on the schedule moves, and no day goes past its seats."""
    sheet = owned_sheet(sid)
    inputs = sheets.schedule_inputs(sheet)
    assignment_rows = sheets.get_assignments(sheet["id"])
    roster_rows = sheets.get_roster(sheet["id"])
    rankers, didnt_rank = sheets.unplaced(assignment_rows, sheets.get_submissions(sheet["id"]), roster_rows,
                                          inputs.days, inputs.tests_counted)
    waiting = len(rankers) + len(didnt_rank)
    if not waiting or not inputs.days:
        flash("Everyone already has a day.", "info")
        return _back(sheet, "schedule")
    known = set(inputs.day_keys)
    load = Counter(a["day_key"] for a in assignment_rows if a["day_key"] in known)
    open_seats = sum(max(0, sheet["capacity_per_day"] - load[d]) for d in known)
    plan = _fill_plan(sheet, inputs, assignment_rows, roster_rows)
    if not plan:
        flash("There are no open seats left. Raise “Seats per day” (or add a day) first, or move people by hand."
              if not open_seats else
              "The only open seats are on days these students said they can't do. Read their notes, then move "
              "them by hand — or raise seats per day.", "error")
        return _back(sheet, "schedule")
    snap_id = sheets.take_snapshot(sheet, "place", "Before giving out the open seats")
    at = iso()
    db.run_many(
        "INSERT INTO assignments (sheet_id, name_key, display_name, day_key, method, assigned_at) "
        "VALUES (:sid, :key, :name, :day, :method, :at)",
        [{"sid": sheet["id"], "key": k, "name": n, "day": d, "method": m, "at": at} for k, n, d, m in plan],
    )
    sheets.touch(sheet["id"])
    db.commit()
    labels = {d["key"]: d["label"] for d in inputs.days}
    message = (f"Gave {plural(len(plan), 'student')} a day: "
               + _names([f"{n} — {labels[d]}" for _k, n, d, _m in plan], sep="; ") + ". Nobody else moved.")
    left = waiting - len(plan)
    if left:
        full = open_seats - len(plan) <= 0
        message += (f" {plural(left, 'student')} still {'has' if left == 1 else 'have'} no day — "
                    + ("there are no more open seats." if full else "the open seats are on days they can't do."))
    if sheet["published_at"]:
        message += " They can see their new day right away."
    _offer_undo(sheet, snap_id, message, ["assignments"], tell=bool(sheet["published_at"]))
    return _back(sheet, "schedule")


@bp.route("/s/<sid>/algorithm", methods=["POST"])
@instructor_required
def set_algorithm(sid):
    sheet = owned_sheet(sid)
    algorithm = request.form.get("algorithm")
    if algorithm not in CHOOSABLE:
        flash("Pick one of the options." if algorithm not in ALGORITHMS else
              "That option is only for comparing — it isn't fair to students who rank honestly, so it can't be "
              "used for a real schedule.", "error")
    elif algorithm != sheet["algorithm"]:
        if sheets.get_assignments(sheet["id"]):
            sheets.take_snapshot(sheet, "method", "Before changing how ties are broken")
        db.run("UPDATE sheets SET algorithm = :a, updated_at = :at WHERE id = :sid",
               a=algorithm, at=iso(), sid=sheet["id"])
        db.commit()
        flash(f"Now using: {ALGORITHMS[algorithm]['label']}." + _after_setting_change(sheet), "success")
    return _back(sheet, "schedule")


@bp.route("/s/<sid>/compare", methods=["GET", "POST"])
@instructor_required
def compare(sid):
    sheet = owned_sheet(sid)
    inputs = sheets.schedule_inputs(sheet)
    if not inputs.students:
        flash("No rankings yet — nothing to compare.", "error")
        return _back(sheet, "schedule")
    stats, trials = compare_algorithms(inputs.students, inputs.day_keys, sheet["capacity_per_day"])
    return render_template(
        "compare.html", sheet=sheet, stats=stats, trials=trials, requested_trials=COMPARE_TRIALS,
        num_submissions=len(inputs.students), capacity=sheet["capacity_per_day"],
        current_algorithm=sheet["algorithm"], verdict=verdict(stats, sheet["algorithm"]),
    )


@bp.route("/s/<sid>/move", methods=["POST"])
@instructor_required
def move_student(sid):
    sheet = owned_sheet(sid)
    key = request.form.get("name_key", "")
    day_key = request.form.get("day_key", "")
    days = sheets.get_days(sheet["id"])
    labels = {d["key"]: d["label"] for d in days}
    name = _person_name(sheet, key)
    if day_key not in labels or not name:
        flash("Pick a student and a day.", "error")
        return _back(sheet, "schedule")
    current = db.row("SELECT day_key FROM assignments WHERE sheet_id = :sid AND name_key = :key",
                     sid=sheet["id"], key=key)
    if current and current["day_key"] == day_key:
        flash(f"{name} is already on {labels[day_key]}.", "info")
        return _back(sheet, "schedule")
    if current:
        db.run("UPDATE assignments SET day_key = :day, method = :method, assigned_at = :at "
               "WHERE sheet_id = :sid AND name_key = :key",
               day=day_key, method=MANUAL, at=iso(), sid=sheet["id"], key=key)
    else:
        db.run("INSERT INTO assignments (sheet_id, name_key, display_name, day_key, method, assigned_at) "
               "VALUES (:sid, :key, :name, :day, :method, :at)",
               sid=sheet["id"], key=key, name=name, day=day_key, method=MANUAL, at=iso())
    sheets.touch(sheet["id"])
    db.commit()
    warnings = []
    there = db.scalar("SELECT COUNT(*) FROM assignments WHERE sheet_id = :sid AND day_key = :day",
                      sid=sheet["id"], day=day_key) or 0
    if there > sheet["capacity_per_day"]:
        warnings.append(f"{labels[day_key]} now has {there} people for {plural(sheet['capacity_per_day'], 'seat')}.")
    sub = db.row("SELECT excluded_days, comments FROM submissions WHERE sheet_id = :sid AND name_key = :key",
                 sid=sheet["id"], key=key)
    if sub and day_key in json.loads(sub["excluded_days"]):
        note = str(json.loads(sub["comments"]).get(day_key, "")).strip()
        warnings.append(f"{name} said they can't do {labels[day_key]}" + (f": “{note}”." if note else "."))
    message = f"Moved {name} to {labels[day_key]}." + (" " + " ".join(warnings) if warnings else "")
    if sheet["published_at"]:
        message += f" {name} can see this change now."
    flash(message, "info" if warnings else "success")
    return _back(sheet, "schedule")


@bp.route("/s/<sid>/publish", methods=["POST"])
@instructor_required
def publish(sid):
    sheet = owned_sheet(sid)
    if sheet["bidding_open"]:
        flash("Close sign-ups first, then publish the schedule.", "error")
        return _back(sheet, "schedule")
    if not sheets.get_assignments(sheet["id"]):
        flash("Make the schedule first.", "error")
        return _back(sheet, "schedule")
    db.run("UPDATE sheets SET published_at = :at, updated_at = :at WHERE id = :sid", at=iso(), sid=sheet["id"])
    db.commit()
    inputs = sheets.schedule_inputs(sheet)
    rankers, didnt_rank = sheets.unplaced(sheets.get_assignments(sheet["id"]), inputs.rows,
                                          sheets.get_roster(sheet["id"]), inputs.days, inputs.tests_counted)
    nobody = [r["display_name"] for r in rankers + didnt_rank]
    message = ("Published. Each student now sees their day when they open the class link. Post the message below "
               "to let them know.")
    if nobody:
        message += (f" {plural(len(nobody), 'student has', 'students have')} no day yet and will see that: "
                    f"{_names(nobody)}. Give them a day under Schedule — nobody else has to move.")
    flash(message, "info" if nobody else "success")
    return _back(sheet, "publish")


@bp.route("/s/<sid>/unpublish", methods=["POST"])
@instructor_required
def unpublish(sid):
    sheet = owned_sheet(sid)
    db.run("UPDATE sheets SET published_at = NULL, updated_at = :at WHERE id = :sid", at=iso(), sid=sheet["id"])
    db.commit()
    flash("The schedule is hidden from students again.", "success")
    return _back(sheet, "schedule")


def _csv_response(body, filename):
    return Response(body.encode("utf-8"), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@bp.route("/s/<sid>/schedule.csv")
@instructor_required
def export_csv(sid):
    sheet = owned_sheet(sid)
    body = sheets.schedule_csv(sheets.get_days(sheet["id"]), sheets.get_assignments(sheet["id"]),
                               sheets.get_submissions(sheet["id"]), sheets.get_roster(sheet["id"]),
                               sheet["capacity_per_day"])
    if sheet["bidding_open"]:
        suffix = "-made-before-reopening"
    elif sheet["schedule_signature"] != sheets.schedule_inputs(sheet).signature:
        suffix = "-out-of-date"
    else:
        suffix = ""
    return _csv_response(body, f"{slugify(sheet['title'])}-schedule{suffix}.csv")


@bp.route("/s/<sid>/rankings.csv")
@instructor_required
def rankings_csv(sid):
    sheet = owned_sheet(sid)
    body = sheets.rankings_csv(sheets.get_days(sheet["id"]), sheets.get_submissions(sheet["id"]),
                               sheets.get_roster(sheet["id"]), _zone())
    return _csv_response(body, f"{slugify(sheet['title'])}-rankings.csv")


# ---------------------------------------------------------------------------
# Backups and recovery
# ---------------------------------------------------------------------------
def _zip_response(filename, data):
    return Response(data, mimetype="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@bp.route("/s/<sid>/backup.zip")
@instructor_required
def download_backup(sid):
    sheet = owned_sheet(sid)
    filename, data = sheets.build_zip(sheets.export_sheet(sheet), "downloaded", _zone())
    return _zip_response(filename, data)


@bp.route("/s/<sid>/backup/email", methods=["POST"])
@instructor_required
def email_backup_now(sid):
    sheet = owned_sheet(sid)
    if _backup_is_current(sheet):
        flash("A backup with these same rankings was emailed a few minutes ago — check your inbox.", "info")
        return _back(sheet, "backups")
    sent, message = email_backup(sheet, "you asked for it")
    flash(message, "success" if sent else "error")
    return _back(sheet, "backups")


@bp.route("/snapshots/<snap_id>.zip")
@instructor_required
def snapshot_zip(snap_id):
    snap = _own_snapshot(snap_id)
    filename, data = sheets.build_zip(json.loads(snap["data"]), snap["reason"], _zone())
    return _zip_response(filename, data)


def _restore_page(sheet, other, source, action_url, different_sheet=None, file_new=False):
    current = sheets.export_sheet(sheet)
    return render_template(
        "restore_confirm.html", sheet=sheet, diff=sheets.compare_exports(current, other), other=other,
        source=source, action_url=action_url, different_sheet=different_sheet, file_new=file_new,
    )


@bp.route("/s/<sid>/restore/<snap_id>", methods=["GET", "POST"])
@instructor_required
def restore_snapshot(sid, snap_id):
    sheet = owned_sheet(sid)
    snap = _own_snapshot(snap_id)
    if snap["sheet_id"] != sheet["id"]:
        abort(404)
    data = json.loads(snap["data"])
    if request.method == "GET":
        return _restore_page(sheet, data, snap, url_for("teach.restore_snapshot", sid=sheet["id"], snap_id=snap_id))
    keep_newer = request.form.get("mode") != "exact"
    before_id = sheets.take_snapshot(sheet, "restore", "Before going back to an earlier version")
    before = db.row("SELECT reason, created_at FROM snapshots WHERE id = :id", id=before_id)
    changed = sheets.restore(sheet["id"], sheet["owner_id"], data, keep_newer=keep_newer)
    auth.end_student_sessions(sheet["id"], changed, reason="list")
    db.commit()
    zone = _zone()
    flash(f"Your sheet now matches the version from {sheets.file_time(snap['created_at'], zone, seconds=True)}"
          + (", plus rankings made since." if keep_newer else ".")
          + f" Changed your mind? Go back to the version from {sheets.file_time(before['created_at'], zone, seconds=True)}"
          f" (“{before['reason']}”) under Earlier versions.", "success")
    return _back(sheet, "backups")


@bp.route("/s/<sid>/restore-file", methods=["POST"])
@instructor_required
def restore_file(sid):
    sheet = owned_sheet(sid)
    upload = request.files.get("backup")
    if not upload or not upload.filename:
        flash("Choose a backup file first.", "error")
        return _back(sheet, "backups")
    data, problem = sheets.read_backup_file(upload.read(sheets.MAX_BACKUP_JSON_BYTES + 1))
    if not data:
        flash(problem, "error")
        return _back(sheet, "backups")
    pid = _store_pending(sheet, "backup", data)
    return redirect(url_for("teach.confirm_restore_file", sid=sheet["id"], pid=pid))


@bp.route("/s/<sid>/restore-file/<pid>", methods=["GET", "POST"])
@instructor_required
def confirm_restore_file(sid, pid):
    sheet = owned_sheet(sid)
    data = _get_pending(sheet, pid, "backup")
    if not data:
        flash("That upload has expired — please choose the backup file again.", "error")
        return _back(sheet, "backups")
    different = data["sheet"].get("id") != sheet["id"]
    if request.method == "GET":
        return _restore_page(
            sheet, data, {"created_at": data["exported_at"], "reason": "backup file"},
            url_for("teach.confirm_restore_file", sid=sheet["id"], pid=pid),
            different_sheet=data["sheet"]["title"] if different else None, file_new=True,
        )
    mode = request.form.get("mode")
    db.run("DELETE FROM pending_uploads WHERE id = :id", id=pid)
    if mode == "new":
        new_sid = sheets.new_sheet_id()
        sheets.restore(new_sid, sheet["owner_id"], data, keep_newer=False)
        db.commit()
        flash(f"Brought back “{data['sheet']['title']}” as a new sheet, with its own new student link.", "success")
        return redirect(url_for("teach.sheet", sid=new_sid))
    sheets.take_snapshot(sheet, "restore", "Before restoring from a backup file")
    changed = sheets.restore(sheet["id"], sheet["owner_id"], data, keep_newer=mode != "exact")
    auth.end_student_sessions(sheet["id"], changed)
    db.commit()
    flash(f"Restored from the backup taken {sheets.file_time(data['exported_at'], _zone())}.", "success")
    return _back(sheet, "backups")


@bp.route("/restore-file", methods=["POST"])
@instructor_required
def restore_file_as_new():
    """From the dashboard: bring a sheet back from a backup file, as a new sheet."""
    upload = request.files.get("backup")
    if not upload or not upload.filename:
        flash("Choose a backup file first.", "error")
        return redirect(url_for("teach.dashboard"))
    data, problem = sheets.read_backup_file(upload.read(sheets.MAX_BACKUP_JSON_BYTES + 1))
    if not data:
        flash(problem, "error")
        return redirect(url_for("teach.dashboard"))
    new_sid = sheets.new_sheet_id()
    sheets.restore(new_sid, current_instructor()["id"], data, keep_newer=False)
    db.commit()
    flash(f"Brought back “{data['sheet']['title']}” from the backup taken "
          f"{sheets.file_time(data['exported_at'], _zone())}. It has its own new student link.", "success")
    return redirect(url_for("teach.sheet", sid=new_sid))


@bp.route("/restore-deleted/<snap_id>", methods=["POST"])
@instructor_required
def restore_deleted(snap_id):
    snap = _own_snapshot(snap_id)
    if snap["kind"] != "deleted":
        abort(404)
    if sheets.get_sheet(snap["sheet_id"]):
        flash("That sheet is already back.", "info")
        return redirect(url_for("teach.sheet", sid=snap["sheet_id"]))
    try:
        sheets.restore(snap["sheet_id"], snap["owner_id"], json.loads(snap["data"]), keep_newer=False)
    except PermissionError:
        abort(404)
    db.run("DELETE FROM snapshots WHERE id = :id", id=snap["id"])
    db.commit()
    flash("Brought the sheet back, with everything it had. Its student link works again too.", "success")
    return redirect(url_for("teach.sheet", sid=snap["sheet_id"]))


# ---------------------------------------------------------------------------
# Deleting
# ---------------------------------------------------------------------------
@bp.route("/s/<sid>/delete-submission", methods=["POST"])
@instructor_required
def delete_submission(sid):
    sheet = owned_sheet(sid)
    key = request.form.get("name_key", "")
    found = db.row("SELECT display_name FROM submissions WHERE sheet_id = :sid AND name_key = :key",
                   sid=sheet["id"], key=key)
    if not found:
        flash("That ranking was already gone.", "info")
        return _back(sheet, "signups")
    snap_id = sheets.take_snapshot(sheet, "delete-one", f"Before deleting {found['display_name']}'s ranking")
    db.run("DELETE FROM submissions WHERE sheet_id = :sid AND name_key = :key", sid=sheet["id"], key=key)
    db.run("DELETE FROM assignments WHERE sheet_id = :sid AND name_key = :key", sid=sheet["id"], key=key)
    if sheet["bidding_open"]:
        # An open ranking page would save it right back; signing them out
        # stops that. With sign-ups closed nothing can be saved, so they
        # simply see that they have no ranking.
        auth.end_student_sessions(sheet["id"], [key], reason="ranking")
    sheets.touch(sheet["id"])
    db.commit()
    _offer_undo(sheet, snap_id, f"Deleted {found['display_name']}'s ranking.", ["submissions"], key=key)
    return _back(sheet, "signups")


@bp.route("/s/<sid>/delete-all", methods=["POST"])
@instructor_required
def delete_all(sid):
    sheet = owned_sheet(sid)
    if request.form.get("confirm", "").strip().upper() != "DELETE":
        flash("Nothing was deleted — type DELETE to confirm.", "error")
        return _back(sheet, "signups")
    count = db.scalar("SELECT COUNT(*) FROM submissions WHERE sheet_id = :sid", sid=sheet["id"]) or 0
    if not count:
        flash("There are no rankings to delete.", "info")
        return _back(sheet, "signups")
    snap_id = sheets.take_snapshot(sheet, "delete-all", "Before deleting every ranking")
    had_schedule = bool(db.scalar("SELECT 1 FROM assignments WHERE sheet_id = :sid LIMIT 1", sid=sheet["id"]))
    for table in ("submissions", "assignments"):
        db.run(f"DELETE FROM {table} WHERE sheet_id = :sid", sid=sheet["id"])
    db.run("UPDATE sheets SET published_at = NULL WHERE id = :sid", sid=sheet["id"])
    sheets.touch(sheet["id"])
    db.commit()
    _offer_undo(sheet, snap_id, f"Deleted {plural(count, 'ranking')}{' and the schedule' if had_schedule else ''}. "
                "A backup is downloading now."
                + ("" if sheet["bidding_open"] else " Sign-ups are still closed — reopen them under Sign-ups if "
                   "students should rank again."),
                ["submissions", "assignments"])
    return redirect(url_for("teach.sheet", sid=sheet["id"], download=snap_id) + "#signups")


@bp.route("/s/<sid>/look", methods=["POST"])
@instructor_required
def set_look(sid):
    """The class's look: the theme and font its students see (each student
    can still pick their own; text size is always their own)."""
    sheet = owned_sheet(sid)
    data = request.get_json(silent=True) or {}
    theme, font = data.get("theme"), data.get("font")
    if theme not in settings.THEMES or font not in settings.FONTS:
        return jsonify(ok=False), 400
    db.run("UPDATE sheets SET theme = :theme, font = :font WHERE id = :sid",
           theme=None if theme == settings.DEFAULT_THEME else theme, font=None if font == "mixed" else font,
           sid=sheet["id"])
    db.commit()
    return jsonify(ok=True)


@bp.route("/s/<sid>/archive", methods=["POST"])
@instructor_required
def archive_sheet(sid):
    """Tuck a sheet away on the dashboard. Nothing else changes: its link
    keeps working, and it can be unarchived any time."""
    sheet = owned_sheet(sid)
    archive = request.form.get("archive", "1") == "1"
    db.run("UPDATE sheets SET archived_at = :at WHERE id = :sid", at=iso() if archive else None, sid=sheet["id"])
    db.commit()
    if archive:
        flash(f"Archived “{sheet['title']}”. It's under Archived sheets at the bottom of this page, and its link "
              "still works for students.", "success")
    else:
        flash(f"“{sheet['title']}” is back on your list.", "success")
    return redirect(url_for("teach.dashboard"))


# Free link-shortening services, tried in order. Only the class link is
# sent, and only after the instructor agrees to it.
SHORTENERS = (
    ("is.gd", "https://is.gd/create.php?format=simple&url={}", re.compile(r"^https://is\.gd/[A-Za-z0-9_]{1,30}$")),
    ("TinyURL", "https://tinyurl.com/api-create.php?url={}", re.compile(r"^https://tinyurl\.com/[A-Za-z0-9_-]{1,40}$")),
)


def _shorten(url):
    """A short link to `url` from the first service that answers, or ""."""
    import urllib.parse
    import urllib.request

    for _name, api, shape in SHORTENERS:
        try:
            ask = urllib.request.Request(api.format(urllib.parse.quote(url, safe="")),
                                         headers={"User-Agent": f"{settings.APP_NAME} (link shortener)"})
            with urllib.request.urlopen(ask, timeout=8) as answer:
                short = answer.read(300).decode("utf-8", "replace").strip()
        except Exception:  # noqa: BLE001 - a service being down just means trying the next
            continue
        if short.startswith("http://"):
            short = "https://" + short[len("http://"):]
        if shape.match(short):
            return short
    return ""


@bp.route("/s/<sid>/shorten", methods=["POST"])
@instructor_required
def shorten_link(sid):
    sheet = owned_sheet(sid)
    if not sheet["short_url"]:
        short = _shorten(url_for("student.signin", sid=sheet["id"], _external=True))
        if not short:
            flash("The link-shortening services didn't answer just now. Please try again in a minute; your full "
                  "link works either way.", "error")
            return _back(sheet)
        db.run("UPDATE sheets SET short_url = :url WHERE id = :sid", url=short, sid=sheet["id"])
        db.commit()
        sheet = sheets.get_sheet(sheet["id"])
    flash(f"Short link: {sheet['short_url']}. It opens the same sign-up page as the full link.", "success")
    return _back(sheet)


@bp.route("/s/<sid>/delete-sheet", methods=["POST"])
@instructor_required
def delete_sheet(sid):
    sheet = owned_sheet(sid)
    if request.form.get("confirm", "").strip().upper() != "DELETE":
        flash("Nothing was deleted — type DELETE to confirm.", "error")
        return redirect(url_for("teach.dashboard")) if request.form.get("back") == "dashboard" else _back(sheet)
    snap_id = sheets.delete_sheet(sheet)
    db.commit()
    flash(
        f"Deleted “{sheet['title']}”. A backup is downloading, and you can bring the sheet back from "
        "“Recently deleted”, below your sheets, for 30 days.",
        "success",
    )
    return redirect(url_for("teach.dashboard", download=snap_id))
