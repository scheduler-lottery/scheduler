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
import settings
from util import iso, now, parse_iso

SEARCH_URL = "https://canvas.instructure.com/api/v1/accounts/search"
HOST = re.compile(r"^(?=.{4,120}$)([a-z0-9-]+\.)+[a-z]{2,}$")
REMEMBER_DAYS = 30
RETRY_MINUTES = 5  # after the directory didn't answer
SHOWN = 10  # schools listed for a typed name
# A school's other ways into its Canvas, not the one its professors use.
SIDE_DOORS = re.compile(r"parent|observer|catalog|pre-?college|professional development|executive education"
                        r"|development instance|sandbox", re.I)


def clean_host(value):
    """A Canvas address typed or pasted any which way ("https://canvas.x.edu/courses/1")
    as a bare host ("canvas.x.edu"), or "" if it isn't one."""
    value = (value or "").strip().lower()
    value = re.sub(r"^[a-z]+://", "", value).split("/")[0].split("?")[0]
    return value if HOST.match(value) else ""


def _ask(**params):
    """Schools matching a name or domain (8, unless per_page says): [{name,
    domain}], or None if the directory couldn't be asked."""
    query = urllib.parse.urlencode({"per_page": 8, **params})
    try:
        ask = urllib.request.Request(f"{SEARCH_URL}?{query}", headers={"Accept": "application/json"})
        with urllib.request.urlopen(ask, timeout=5) as answer:
            found = json.load(answer)
    except Exception:  # noqa: BLE001 - the professor can still type their school
        return None
    schools = []
    for school in found if isinstance(found, list) else []:
        if not isinstance(school, dict):
            continue
        host = clean_host(str(school.get("domain", "")))
        name = " ".join(str(school.get("name", "")).split())[:120]
        if host and name:
            schools.append({"name": name, "domain": host})
    return schools


def search(query):
    """Schools whose name matches what the professor typed, likeliest first,
    and whether more matched than are shown. The directory answers in
    alphabetical order, so "Chicago" would list Chicago Jesuit Academy and
    leave out University of Chicago; ask for plenty, then rank."""
    query = " ".join((query or "").split())[:80]
    if len(query) < 2:
        return [], False
    found = []
    for school in _ask(name=query, per_page=50) or []:
        if school not in found:  # (the directory lists some schools twice or three times over)
            found.append(school)
    typed = query.lower()
    word = re.compile(r"\b" + re.escape(typed))
    usual = {f"university of {typed}", f"{typed} university"}
    found.sort(key=lambda s: (
        s["name"].lower() != typed,                       # exactly what was typed
        re.sub(r"^the |\s*\(.*\)$", "", s["name"].lower()) not in usual,  # "Columbia University", not "...College"
        not word.search(s["name"].lower()),               # a word starts with it ("Penn": Pennsylvania)
        bool(SIDE_DOORS.search(s["name"])),               # parents', catalog, pre-college portals last
        not re.search(r"universit|college|\blaw\b", s["name"], re.I),
        not s["domain"].startswith("canvas."),            # a school's own Canvas address
        len(s["name"]),
    ))
    return found[:SHOWN], len(found) > SHOWN


def name_of(host):
    """The school whose Canvas is at exactly this address, by name, or ""
    (for a professor who typed the address instead of picking the school).
    The directory also answers with addresses that only contain this one
    (iu.instructure.com: 13 others first), so ask for plenty. Several schools
    can share one Canvas (umsystem.instructure.com: Mizzou, UMKC, Missouri
    S&T...); no one name is right then, so "". A school Scheduler Helper
    reads is known without asking; anything else is one quick question to
    the directory. Never raises."""
    for name, canvas in settings.CANVAS_HELPER_SCHOOLS.values():
        if canvas == host:
            return name
    ours = [s["name"] for s in _ask(domain=host, per_page=50) or [] if s["domain"] == host]
    ours = sorted([n for n in ours if not SIDE_DOORS.search(n)] or ours, key=len)
    if not ours or not all(n == ours[0] or n.startswith(ours[0] + " ") for n in ours):
        return ""  # several schools (one school's entries all start with its name: "SW Law - Visitors")
    return ours[0]


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
    school, failed = None, False
    for start in range(len(labels) - 1):
        candidate = ".".join(labels[start:])
        schools = _ask(domain=candidate)
        if schools is None:
            failed = True
            break
        school = _best(schools, candidate)
        if school:
            break
    # A directory that didn't answer (even for school.edu after law.school.edu)
    # says nothing about the school: asked again in a few minutes, not after a
    # month, so one slow reply doesn't hide the guess from everyone there.
    until = now() + (timedelta(minutes=RETRY_MINUTES) if failed else timedelta(days=REMEMBER_DAYS))
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
