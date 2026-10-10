"""
Class lists straight from Canvas, by the "Send to Scheduler" button: a
bookmark the professor keeps (bookmarklet/send_to_scheduler.js) that runs
on a Canvas page, signed in as them, reads a course's students (names and
emails) from Canvas's own API, and hands them to this site, either

- to the Scheduler page that opened that Canvas window (a message only this
  site can receive; app.js shows the list, and the instructor presses Add),
  or
- by a post from Canvas to /canvas-import. That post comes from another
  site, so it carries no sign-in and can't be trusted to do anything by
  itself: the list waits in canvas_imports, under an unguessable id, until
  a signed-in instructor looks it over and picks the sheet it goes to.
  Lists wait an hour at most, and go once they're used.

Either way the list then goes through the same reading and checks as a
pasted one (roster.parse_roster), as "Name, email" lines.
"""

import json
import os
import re
from datetime import timedelta
from urllib.parse import quote

import db
from util import iso, new_id, now, parse_iso

KEEP = timedelta(hours=1)
MAX_BYTES = 400 * 1024
MAX_STUDENTS = 3000
SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bookmarklet", "send_to_scheduler.js")


def _text(value, limit):
    return " ".join(str(value or "").split())[:limit]


def read(raw):
    """The list from a post or a message: {course, host, students: [{name,
    email}]}, or None if it isn't one."""
    if not raw or len(raw) > MAX_BYTES:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("type") != "scheduler-class-list":
        return None
    people = data.get("students")
    if not isinstance(people, list) or not people or len(people) > MAX_STUDENTS:
        return None
    students = []
    for person in people:
        if not isinstance(person, dict):
            return None
        name, email = _text(person.get("name"), 200), _text(person.get("email"), 254)
        if name or email:
            students.append({"name": name, "email": email})
    if not students:
        return None
    return {"course": _text(data.get("course"), 200) or "your Canvas course",
            "host": _text(data.get("host"), 200).lower(), "students": students}


def lines(data):
    """The list as a pasted one would be: "Name, email" a line."""
    return "\n".join(f"{s['name']}, {s['email']}" if s["name"] and s["email"] else s["name"] or s["email"]
                     for s in data["students"])


def store(data):
    """Keep it for a signed-in instructor to pick up. Returns its id. Commits."""
    iid = new_id(22)
    db.run("INSERT INTO canvas_imports (id, data, created_at) VALUES (:id, :data, :at)",
           id=iid, data=json.dumps(data), at=iso())
    db.commit()
    return iid


def load(iid):
    """The waiting list, or None if there's none (or it's over an hour old)."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,40}", iid or ""):
        return None
    row = db.row("SELECT data, created_at FROM canvas_imports WHERE id = :id", id=iid)
    if not row or parse_iso(row["created_at"]) < now() - KEEP:
        return None
    return json.loads(row["data"])


def forget(iid):
    db.run("DELETE FROM canvas_imports WHERE id = :id", id=iid)


def bookmarklet(site):
    """The "Send to Scheduler" bookmark, for this site: the script with its
    comments and indentation taken out, as a javascript: address."""
    with open(SOURCE, encoding="utf-8") as handle:
        code = handle.read()
    kept = [line.strip() for line in code.splitlines() if line.strip() and not line.strip().startswith("//")]
    code = "\n".join(kept).replace("__SITE__", site.rstrip("/"))
    return "javascript:" + quote(code, safe="(){}[];,.:=!+-*/&|?'$_")
