"""
Scheduler — fair presentation-day sign-ups for any class.

This file is what Vercel runs: it looks for a Flask object named `app` in
app.py. Locally, run `python app.py` and open http://127.0.0.1:5050.

The pieces:
  teach.py    instructor pages (/teach/...)       — only ever your own sheets
  student.py  student pages (/c/<sheet id>/...)   — one class at a time
  owner.py    site-wide counts for the owner (/owner) — no one's data
  signin.py   emailed one-time codes and their rate limits
  sheets.py   sheet data, backups and restore points
  roster.py   reading class lists out of Canvas exports and spreadsheets
  matching_engine.py  the assignment algorithms
"""

import base64
import hmac
import ipaddress
import re
from datetime import timedelta

from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from flask_mailman import Mail
from markupsafe import Markup, escape
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

import auth
import canvas_import
import canvas_oauth
import db
import deadlines
import maintenance
import owner
import settings
import sheets
import signin
import student
import teach
import usage
from matching_engine import ALGORITHMS, DEFAULT_ALGORITHM, example_views
from util import ID_ALPHABET, in_zone, initials, iso, new_id, now, parse_iso, plural

app = Flask(__name__, static_folder="public/static", static_url_path="/static")

# On Vercel, files in public/ are served straight from the CDN; locally,
# Flask serves the same folder so `python app.py` looks identical.
app.secret_key = settings.SECRET_KEY or "local-development-only-not-secret"
app.config.update(
    PERMANENT_SESSION_LIFETIME=timedelta(days=90),
    SESSION_COOKIE_NAME="scheduler_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=settings.IS_VERCEL,
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    MAIL_SERVER=settings.SMTP_HOST,
    MAIL_PORT=settings.SMTP_PORT,
    MAIL_USERNAME=settings.SMTP_USER,
    MAIL_PASSWORD=settings.SMTP_PASSWORD,
    MAIL_USE_SSL=settings.SMTP_PORT == 465,
    MAIL_USE_TLS=settings.SMTP_PORT != 465,
    MAIL_TIMEOUT=15,
    MAIL_DEFAULT_SENDER=settings.SMTP_FROM,
)
mail = Mail(app)

if settings.IS_VERCEL:
    # Vercel's edge terminates HTTPS and forwards the real scheme, host, and
    # client address; trust exactly one hop of those headers.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

URL_IN_TEXT = re.compile(r"\bhttps?://[^\s<>\"']+[^\s<>\"'.,;:!?)\]]")


def autolink(text):
    """Text as the instructor wrote it, with web addresses made clickable.
    Escaped first, so nothing in it can become markup."""
    escaped = str(escape(text or ""))
    return Markup(URL_IN_TEXT.sub(
        lambda m: f'<a href="{m.group(0)}" target="_blank" rel="noopener nofollow">{m.group(0)}</a>', escaped
    ))


TIME_MARKER = re.compile(r"\[\[at:([0-9T:.+\-Z]{10,40})\]\]")


def local_times(text):
    """A message with [[at:timestamp]] markers (from signin.clock) as
    times each reader's browser shows in their own time zone — the class's
    time zone until it does. Escaped first, so nothing else becomes markup."""
    sheet = g.get("sheet")
    instructor = auth.current_instructor()
    zone = ((sheet and sheet.get("owner_timezone")) or (instructor and instructor.get("timezone"))
            or settings.DEFAULT_TIMEZONE)

    def swap(match):
        try:
            when = parse_iso(match.group(1))
        except ValueError:
            return ""
        shown = in_zone(when, zone)
        hour = shown.strftime("%I").lstrip("0") or "12"
        return f'<time datetime="{iso(when)}" data-local="time">{hour}:{shown.strftime("%M %p")}</time>'

    return Markup(TIME_MARKER.sub(swap, str(escape(text or ""))))


# The "Send to Scheduler" bookmark, made for the address this site is on.
app.add_template_filter(initials, "initials")
app.add_template_filter(local_times, "local_times")
app.add_template_filter(autolink, "autolink")
app.add_template_filter(lambda n, word, many=None: plural(n, word, many), "plural")


