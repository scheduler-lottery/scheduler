"""
"Connect Canvas": the class list straight from Canvas's API, the way Canvas
means outside sites to ask. The professor is sent to their school's Canvas,
signs in there if they need to (Scheduler never sees a password), and
presses Authorize on Canvas's own page. Canvas hands back a short-lived key
(OAuth2) that can only do what the school's developer key allows: here, read
the professor's courses and a course's users. Scheduler reads the courses,
then the class list of the one the professor picks, keeps each student's
name and email, and gives the key back to Canvas (revokes it) at once.

Off until the school's Canvas admins issue a developer key and the site has
CANVAS_OAUTH_HOST, CANVAS_OAUTH_CLIENT_ID and CANVAS_OAUTH_CLIENT_SECRET
(docs/northwestern-canvas-request.md). Between Canvas's answer and the
professor's pick, the key waits in their own session, sealed (encrypted with
a key only this site has) and good for ten minutes.

The same reading serves a key the professor makes themselves in Canvas
(Account > Settings > New access token) and pastes in: used once, for their
own school's Canvas, then deleted in Canvas the same way.
"""

import base64
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from cryptography.fernet import Fernet, InvalidToken

import settings

SCOPES = ("url:GET|/api/v1/courses", "url:GET|/api/v1/courses/:course_id/users")
SEALED_FOR = 600  # seconds
MAX_PAGES = 40


class CanvasError(Exception):
    """Canvas said no, or didn't answer. .why: signin, forbidden, missing, canvas."""

    def __init__(self, why):
        super().__init__(why)
        self.why = why


def ready():
    return bool(settings.CANVAS_OAUTH_HOST and settings.CANVAS_OAUTH_CLIENT_ID and settings.CANVAS_OAUTH_CLIENT_SECRET)


def base(host=None):
    host = host or settings.CANVAS_OAUTH_HOST
    local = re.fullmatch(r"(127\.0\.0\.1|localhost)(:\d+)?", host) and not settings.IS_VERCEL
    return ("http://" if local else "https://") + host


def authorize_url(state, redirect_uri):
    return base() + "/login/oauth2/auth?" + urllib.parse.urlencode({
        "client_id": settings.CANVAS_OAUTH_CLIENT_ID, "response_type": "code", "redirect_uri": redirect_uri,
        "state": state, "scope": " ".join(SCOPES),
    })


def _http(method, url, headers=None, data=None):
    """(status, headers, body text). Tests replace this with the stand-in Canvas."""
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    ask = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(ask, timeout=15) as answer:
            return answer.status, dict(answer.headers), answer.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers or {}), err.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, {}, ""


def exchange(code, redirect_uri):
    """Canvas's one-time code for a key."""
    status, _headers, body = _http("POST", base() + "/login/oauth2/token", {"Accept": "application/json"}, {
        "grant_type": "authorization_code", "client_id": settings.CANVAS_OAUTH_CLIENT_ID,
        "client_secret": settings.CANVAS_OAUTH_CLIENT_SECRET, "redirect_uri": redirect_uri, "code": code,
    })
    try:
        token = json.loads(body).get("access_token") if status == 200 else None
    except ValueError:
        token = None
    if not token:
        raise CanvasError("canvas")
    return token


def _get_all(token, path, host=None):
    root = base(host)
    url, out = root + path, []
    for _ in range(MAX_PAGES):
        status, headers, body = _http("GET", url, {"Authorization": "Bearer " + token, "Accept": "application/json"})
        if status == 401:
            raise CanvasError("forbidden" if re.search(r"not authorized|unauthorized|insufficient scope", body, re.I)
                              else "signin")
        if status in (403, 404):
            raise CanvasError("forbidden" if status == 403 else "missing")
        if status != 200:
            raise CanvasError("canvas")
        try:
            data = json.loads(body[len("while(1);"):] if body.startswith("while(1);") else body)
        except ValueError:
            raise CanvasError("canvas")
        if not isinstance(data, list):
            raise CanvasError("canvas")
        out.extend(data)
        link = next((v for k, v in headers.items() if k.lower() == "link"), "")
        found = re.search(r'<([^>]+)>;\s*rel="next"', link)
        url = found.group(1) if found and found.group(1).startswith(root + "/") else None
        if not url:
            return out
    return out


def courses(token, host=None):
    """The professor's teaching courses: [{id, name, term}], this term's first."""
    found = []
    for c in _get_all(token, "/api/v1/courses?per_page=100&include[]=term&include[]=total_students", host):
        if not isinstance(c, dict) or not c.get("id") or not c.get("name"):
            continue
        roles = [str(e.get("type") or "").lower() for e in c.get("enrollments") or [] if isinstance(e, dict)]
        if roles and not any(r in ("teacher", "ta", "designer") for r in roles):
            continue
        term = c.get("term") if isinstance(c.get("term"), dict) else {}
        term_name = str(term.get("name") or "")
        found.append({
            "id": str(c["id"]), "name": " ".join(str(c["name"]).split())[:200],
            "term": "" if re.search(r"default term", term_name, re.I) else term_name[:80],
            "students": c.get("total_students") if isinstance(c.get("total_students"), int) else None,
        })
    return found


def students(token, course_id, host=None):
    """A course's students as [{name, email}]."""
    if not re.fullmatch(r"\d{1,15}", str(course_id)):
        raise CanvasError("missing")
    people, seen = [], set()
    for u in _get_all(token, f"/api/v1/courses/{course_id}/users?enrollment_type[]=student&include[]=email&per_page=100",
                      host):
        if not isinstance(u, dict) or u.get("id") in seen:
            continue
        seen.add(u.get("id"))
        login = str(u.get("login_id") or "")
        email = str(u.get("email") or (login if "@" in login else ""))
        name = str(u.get("name") or u.get("sortable_name") or "")
        if name or email:
            people.append({"name": name, "email": email})
    return people


def revoke(token, host=None):
    """Give the key back: Canvas forgets it. Never raises."""
    try:
        _http("DELETE", base(host) + "/login/oauth2/token", {"Authorization": "Bearer " + token})
    except Exception:  # noqa: BLE001
        pass


def _box():
    secret = settings.SECRET_KEY or ("" if settings.IS_VERCEL else "local development only")
    key = hashlib.sha256(b"scheduler/canvas-oauth|" + secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def seal(token):
    return _box().encrypt(token.encode()).decode("ascii")


def unseal(sealed):
    """The key, if it was sealed here within the last ten minutes; else None."""
    try:
        return _box().decrypt(str(sealed or "").encode("ascii"), ttl=SEALED_FOR).decode()
    except (InvalidToken, ValueError):
        return None
