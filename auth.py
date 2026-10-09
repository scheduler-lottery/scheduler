"""
Who's signed in, and what they're allowed to see.

Two separate identities can live in one browser's sign-in cookie:

* an instructor (session["instructor_id"]), who sees only their own sheets;
* a student, per sheet (session["students"][sheet_id]), who sees only that
  one sheet's student pages.

Nothing gives one instructor access to another's sheets — including the site
owner, whose extra page (/owner) shows site-wide counts and nothing else.

A student's sign-in is re-checked on every visit, so the instructor can end
it from their side: resetting a PIN, taking a name off the class list, or
deleting a ranking signs out whoever was signed in as that student, on
every device.
"""

import hmac
import secrets
from datetime import timedelta
from functools import wraps

from flask import abort, flash, g, redirect, request, session, url_for

import db
import settings
from util import iso_precise, now, parse_iso

MAX_REMEMBERED_SHEETS = 12
# "I'm on a shared computer": signed out after this long without activity.
SHARED_COMPUTER_IDLE_MINUTES = 20


# ---------------------------------------------------------------------------
# Instructors
# ---------------------------------------------------------------------------
def current_instructor():
    if "instructor" not in g:
        g.instructor = None
        instructor_id = session.get("instructor_id")
        if instructor_id:
            found = db.row("SELECT * FROM instructors WHERE id = :id", id=instructor_id)
            if found and not found["disabled"]:
                g.instructor = found
            else:
                session.pop("instructor_id", None)
                g.instructor_switched_off = bool(found)
    return g.instructor


def is_owner(instructor=None):
    instructor = instructor or current_instructor()
    return bool(
        instructor and settings.OWNER_EMAIL and instructor["email"] == settings.OWNER_EMAIL
    )


def switched_off_message():
    contact = settings.CONTACT_EMAIL or settings.OWNER_EMAIL
    return (
        "This account has been switched off, so it can't sign in"
        + (f". If that's a mistake, write to {contact}." if contact else ".")
    )


def instructor_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_instructor():
            if g.get("instructor_switched_off"):
                flash(switched_off_message(), "error")
            elif request.method == "GET":
                session["teach_next"] = request.path
            return redirect(url_for("teach.login"))
        return view(*args, **kwargs)

    return wrapped


