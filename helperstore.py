"""
Which version of Scheduler Helper the Chrome Web Store hands out right now,
asked of Chrome's own update service (the address Chrome itself asks when it
updates extensions), and remembered for six hours. From 1.1.0 the helper
reads any school's Canvas (each allowed once by the professor), so the
class-list step offers it to every school as soon as the store has 1.1.0;
until then, only to the schools it reads from the start (Northwestern).
Nothing about anyone is sent: just the helper's public id.
"""

import json
import re
import threading
import urllib.request
from datetime import timedelta

from sqlalchemy import text

import db
import settings
from util import iso, now, parse_iso

UPDATE_URL = ("https://clients2.google.com/service/update2/crx?response=updatecheck&acceptformat=crx3"
              "&prodversion=141.0.0.0&x=id%3D{id}%26installsource%3Dondemand%26uc")
ANY_SCHOOL = (1, 1, 0)  # the first version that reads any school's Canvas
REMEMBER = timedelta(hours=6)
RETRY = timedelta(minutes=10)  # after the update service didn't answer
KEY = "helper-store-version"

_known = {"version": None, "until": None}
_lock = threading.Lock()


def _parts(version):
    try:
        return tuple(int(n) for n in str(version).split("."))
    except ValueError:
        return ()


def _ask():
    """The version the store hands out ("1.0.6"), or None if the update
    service couldn't be asked."""
    try:
        with urllib.request.urlopen(UPDATE_URL.format(id=settings.CHROME_HELPER_ID), timeout=4) as answer:
            found = re.search(r'<updatecheck\b[^>]*\bversion="([\d.]{1,20})"', answer.read(20000).decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - then only the schools it reads from the start
        return None
    return found.group(1) if found else None


def store_version():
    """The store's version, as last learned (this server, then the database,
    then the update service). Never raises."""
    with _lock:
        if _known["until"] and _known["until"] > now():
            return _known["version"]
    version, until = None, None
    try:
        saved = db.scalar("SELECT value FROM app_state WHERE key = :key", key=KEY)
        if saved:
            found = json.loads(saved)
            if parse_iso(found["until"]) > now():
                version, until = found.get("version"), parse_iso(found["until"])
    except Exception:  # noqa: BLE001
        pass
    if until is None:
        version = _ask()
        until = now() + (REMEMBER if version else RETRY)
        try:
            with db.get_engine().begin() as connection:
                connection.execute(
                    text("INSERT INTO app_state (key, value) VALUES (:key, :value) "
                         "ON CONFLICT (key) DO UPDATE SET value = excluded.value"),
                    {"key": KEY, "value": json.dumps({"version": version, "until": iso(until)})},
                )
        except Exception:  # noqa: BLE001 - only a remembered answer
            pass
    with _lock:
        _known.update(version=version, until=until)
    return version


def any_school():
    """May the class-list step offer the helper for any school's Canvas?
    CANVAS_HELPER_ANY_SCHOOL=1 or 0 says so outright; trying it locally
    (CANVAS_DEV_HOST), yes; otherwise, once the store has 1.1.0."""
    if settings.CANVAS_HELPER_ANY_SCHOOL in ("1", "0"):
        return settings.CANVAS_HELPER_ANY_SCHOOL == "1"
    if settings.CANVAS_DEV_HOST:
        return True
    return _parts(store_version() or "") >= ANY_SCHOOL