@app.template_filter("zone_name")
def zone_name(zone):
    """"Denver time (MDT)" for "America/Denver", as the Settings window says it."""
    city = (zone or "").rsplit("/", 1)[-1].replace("_", " ")
    short = deadlines.zone_label(zone)
    if not city or city == short:
        return short or city
    return f"{city} time ({short})" if short and short[0] not in "+-" and short != zone else f"{city} time"


# Usage totals for the owner's page. Teardown functions run last-registered
# first, so this one runs after the request's own connection is closed.
app.teardown_appcontext(usage.save)
app.teardown_appcontext(db.close_conn)
app.before_request(usage.start)
app.after_request(usage.measure)

app.register_blueprint(teach.bp)
app.register_blueprint(student.bp)
app.register_blueprint(owner.bp)


# ---------------------------------------------------------------------------
# Every request
# ---------------------------------------------------------------------------
def missing_settings():
    """Settings a real deployment can't run without. Locally there are
    safe fallbacks (a SQLite file, a throwaway key), so nothing is fatal."""
    if not settings.IS_VERCEL:
        return []
    return [name for name in ("SECRET_KEY", "DATABASE_URL") if not getattr(settings, name)]


@app.before_request
def before_every_request():
    if request.endpoint == "static":
        return None
    missing = missing_settings()
    if missing:
        return render_template("setup_needed.html", missing=missing), 503
    if request.method == "POST" and not auth.csrf_ok():
        if request.is_json:
            return jsonify(ok=False, error="This page expired. Refresh it and try again."), 400
        return render_template(
            "error.html", code=400, title="That form expired",
            message="Go back, refresh the page, and try again.",
        ), 400
    return None


CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data:",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
])


@app.after_request
def security_headers(response):
    # No inline scripts anywhere, so injected markup can't run code.
    response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    # Share links are the keys to a sign-up sheet; don't leak them in Referer.
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.endpoint not in ("static", "site_font"):
        # Pages hold names and rankings: never store them in any cache.
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def _preload_fonts():
    """The uploaded font files nearly every page uses, so the browser starts
    fetching them before it reads the stylesheet: the page waits for its
    fonts before showing (see app.js), and this keeps that wait short."""
    uploaded = owner.uploaded_fonts()
    heading = "roslindale-variable.woff2" if "roslindale-variable.woff2" in uploaded else (
        "roslindale-display-condensed-regular.woff2")
    return [url_for("site_font", name=name) for name in (heading, "yalenew-roman.woff2") if name in uploaded]


def _has_sheets(instructor):
    """Is there anything on this instructor's "My sheets" page: a sheet
    (archived or not), or a deleted one they can still bring back? Until
    there is, the top bar greys "My sheets" out and says why."""
    if not instructor:
        return False
    try:
        return bool(db.scalar("SELECT 1 FROM sheets WHERE owner_id = :me LIMIT 1", me=instructor["id"])
                    or sheets.deleted_sheets(instructor["id"]))
    except Exception:  # noqa: BLE001 - a plain link, as before
        return True


def _dock_share(instructor):
    """On one of the instructor's own sheet pages: its link for students, for
    the top bar's copy button."""
    sid = (request.view_args or {}).get("sid") if request.blueprint == "teach" else None
    if not (instructor and sid):
        return None
    try:
        sheet = sheets.get_sheet(sid)
    except Exception:  # noqa: BLE001 - no button, then
        return None
    if not sheet or sheet["owner_id"] != instructor["id"]:
        return None
    return url_for("student.signin", sid=sheet["id"], _external=True)