def owner_required(view):
    """The site owner's page. Signed out, you're asked to sign in (and
    brought back); signed in as anyone else, it doesn't exist."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_instructor():
            if request.method == "GET":
                session["teach_next"] = request.path
            flash("Sign in with the site owner's email to open that page.", "info")
            return redirect(url_for("teach.login"))
        if not is_owner():
            abort(404)
        return view(*args, **kwargs)

    return wrapped


# ---------------------------------------------------------------------------
# Students (one sign-in per sheet)
# ---------------------------------------------------------------------------
# session["students"][sheet_id] = {
#     "n": display name, "k": name key, "t": when they signed in,
#     "m": how — "code", "pin", or "preview" (an instructor's test student),
#     "x": True on a shared computer, with "s" = when last seen,
# }
def _entries():
    return dict(session.get("students") or {})


def _drop(sheet_id):
    students = _entries()
    if students.pop(sheet_id, None) is not None:
        session["students"] = students


def current_student(sheet):
    """The student signed in to this sheet in this browser, or None. Sets
    g.signed_out_because when a sign-in just ended, so the page can say
    why."""
    sid = sheet["id"]
    entry = _entries().get(sid)
    if not entry:
        return None
    if not isinstance(entry, dict) or not entry.get("k") or not entry.get("t"):
        _drop(sid)
        return None

    if entry.get("x"):
        seen = parse_iso(entry.get("s") or entry["t"])
        if now() - seen > timedelta(minutes=SHARED_COMPUTER_IDLE_MINUTES):
            _drop(sid)
            g.signed_out_because = "You were signed out after a while without activity, since this is a shared computer."
            return None
        if (now() - seen).total_seconds() > 60:
            students = _entries()
            students[sid] = {**entry, "s": iso_precise()}
            session["students"] = students

    ended = db.row(
        "SELECT at, reason FROM student_signouts WHERE sheet_id = :sid AND name_key = :key",
        sid=sid, key=entry["k"],
    )
    if ended and parse_iso(ended["at"]) >= parse_iso(entry["t"]):
        _drop(sid)
        g.signed_out_because = SIGNOUT_REASONS.get(ended["reason"], SIGNOUT_REASONS[""])
        return None

    if not sheet["allow_unlisted"] and not db.scalar(
        "SELECT 1 FROM roster WHERE sheet_id = :sid AND name_key = :key", sid=sid, key=entry["k"]
    ):
        _drop(sid)
        g.signed_out_because = (
            f"“{entry['n']}” isn't on the class list for this sign-up any more, so you've been signed "
            "out. If that's a mistake, ask your instructor."
        )
        return None
    return entry


def sign_in_student(sheet_id, name, key, method, shared=False):
    students = _entries()
    students.pop(sheet_id, None)
    entry = {"n": name, "k": key, "t": iso_precise(), "m": method}
    if shared:
        entry.update(x=True, s=entry["t"])
    students[sheet_id] = entry
    while len(students) > MAX_REMEMBERED_SHEETS:  # keep the cookie small
        students.pop(next(iter(students)))
    session["students"] = students
    for pending in ("pending_student", "pending_pin"):
        session.pop(pending, None)
    session.pop("dev_code", None)
    session.permanent = True


def sign_out_student(sheet_id):
    _drop(sheet_id)
    for pending in ("pending_student", "pending_pin"):
        found = session.get(pending)
        if found and found.get("sid") == sheet_id:
            session.pop(pending, None)
            session.pop("dev_code", None)


def sign_out_all_students():
    session.pop("students", None)
    session.pop("pending_student", None)
    session.pop("pending_pin", None)


# What a student signed out from the instructor's side is told, by reason.
SIGNOUT_REASONS = {
    "": "Please sign in again.",
    "pin": "Your instructor reset your PIN. Please sign in again and choose a new one.",
    "removed": "You were taken off the class list for this sign-up. If that's a mistake, email your instructor.",
    "ranking": "Your instructor removed your ranking. Sign in again to see where things stand.",
    "list": "Your instructor updated the class list, so please sign in again.",
    "linked": "Your sign-up was matched to your name on the class list. Please sign in again with that name.",
    "test": "The test students were removed. You can close this tab.",
}


def end_student_sessions(sheet_id, name_keys, reason="", email=""):
    """Sign out anyone signed in to this sheet as any of these students, on
    every device, the next time they load a page. `email` is the address
    they had, for "removed": putting them back with the same one lets those
    sign-ins carry on. Not committed here."""
    at = iso_precise()
    db.run_many(
        """
        INSERT INTO student_signouts (sheet_id, name_key, at, reason, email)
        VALUES (:sid, :key, :at, :reason, :email)
        ON CONFLICT (sheet_id, name_key) DO UPDATE SET
            at = excluded.at, reason = excluded.reason, email = excluded.email
        """,
        [{"sid": sheet_id, "key": key, "at": at, "reason": reason, "email": (email or "").lower()}
         for key in set(name_keys)],
    )


# ---------------------------------------------------------------------------
# Cross-site request forgery protection
# ---------------------------------------------------------------------------
def csrf_token():
    token = session.get("csrf")
    if not token:
        token = session["csrf"] = secrets.token_urlsafe(32)
    return token


def csrf_ok():
    """Every POST must carry the token from this browser's session, either
    as a form field or (for fetch requests) an X-CSRF-Token header."""
    expected = session.get("csrf")
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
    return bool(expected) and hmac.compare_digest(sent, expected)
