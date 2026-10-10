"""
Class lists from Canvas, as they reach this site: Scheduler Helper (the
browser extension, extension/) reads a course's students from the
professor's own Canvas and hands this site's page the list, which the page
posts here when the professor presses Add; Connect Canvas (canvas_oauth)
builds the same list server to server. Either way it's read and checked here
(read), and only names and emails go on (people, for roster.parse_people).

canvas_imports held lists sent by the retired "Send to Scheduler" bookmark;
nothing writes it any more, and sweep clears anything left in it.
"""

import json
import re

import db
from util import iso, now

MAX_BYTES = 300 * 1024
MAX_STUDENTS = 3000


def _text(value, limit):
    """Plain text: no control characters, no lone surrogates (which can't
    be stored), spaces collapsed."""
    text = "".join(ch for ch in str(value or "") if ch >= " " and not 0xD800 <= ord(ch) <= 0xDFFF and ch != "\x7f")
    return " ".join(text.split())[:limit]


def read(raw):
    """The list from a post: {course, course_id, host, students: [{name,
    email}]}, or None if it isn't one."""
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
    return {"course": _text(data.get("course"), 200) or "your Canvas course",
            "course_id": course_id if re.fullmatch(r"\d{1,15}", course_id) else "",
            "host": _text(data.get("host"), 200).lower(),
            "students": students}


def people(data):
    """The list as (name, email) pairs, for roster.parse_people."""
    return [(s["name"], s["email"]) for s in data["students"]]


def sweep():
    """Anything left in canvas_imports goes (see the top)."""
    db.run("DELETE FROM canvas_imports WHERE created_at < :t", t=iso(now()))