@app.context_processor
def template_globals():
    try:
        instructor = auth.current_instructor()
    except Exception:  # a broken database shouldn't also break the error page
        instructor = None
    return {
        "app_name": settings.APP_NAME,
        "instructor": instructor,
        "is_owner": auth.is_owner(instructor),
        "has_sheets": _has_sheets(instructor),
        "csrf_token": auth.csrf_token,
        "nav_sheet": g.get("sheet"),
        "nav_student": g.get("student"),
        "dock_share": _dock_share(instructor),
        "contact_email": settings.CONTACT_EMAIL,
        "source_url": settings.SOURCE_URL,
        "built_by": "" if settings.BUILT_BY == "-" else settings.BUILT_BY,
        "built_by_email": settings.BUILT_BY_EMAIL,
        "built_by_url": settings.BUILT_BY_URL,
        "code_sender": signin.code_sender(),
        "preload_fonts": _preload_fonts(),
        "codes_by_workos": signin.email_mode() == "workos",
        "helper_ids": ",".join(settings.CANVAS_HELPER_IDS),
        "helper_hosts": ",".join(settings.CANVAS_HELPER_HOSTS + ([settings.CANVAS_DEV_HOST] if settings.CANVAS_DEV_HOST else [])),
        "canvas_connect": canvas_oauth.ready(),
        "canvas_connect_host": settings.CANVAS_OAUTH_HOST,
        "helper_store_url": settings.CANVAS_HELPER_STORE_URL,
        "mail_by_gmail": signin.smtp_ready(),
        # On the general pages a student might wander to (Privacy, How it
        # works), a way back to their class.
        "back_to_class": next(iter(session.get("students") or {}), None)
        if request.endpoint in ("how_it_works", "privacy") else None,
    }


@app.errorhandler(HTTPException)
def http_error(err):
    messages = {
        404: ("Page not found", "That page doesn't exist. If you followed a link, check that it was copied in full."),
        405: ("That didn't work", "Go back and try again."),
        413: ("That file is too big", "Uploads can be up to 2 MB — a class list is usually far smaller. Is it the right file?"),
    }
    title, message = messages.get(err.code, (err.name, err.description))
    found = re.match(r"^/(teach/s|c)/([a-z0-9]{8,16})/", request.path)
    if err.code == 405 and request.method == "GET" and found:
        # Reopening a page that only exists as the answer to a button (a
        # refresh, the back button, a bookmark): go to the sheet instead.
        if found.group(1) == "teach/s":
            return redirect(url_for("teach.sheet", sid=found.group(2)))
        return redirect(url_for("student.signin", sid=found.group(2)))
    if request.is_json:
        return jsonify(ok=False, error=message), err.code
    return render_template(
        "error.html", code=err.code, title=title, message=message, show_join=err.code == 404,
    ), err.code


@app.errorhandler(Exception)
def unexpected_error(err):
    db.rollback()
    db.record_error(request.method, f"{type(err).__name__}: {err}")
    app.logger.exception("Unhandled error on %s %s", request.method, request.path)
    message = "Something went wrong on our end. Your work up to the last save is safe — please try again."
    if request.is_json:
        return jsonify(ok=False, error=message), 500
    return render_template("error.html", code=500, title="Something went wrong", message=message), 500


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------
@app.route("/")
def landing():
    return render_template("landing.html", code=(request.args.get("code") or "")[:80])


# One paste is read at most this many ways (longest first), and each one
# looked up that isn't a class counts as a wrong try, so a page of made-up
# codes pasted at once is no faster than typing them one at a time.
CODE_READINGS = 3


def _code_readings(raw):
    """The ways of reading what a student pasted as a class's code: the code
    itself, the whole link (with or without anything after it), "Code:
    k7q2mx9pa3fh", or the code typed with spaces or dashes in it ("k7q2 mx9p
    a3fh"). Longest first, and only those shaped like a code."""
    text = (raw or "").strip().split("?")[0].split("#")[0]
    if "/c/" in text:
        text = text.split("/c/", 1)[1].split("/")[0]
    tokens = [t.lower() for t in re.findall(r"[A-Za-z0-9]+", text)]
    candidates = sorted(set(tokens + ["".join(tokens)]), key=lambda t: (-len(t), t))
    return [t for t in candidates if _code_shaped(t)][:CODE_READINGS]


def _code_shaped(token):
    """A class's code or short code could be this: the right length, only
    letters and digits a code uses, and (a short code) a digit. Anything
    else (/robots.txt, /wp-login.php) is a plain 404, counted against no
    one and never looked up."""
    if not token or not all(ch in ID_ALPHABET for ch in token):
        return False
    return len(token) in sheets.SHEET_ID_LENGTHS or (
        len(token) in sheets.SHORT_CODE_LENGTHS and any(ch.isdigit() for ch in token))


