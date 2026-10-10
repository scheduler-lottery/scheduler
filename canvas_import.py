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
  a signed-in instructor looks it over and picks the sheet it goes to. The
  first instructor to open it is the only one who can (claimed_by).

Each instructor's button carries a key made for them (button_key), so a
list their own button sent can be told from one any other site posted:
only a list with the key goes in without a second look. Waiting lists go
once they're used, or once they're old (KEEP), swept away whenever another
arrives and by the daily job.

Either way only names and emails are kept, and they go through the same
checks as any class list (roster.parse_people).
"""

import functools
import json
import os
import re
from datetime import timedelta
from urllib.parse import quote

import db
from util import iso, new_id, now, parse_iso

KEEP = timedelta(minutes=30)
MAX_BYTES = 300 * 1024
MAX_STUDENTS = 3000
MAX_WAITING = 200  # lists waiting at once, from everyone: a ceiling no flood gets past
VERSION = 2
SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bookmarklet", "send_to_scheduler.js")


def _text(value, limit):
    return " ".join(str(value or "").split())[:limit]


def read(raw):
    """The list from a post or a message: {course, course_id, host, key, v,
    students: [{name, email}]}, or None if it isn't one."""
    if not raw:
        return None
    if isinstance(raw, str):
        # A real list is one array of flat objects: anything nested deeper
        # (which could exhaust the parser) isn't one.
        if len(raw.encode("utf-8")) > MAX_BYTES or raw.count("[") + raw.count("{") > 2 * MAX_STUDENTS + 10:
            return None
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError):
            return None
    else:
        data = raw
    if not isinstance(data, dict) or data.get("type") != "scheduler-class-list":
        return None
    people = data.get("students")
    if not isinstance(people, list) or not people or len(people) > MAX_STUDENTS:
        return None
    students = []
    for person in people:
        if not isinstance(person, dict):
            return None
        name, email = _text(person.get("name"), 120), _text(person.get("email"), 254)
        if "@" not in email:
            email = ""
        if name or email:
            students.append({"name": name, "email": email})
    if not students:
        return None
    course_id = str(data.get("courseId") or "")
    key = str(data.get("key") or "")
    version = data.get("v")
    return {"course": _text(data.get("course"), 200) or "your Canvas course",
            "course_id": course_id if re.fullmatch(r"\d{1,15}", course_id) else "",
            "host": _text(data.get("host"), 200).lower(),
            "key": key if re.fullmatch(r"[A-Za-z0-9]{1,64}", key) else "",
            "v": version if isinstance(version, int) else 1,
            "students": students}


def people(data):
    """The list as (name, email) pairs, for roster.parse_people."""
    return [(s["name"], s["email"]) for s in data["students"]]


def lines(data):
    """The list as a pasted one would be: "Name, email" a line, with a name
    that has a comma in it ("Kim, Alex") in quotes."""
    def name(value):
        return '"' + value.replace('"', '""') + '"' if "," in value or '"' in value else value
    return "\n".join(f"{name(s['name'])}, {s['email']}" if s["name"] and s["email"] else s["name"] or s["email"]
                     for s in data["students"])


def button_key(instructor_id):
    """The key in this instructor's own Send to Scheduler button."""
    from signin import keyed_hash  # (signin imports a lot; only needed here)
    return keyed_hash("canvas-button|" + instructor_id)[:24] if instructor_id else ""


def sent_by(data, instructor_id):
    """Did this instructor's own button send this list?"""
    import hmac
    expected = button_key(instructor_id)
    return bool(expected) and hmac.compare_digest(str(data.get("key") or "").encode(), expected.encode())


def sweep():
    db.run("DELETE FROM canvas_imports WHERE created_at < :t", t=iso(now() - KEEP))


def store(data, origin):
    """Keep it for a signed-in instructor to pick up, with the site that
    really sent it (the post's Origin, not what the list says). Returns its
    id, or None when too many are waiting already. A real list is opened
    (claimed) within moments of arriving, so when the room runs out, the
    oldest lists nobody opened make way: a flood of posts can't keep real
    ones out. Commits."""
    sweep()
    waiting = db.scalar("SELECT COUNT(*) FROM canvas_imports") or 0
    if waiting >= MAX_WAITING:
        oldest = [r["id"] for r in db.rows(
            "SELECT id FROM canvas_imports WHERE claimed_by IS NULL ORDER BY created_at LIMIT :n",
            n=waiting - MAX_WAITING + 1)]
        if len(oldest) < waiting - MAX_WAITING + 1:
            db.commit()
            return None
        for old in oldest:
            forget(old)
    iid = new_id(22)
    kept = {k: data[k] for k in ("course", "course_id", "key", "v", "students")}
    db.run("INSERT INTO canvas_imports (id, data, origin, created_at) VALUES (:id, :data, :origin, :at)",
           id=iid, data=json.dumps(kept, ensure_ascii=False, separators=(",", ":")), origin=origin or "", at=iso())
    db.commit()
    return iid


def load(iid, instructor_id):
    """The waiting list, for this instructor: the first to open it claims
    it. None if there's none, it's someone else's, or it's too old."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,40}", iid or ""):
        return None
    row = db.row("SELECT data, origin, claimed_by, created_at FROM canvas_imports WHERE id = :id", id=iid)
    if not row:
        return None
    if parse_iso(row["created_at"]) < now() - KEEP:
        forget(iid)
        db.commit()
        return None
    if row["claimed_by"] and row["claimed_by"] != instructor_id:
        return None
    if not row["claimed_by"]:
        db.run("UPDATE canvas_imports SET claimed_by = :me WHERE id = :id AND claimed_by IS NULL",
               me=instructor_id, id=iid)
        db.commit()
        if db.scalar("SELECT claimed_by FROM canvas_imports WHERE id = :id", id=iid) != instructor_id:
            return None
    data = json.loads(row["data"])
    data["host"] = row["origin"] or ""
    return data


def forget(iid):
    db.run("DELETE FROM canvas_imports WHERE id = :id", id=iid)


def wait_longer(iid, instructor_id):
    """Their list, kept another half hour from now (while they make a sheet
    for it). Commits."""
    db.run("UPDATE canvas_imports SET created_at = :at WHERE id = :id AND claimed_by = :me",
           at=iso(), id=iid, me=instructor_id)
    db.commit()


@functools.lru_cache(maxsize=1)
def _source():
    """The button's script, with its comments and indentation taken out."""
    with open(SOURCE, encoding="utf-8") as handle:
        code = handle.read()
    return "\n".join(line.strip() for line in code.splitlines()
                     if line.strip() and not line.strip().startswith("//"))


def bookmarklet(site, key=""):
    """The "Send to Scheduler" bookmark, for this site and this instructor,
    as a javascript: address."""
    site = re.sub(r"[^A-Za-z0-9.:/_-]", "", site).rstrip("/")
    key = re.sub(r"[^A-Za-z0-9]", "", key or "")
    code = _source().replace("__SITE__", site).replace("__KEY__", key)
    return "javascript:" + quote(code, safe="(){}[];,.:=!+-*/&|?'$_")
