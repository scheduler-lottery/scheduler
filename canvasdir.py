"""
Which school's Canvas a professor uses, from Instructure's public directory
of schools (the one the Canvas Student app's "Find my school" searches): by
name when they type it, or guessed from their email's domain, so links go to
their school's own Canvas (canvas.northwestern.edu, not canvas.instructure.com).
Guesses are remembered per domain; only the domain is ever looked up.
"""

import json
import re
import urllib.parse
import urllib.request
from datetime import timedelta

from sqlalchemy import text

import db
from util import iso, now, parse_iso

SEARCH_URL = "https://canvas.instructure.com/api/v1/accounts/search"
HOST = re.compile(r"^(?=.{4,120}$)([a-z0-9-]+\.)+[a-z]{2,}$")
REMEMBER_DAYS = 30


def clean_host(value):
    """A Canvas address typed or pasted any which way ("https://canvas.x.edu/courses/1")
    as a bare host ("canvas.x.edu"), or "" if it isn't one."""
    value = (value or "").strip().lower()
    value = re.sub(r"^[a-z]+://", "", value).split("/")[0].split("?")[0]
    return value if HOST.match(value) else ""


def _ask(**params):
    """Schools matching a name or domain: [{name, domain}], or None if the
    directory couldn't be asked."""
    query = urllib.parse.urlencode({**params, "per_page": 8})
    try:
        ask = urllib.request.Request(f"{SEARCH_URL}?{query}", headers={"Accept": "application/json"})
        with urllib.request.urlopen(ask, timeout=5) as answer:
            found = json.load(answer)
    except Exception:  # noqa: BLE001 - the professor can still type their school
        return None
    schools = []
    for school in found if isinstance(found, list) else []:
        host = clean_host(str(school.get("domain", "")))
        name = " ".join(str(school.get("name", "")).split())[:120]
        if host and name:
            schools.append({"name": name, "domain": host})
    return schools


def search(query):
    """Schools whose name matches what the professor typed."""
    query = " ".join((query or "").split())[:80]
    return (_ask(name=query) or []) if len(query) >= 2 else []


def _best(schools, domain):
    """The school whose Canvas lives under this email domain: canvas.x.edu
    before a program's longer address (app.precollege.x.edu)."""
    ours = [s for s in schools if s["domain"] == domain or s["domain"].endswith("." + domain)]
    ours.sort(key=lambda s: (not s["domain"].startswith("canvas."), s["domain"].count("."), len(s["name"])))
    return ours[0] if ours else None


def guess(email):
    """{name, domain} of the school this address most likely belongs to, or
    None. Tries law.school.edu, then school.edu. Never raises; call it
    before the request writes anything (it may save on its own connection)."""
    domain = (email or "").rpartition("@")[2].strip().lower()
    labels = domain.split(".")
    if len(labels) < 2:
        return None
    key = "canvas-school:" + domain
    try:
        saved = db.scalar("SELECT value FROM app_state WHERE key = :key", key=key)
        if saved:
            found = json.loads(saved)
            if parse_iso(found["until"]) > now():
                return found.get("school")
    except Exception:  # noqa: BLE001
        pass
    school, asked = None, False
    for start in range(len(labels) - 1):
        candidate = ".".join(labels[start:])
        schools = _ask(domain=candidate)
        if schools is None:
            break
        asked = True
        school = _best(schools, candidate)
        if school:
            break
    until = now() + (timedelta(days=REMEMBER_DAYS) if asked else timedelta(hours=1))
    try:
        with db.get_engine().begin() as connection:
            connection.execute(
                text("INSERT INTO app_state (key, value) VALUES (:key, :value) "
                     "ON CONFLICT (key) DO UPDATE SET value = excluded.value"),
                {"key": key, "value": json.dumps({"school": school, "until": iso(until)})},
            )
    except Exception:  # noqa: BLE001 - only a remembered guess
        pass
    return school