def find_code(raw, missed=None):
    """The sheet code in whatever a student pasted (see _code_readings). The
    first reading that's a real sheet (or a deleted one that can still come
    back) wins; "" if none is. `missed`, if given, gets every reading looked
    up that wasn't a class's, even ones tried before the one that was."""
    for token in _code_readings(raw):
        if len(token) in sheets.SHEET_ID_LENGTHS and (sheets.get_sheet(token) or _was_deleted(token)):
            return token
        if len(token) in sheets.SHORT_CODE_LENGTHS:
            sid = sheets.by_short_code(token)
            if sid:
                return sid
        if missed is not None:
            missed.append(token)
    return ""


# Codes that matched no class, typed in the "Have a code?" box or opened as
# example.com/<code>: about 30 an hour from one browser, and a much higher
# ceiling from one network address (a whole class on campus Wi-Fi mistyping
# a link never gets near it, and a script that drops its cookies can't get
# past it). Past either, no code is looked up at all, even a right one, or
# the limit would only hide which tries worked. The full link (/c/<code>)
# never comes through here, so it always works.
CODE_MISSES_PER_BROWSER = 30
CODE_MISSES_PER_NETWORK = 200


def _code_tries_from():
    """The two counters a wrong code counts against: this browser, and its
    network address (hashed, so the table holds no addresses)."""
    return ("b:" + signin.browser_id(), "n:" + signin.keyed_hash("ip|" + (signin.client_ip() or "unknown")))


def _code_misses_allowed():
    """Whether this browser and network may try another code this hour."""
    browser, network = _code_tries_from()
    since = iso(now() - timedelta(hours=1))
    count = lambda client: db.scalar(  # noqa: E731
        "SELECT COUNT(*) FROM auth_failures WHERE scope = 'join' AND client = :client AND at > :since",
        client=client, since=since,
    ) or 0
    return count(browser) < CODE_MISSES_PER_BROWSER and count(network) < CODE_MISSES_PER_NETWORK


def _record_code_misses(missed):
    """One row per wrong code for this browser, and one for its network."""
    if not missed:
        return
    at, clients = iso(), _code_tries_from()
    db.run_many(
        "INSERT INTO auth_failures (id, scope, subject, client, at) VALUES (:id, 'join', '', :client, :at)",
        [{"id": new_id(16), "client": client, "at": at} for _ in missed for client in clients],
    )
    db.commit()


@app.route("/join")
def join():
    """Students who were given a code instead of a link."""
    raw = (request.args.get("code") or "").strip()
    if raw and not _code_misses_allowed():
        flash("Too many codes that didn't match were tried from here. Use the link your instructor shared, "
              "or try again in an hour.", "error")
        return redirect(url_for("landing"))
    missed = []
    code = find_code(raw, missed)
    _record_code_misses(missed)
    if code:
        return redirect(url_for("student.signin", sid=code))
    if not raw:
        flash("Type the code your instructor gave you: the letters and numbers at the end of the class link.",
              "error")
    elif raw.isdigit() and len(raw) == 6:
        flash("That looks like the sign-in code from your email. Open your class's link first, then type "
              "the code there.", "error")
    else:
        flash(f"We couldn't find a sign-up with the code “{raw[:40]}”. Check it with your instructor. It's the "
              "letters and numbers at the end of the class link, like k7q2mx9pa3fh.", "error")
    return redirect(url_for("landing", code=raw[:80]))


@app.route("/<code>")
def short_link(code):
    """example.com/k7q2mx9pa3fh works as well as example.com/c/k7q2mx9pa3fh, and so
    does the class's short link, example.com/k7q2mx9p (or an older k7q2mx).
    Capitals, and a period or comma copied from the end of a sentence, are
    forgiven."""
    if not _code_readings(code):
        abort(404)
    if not _code_misses_allowed():
        return render_template(
            "error.html", code=429, title="Too many links that didn’t match",
            message="Too many links that didn’t match a class were opened from here in the last hour. Use the "
                    "full link your instructor shared, the one with /c/ in it, or try again in an hour.",
        ), 429
    missed = []
    sid = find_code(code, missed)
    _record_code_misses(missed)
    if sid:
        return redirect(url_for("student.signin", sid=sid))
    abort(404)


def _was_deleted(sid):
    """A sheet taken offline but not gone for good: its link should say so
    rather than claim the code is wrong."""
    return bool(db.scalar("SELECT 1 FROM snapshots WHERE sheet_id = :sid AND kind = 'deleted'", sid=sid))


