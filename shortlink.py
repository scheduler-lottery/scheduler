"""
Short links for a class's sign-in page, for a slide or the board, made by
spoo.me (a free, open-source shortener: spoo.me/xxxxxx, straight to the
page, no ads and no page in between; no account needed, 200 links a day
from one address). Each sheet gets one, made when the sheet is, kept in
sheets.short_url, and checked by the daily job (remade if it no longer
leads to the class). If spoo.me can't make one, the class page simply
shows no short link: the full link always works. (TinyURL showed ads, and
da.gd a "created recently" page to click through, so neither is used.)
"""

import json
import urllib.error
import urllib.parse
import urllib.request

import db
import settings
from util import iso, now, parse_iso

API = "https://spoo.me/"
TIMEOUT = 4  # seconds: a class page never waits longer than this, once a day at most
RETRY_HOURS = 24
CHECKS_PER_DAY = 60


def make(url):
    """A short link to `url` ("https://spoo.me/AbC123"), or None."""
    if not settings.SHORT_LINKS:
        return None
    request = urllib.request.Request(
        API, data=urllib.parse.urlencode({"url": url}).encode(), method="POST",
        headers={"Accept": "application/json", "User-Agent": "Scheduler (scheduler-lottery.vercel.app)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
            short = json.loads(answer.read(4096).decode()).get("short_url") or ""
    except (urllib.error.URLError, OSError, ValueError):
        return None
    short = short.replace("http://", "https://", 1)
    return short if short.startswith("https://spoo.me/") and 18 <= len(short) <= 40 else None


class _NoFollow(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def leads_to(short, url):
    """Does the short link still go straight to `url`? (None: couldn't tell.)"""
    opener = urllib.request.build_opener(_NoFollow)
    try:
        opener.open(urllib.request.Request(short, method="HEAD", headers={"User-Agent": "Mozilla/5.0"}),
                    timeout=TIMEOUT)
    except urllib.error.HTTPError as answer:
        if answer.code in (301, 302, 303, 307, 308):
            return answer.headers.get("Location", "").rstrip("/") == url.rstrip("/")
        return False if answer.code == 404 else None
    except (urllib.error.URLError, OSError):
        return None
    return False  # (a page, not a redirect)


def for_sheet(sheet, url):
    """The sheet's short link: the one it has, or a new one (tried at most
    once a day). Commits what it makes."""
    if (sheet.get("short_url") or "").startswith("https://spoo.me/"):  # (an older kind is made again)
        return sheet["short_url"]
    tried = sheet.get("short_url_at")
    if tried and (now() - parse_iso(tried)).total_seconds() < RETRY_HOURS * 3600:
        return None
    short = make(url)
    db.run("UPDATE sheets SET short_url = :short, short_url_at = :at WHERE id = :sid "
           "AND (short_url IS NULL OR short_url NOT LIKE 'https://spoo.me/%')", short=short, at=iso(), sid=sheet["id"])
    db.commit()
    return short


def check_all(url_for_sheet):
    """The daily job: open sheets' short links that no longer lead to their
    class are remade. Returns how many it remade."""
    rows = db.rows("SELECT id, short_url FROM sheets WHERE bidding_open = 1 AND short_url LIKE 'https://spoo.me/%' "
                   "ORDER BY short_url_at LIMIT :n", n=CHECKS_PER_DAY)
    remade = 0
    for row in rows:
        url = url_for_sheet(row["id"])
        if leads_to(row["short_url"], url) is False:
            short = make(url)
            db.run("UPDATE sheets SET short_url = :short, short_url_at = :at WHERE id = :sid",
                   short=short, at=iso(), sid=row["id"])
            db.commit()
            remade += 1
        else:
            db.run("UPDATE sheets SET short_url_at = :at WHERE id = :sid", at=iso(), sid=row["id"])
            db.commit()
    return remade