@app.route("/fonts/<name>")
def site_font(name):
    """A web font the site's owner uploaded on /owner (licensed fonts stay
    out of the public code). Cached at the edge, so it's served from the
    database about once per deployment."""
    if name not in owner.SITE_FONTS:
        abort(404)
    found = db.row("SELECT content_type, data FROM site_assets WHERE name = :name", name=name)
    if not found:
        abort(404)
    response = app.response_class(base64.b64decode(found["data"]), mimetype=found["content_type"])
    response.headers["Cache-Control"] = "public, max-age=604800, s-maxage=31536000"
    return response


@app.route("/how-it-works")
def how_it_works():
    # A student signed in to a class: what its option means for them.
    sid = next(iter(session.get("students") or {}), None)
    try:
        sheet = sheets.get_sheet(sid) if sid else None
    except Exception:  # noqa: BLE001 - the general page, then
        sheet = None
    return render_template("how_it_works.html", algorithms=ALGORITHMS, examples=example_views(),
                           default_algorithm=DEFAULT_ALGORITHM, algorithm=sheet["algorithm"] if sheet else None)


@app.route("/invite", methods=["GET", "POST"])
def accept_invitation():
    """The button in an invitation WorkOS emailed (signin.send_invitations).
    Opening the link only shows a button, since email scanners open links
    too; pressing it signs the student in to their class (every class that
    sent this invitation), and the invitation is used up."""
    token = (request.values.get("invitation_token") or "")[:300]
    if request.method == "GET":
        return render_template("invite_accept.html", token=token, problem="" if token else "used")
    invitation = signin.find_invitation(token) if token else None
    if not invitation or invitation.get("state") != "pending":
        return render_template("invite_accept.html", token="", problem="used")
    email = (invitation.get("email") or "").lower()
    rows = db.rows("SELECT sheet_id FROM invitations WHERE id = :id", id=invitation["id"])
    signed = []
    for row in rows:
        sheet = sheets.get_sheet(row["sheet_id"])
        student = db.row(
            "SELECT name_key, display_name FROM roster WHERE sheet_id = :sid AND lower(email) = :email AND is_test = 0",
            sid=row["sheet_id"], email=email,
        ) if sheet and not sheet["owner_disabled"] else None
        if not student:
            continue
        auth.sign_in_student(sheet["id"], student["display_name"], student["name_key"], "code",
                             shared=request.form.get("shared") == "1")
        signin.clear_refusals(sheet["id"], student["name_key"])
        db.run("UPDATE sheets SET shared_at = :at WHERE id = :sid AND shared_at IS NULL", at=iso(), sid=sheet["id"])
        signed.append(sheet)
    db.run("DELETE FROM invitations WHERE id = :id", id=invitation["id"])
    db.commit()
    signin.revoke_invitation(invitation["id"])
    if len(signed) == 1:
        flash("You're signed in.", "success")
        return redirect(url_for("student.home", sid=signed[0]["id"]))
    return render_template("invite_accept.html", token="", classes=signed,
                           problem="" if signed else "test" if not rows else "gone")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/helper")
def helper():
    """What Scheduler Helper (the browser extension) is and does: its store
    page and the extension itself link here."""
    return render_template("helper.html")


@app.route("/healthz")
def healthz():
    try:
        db.scalar("SELECT 1")
        return jsonify(ok=True)
    except Exception:
        return jsonify(ok=False), 503


@app.route("/cron/daily")
def cron_daily():
    """Called once a day by Vercel Cron (see vercel.json), which sends
    "Authorization: Bearer <CRON_SECRET>". Nobody else can trigger it."""
    expected = f"Bearer {settings.CRON_SECRET}"
    if not settings.CRON_SECRET or not hmac.compare_digest(
        request.headers.get("Authorization", ""), expected
    ):
        return jsonify(ok=False), 401
    return jsonify(ok=True, **maintenance.run_daily())


@app.cli.command("init-db")
def init_db_command():
    """Create the tables (the app also does this on its own at first start)."""
    db.ensure_schema()
    print("Database is ready.")


if __name__ == "__main__":
    app.run(debug=True, port=5050)
