"""
One-time sign-in codes, the email that carries them, and PINs.

Students and instructors sign in the same way: we email a 6-digit code and
they type it back, which proves they can read that inbox. There are no
passwords anywhere. A student with no email address on the class list
protects their name with a PIN they choose instead.

The code email is sent by WorkOS (its free "Magic Auth" service) when it's
set up: WorkOS makes the code and emails it, and the site checks it here
like any other. WorkOS keeps a record for each address it emails; the site
deletes it once that person signs in, or within a day. Without WorkOS, an
ordinary mailbox (Gmail) sends the site's own code emails, and each
student's also carries a one-click sign-in link, good for a day. Six digits have to expire fast; a long random link doesn't — so any
code email a student received (even one a classmate triggered by typing
their name) still gets them in later, and nobody can lock a student out by
using up the limits below.

Sending is rate-limited — per address, per network address, per class, and
site-wide per day — because a free email account is the scarcest resource
this site has, and the cheapest thing for someone to abuse. Whenever a limit
stops a new code but an earlier one still works, people are pointed at that
one rather than turned away. A student's first code of the day for a class
is never held back by the class's limits, so strangers can't use those up
to keep someone out.
"""

import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import timedelta
from email.utils import formataddr

from flask import current_app, g, has_app_context, request
from flask_mailman import EmailMessage

import db
import settings
import sitesecrets
from util import iso, minutes_until, new_id, now, parse_iso, plural

CODE_LENGTH = 6
CODE_EXPIRY_MINUTES = 30
LINK_EXPIRY_HOURS = 24
RESEND_COOLDOWN_SECONDS = 30
# Codes to one address per hour: per asking browser (so a stranger typing a
# student's name can't use up the student's own share), and in total.
PER_ADDRESS_PER_HOUR = 5
PER_ADDRESS_PER_HOUR_TOTAL = 15
# Per network address. A whole class on campus Wi-Fi can share one address,
# so students get plenty; instructors sign in far less often.
PER_IP_PER_HOUR = {"student": 150, "teach": 20}
# Emails a day held back so instructors can always get a sign-in code, even
# if students (or someone abusing a class link) used up everything else.
INSTRUCTOR_RESERVE = 10
# Different names one browser may ask codes for in an hour, per class, and
# codes per class per hour beyond its class size: one visitor with a class
# link can't email the whole list over and over.
CODE_NAMES_PER_BROWSER = 5
CLASS_CODES_PER_HOUR_EXTRA = 10
# And per class per day, so one class link can't use up every other
# class's share of the day's emails. Codes after a student's first of the
# day also stop while the site is down to its last third for the day,
# which stays for first codes — any class's.
CLASS_CODES_PER_DAY_MIN = 20
REPEAT_RESERVE_SHARE = 3
# Wrong code guesses: counted per browser (so a guesser can't cancel the
# code the real person is waiting for), with looser limits per name and per
# network address so nobody can just keep switching browsers.
CODE_TRIES_PER_BROWSER = 5  # per name, per 15 minutes
CODE_TRIES_PER_SUBJECT = 30  # per name, per hour, from anywhere
CODE_TRIES_PER_NETWORK = 60  # per class, per hour
CODE_LOCK_MINUTES = 15

INSTRUCTOR_SCOPE = "teach"


class SendRefused(Exception):
    """Raised with a message that's fine to show the person signing in.
    `reason` names the limit, for the instructor's list of students who
    couldn't get an email: site-day, class-day, class-hour, site-busy,
    address, browser, network, paused, failed, or setup."""

    def __init__(self, message, reason=""):
        super().__init__(message)
        self.reason = reason


@dataclass
class Issued:
    # "sent"; "recent": a code or link that still works went out already, so
    # nothing new was sent; "instructor": the instructor made a code for
    # this person, so nothing was sent and they should type that one.
    status: str
    dev_code: str = ""  # local development only, where codes are shown on screen
    note: str = ""  # for "recent": what to tell the person


@dataclass
class Check:
    status: str  # ok, wrong, expired, locked (this browser), paused (this name), length, missing
    record: dict = None
    tries_left: int = 0
    typed: int = 0  # digits typed, for the "length" message
    until: object = None  # for locked and paused: when it lifts, if known


def workos_key():
    """The WorkOS API key: the WORKOS_API_KEY setting if there is one, else
    the key the owner saved on /owner. "" when neither is set."""
    if settings.WORKOS_API_KEY:
        return settings.WORKOS_API_KEY
    if not has_app_context():
        return ""
    if "workos_key" not in g:
        try:
            g.workos_key = sitesecrets.load("workos_api_key")
        except Exception:  # noqa: BLE001 - no database yet: no saved key
            g.workos_key = ""
    return g.workos_key


def email_mode():
    """'workos' when WorkOS emails the codes; 'smtp' when an ordinary mailbox
    (Gmail) does; 'dev' locally without either, where codes are shown on
    screen; 'missing' on a deployment that forgot both, where sign-in has to
    fail loudly instead."""
    if workos_key():
        return "workos"
    if settings.SMTP_HOST:
        return "smtp"
    return "missing" if settings.IS_VERCEL else "dev"


def codes_live():
    """Do sign-in codes really go out by email (so the limits, and the list
    of students who couldn't get one, apply)?"""
    return email_mode() in ("workos", "smtp")


def smtp_ready():
    """Can the site send email of its own (backups, the owner's alerts)?"""
    return bool(settings.SMTP_HOST)


def code_sender():
    """The address sign-in codes come from, so people know what to look for."""
    mode = email_mode()
    return settings.WORKOS_SENDER if mode == "workos" else settings.SMTP_FROM if mode == "smtp" else ""


def email_daily_limit():
    """Sign-in emails allowed per rolling day across the site: the
    EMAIL_DAILY_LIMIT setting if it's set, otherwise 1000 with WorkOS and 90
    with Gmail alone (which refuses somewhere past 100-500 a day)."""
    return settings.EMAIL_DAILY_LIMIT or (1000 if email_mode() == "workos" else 90)


def codes_have_links():
    """Do code emails carry the site's one-click sign-in link? Only the
    site's own emails do; WorkOS's carry just the code."""
    return email_mode() == "smtp"


def clock(when):
    """“11:52 AM (in about 13 minutes)”. The clock time is a marker the
    local_times filter (app.py) shows in the reader's own time zone."""
    minutes = minutes_until(when)
    wait = plural(round(minutes / 60), "hour") if minutes > 90 else plural(minutes, "minute")
    return f"[[at:{iso(when)}]] (in about {wait})"


def at_time(when):
    return "at " + clock(when)


def keyed_hash(value):
    """HMAC with the app's secret key. Used for codes and PINs (so a
    database leak doesn't reveal them — six digits are trivial to
    brute-force from a plain hash) and for rate-limit rows (so the logs
    hold no addresses)."""
    key = (current_app.secret_key or "").encode()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


def hash_code(scope, subject, code):
    return keyed_hash(f"code|{scope}|{subject}|{code}")


def client_ip():
    """The address to rate-limit by. On Vercel, ProxyFix (app.py) has
    already replaced remote_addr with the one hop Vercel's edge vouches for;
    raw X-Forwarded-For / X-Real-IP headers are never read directly here,
    since a client can put anything in them."""
    return request.remote_addr or ""


def browser_id():
    """This browser, as a hash of its session's random token."""
    from auth import csrf_token

    return keyed_hash("browser|" + csrf_token())


def emails_sent_today():
    return db.scalar(
        "SELECT COUNT(*) FROM email_log WHERE sent_at > :since",
        since=iso(now() - timedelta(days=1)),
    ) or 0


def daily_room(kind):
    """How many more emails of this kind may go out today. Everything but
    instructor sign-in codes stops short of the reserve."""
    limit = email_daily_limit()
    if kind != "teach":
        limit -= min(INSTRUCTOR_RESERVE, limit // 5)
    return limit - emails_sent_today()


def _ask_instructor(kind):
    return " — or ask your instructor for a sign-in code." if kind == "student" else "."


def _check_limits(email, kind):
    if daily_room(kind) <= 0:
        raise SendRefused(
            "This site has sent as many sign-in emails as it can for today, so no new one can go out until "
            "tomorrow." + (" Your instructor can give you a sign-in code instead." if kind == "student" else ""),
            reason="site-day",
        )
    hour_ago = iso(now() - timedelta(hours=1))
    address = keyed_hash(email.lower())
    mine = db.rows(
        "SELECT at FROM auth_failures WHERE scope = :scope AND subject = :subject AND client = :client "
        "AND at > :since ORDER BY at",
        scope=f"addr:{kind}", subject=address, client="b:" + browser_id(), since=hour_ago,
    )
    if len(mine) >= PER_ADDRESS_PER_HOUR:
        until = parse_iso(mine[0]["at"]) + timedelta(hours=1)
        raise SendRefused(
            f"You've asked for {len(mine)} sign-in codes in the last hour, the most we send. You can ask for "
            f"a new one {at_time(until)}" + _ask_instructor(kind),
            reason="address",
        )
    everyone = db.rows(
        "SELECT sent_at FROM email_log WHERE recipient = :r AND kind = :kind AND sent_at > :since ORDER BY sent_at",
        r=address, kind=kind, since=hour_ago,
    )
    if len(everyone) >= PER_ADDRESS_PER_HOUR_TOTAL:
        until = parse_iso(everyone[0]["sent_at"]) + timedelta(hours=1)
        raise SendRefused(
            "A lot of sign-in codes have gone to that address in the last hour, from different places. You can "
            f"ask for a new one {at_time(until)}" + _ask_instructor(kind),
            reason="address",
        )
    ip = client_ip()
    if ip:
        per_ip = db.scalar(
            "SELECT COUNT(*) FROM email_log WHERE client_ip = :ip AND kind = :kind AND sent_at > :since",
            ip=keyed_hash(ip), kind=kind, since=hour_ago,
        ) or 0
        if per_ip >= PER_IP_PER_HOUR.get(kind, 20):
            raise SendRefused(
                "A lot of sign-in emails have gone out from this network in the last hour. Please try again in "
                "a little while" + _ask_instructor(kind),
                reason="network",
            )


def log_email(email, kind):
    ip = client_ip()
    at = iso()
    db.run(
        "INSERT INTO email_log (id, sent_at, recipient, client_ip, kind) "
        "VALUES (:id, :at, :r, :ip, :kind)",
        id=new_id(16), at=at, r=keyed_hash(email.lower()),
        ip=keyed_hash(ip) if ip else "", kind=kind,
    )
    if kind in ("student", "teach"):
        db.run(
            "INSERT INTO auth_failures (id, scope, subject, client, at) VALUES (:id, :scope, :subject, :client, :at)",
            id=new_id(16), scope=f"addr:{kind}", subject=keyed_hash(email.lower()), client="b:" + browser_id(), at=at,
        )


def from_header():
    return formataddr((settings.SMTP_FROM_NAME, settings.SMTP_FROM))


def send_email(to, subject, body, reply_to=None, attachments=()):
    """Send one plain-text email. Raises on failure."""
    message = EmailMessage(
        subject=subject, body=body, from_email=from_header(), to=[to],
        reply_to=[reply_to] if reply_to else None,
    )
    for filename, content, mimetype in attachments:
        message.attach(filename, content, mimetype)
    message.send()


# ---------------------------------------------------------------------------
# WorkOS
# ---------------------------------------------------------------------------
def _workos(method, path, body=None, timeout=10, key=None):
    """One call to the WorkOS API; its JSON answer. Raises on any failure
    (urllib's HTTPError for a 4xx or 5xx answer)."""
    ask = urllib.request.Request(
        settings.WORKOS_API_URL.rstrip("/") + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key or workos_key()}", "Content-Type": "application/json",
                 "Accept": "application/json"},
    )
    with urllib.request.urlopen(ask, timeout=timeout) as answer:
        raw = answer.read()
    return json.loads(raw) if raw else {}


def _why(exc):
    """A failed call, for the error log: WorkOS explains errors in the body."""
    if isinstance(exc, urllib.error.HTTPError):
        try:
            return f"HTTP {exc.code}: {exc.read()[:300].decode('utf-8', 'replace')}"
        except Exception:  # noqa: BLE001
            return f"HTTP {exc.code}"
    return repr(exc)


def workos_check_key(key):
    """Does WorkOS accept this API key? (ok, what to tell the owner). Only
    reads: lists at most one user."""
    if not key.startswith("sk_"):
        return False, "That isn't a WorkOS secret key: those start with “sk_live_” (or “sk_test_”)."
    try:
        _workos("GET", "/user_management/users?limit=1", timeout=8, key=key)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return False, "WorkOS didn't accept that key. Copy the secret key again from API Keys in its dashboard."
        return False, f"WorkOS answered with an error ({_why(exc)[:120]}). Try again in a minute."
    except Exception as exc:  # noqa: BLE001
        return False, f"Couldn't reach WorkOS just now ({exc!r:.80}). Try again in a minute."
    return True, ""


def workos_send_code(email):
    """Have WorkOS email a fresh 6-digit code to this address (Magic Auth).
    Returns its answer: the code, when it expires, and the id of the record
    WorkOS keeps for the address. Raises unless a usable code came back."""
    made = _workos("POST", "/user_management/magic_auth", {"email": email})
    code = made.get("code")
    if not (isinstance(code, str) and code.isdigit() and len(code) == CODE_LENGTH and made.get("expires_at")):
        raise ValueError("WorkOS answered without a usable code")
    return made


def _remember_remote(user_id, email):
    """Note WorkOS's record for an address, to delete it later. Not committed here."""
    if user_id:
        db.run(
            "INSERT INTO remote_users (user_id, email_hash, created_at) VALUES (:user_id, :hash, :at) "
            "ON CONFLICT (user_id) DO UPDATE SET email_hash = excluded.email_hash, created_at = excluded.created_at",
            user_id=user_id, hash=keyed_hash(email.lower()), at=iso(),
        )


def _forget_one(user_id):
    """Delete WorkOS's record of a user. True when it's gone (or was already)."""
    try:
        _workos("DELETE", f"/user_management/users/{urllib.parse.quote(user_id, safe='')}", timeout=6)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            return False
    except Exception:  # noqa: BLE001 - the daily job tries again
        return False
    db.run("DELETE FROM remote_users WHERE user_id = :user_id", user_id=user_id)
    return True


def forget_remote(email):
    """Someone signed in: delete the record WorkOS kept for their address
    when it emailed them a code. Best effort; the daily job catches any left
    over. Commits."""
    if not workos_key() or not email:
        return
    for row in db.rows("SELECT user_id FROM remote_users WHERE email_hash = :hash", hash=keyed_hash(email.lower())):
        _forget_one(row["user_id"])
    db.commit()


def forget_remote_users(budget_seconds=20):
    """Daily: delete WorkOS's records of every address it emailed a code to
    more than an hour ago, whether or not anyone signed in. Stops after
    budget_seconds (the rest wait for tomorrow). Commits. Returns how many."""
    if not workos_key():
        return 0
    started, done = time.monotonic(), 0
    for row in db.rows("SELECT user_id FROM remote_users WHERE created_at < :cutoff ORDER BY created_at",
                       cutoff=iso(now() - timedelta(hours=1))):
        if time.monotonic() - started > budget_seconds:
            break
        done += _forget_one(row["user_id"])
    db.commit()
    return done


def pending_code(scope, subject):
    return db.row(
        "SELECT * FROM login_codes WHERE scope = :scope AND subject = :subject",
        scope=scope, subject=subject,
    )


def code_works(record):
    """Can this outstanding code still be used to sign in?"""
    return bool(record) and parse_iso(record["expires_at"]) > now()


def student_budget():
    """Emails a day for everything but instructors' sign-in codes."""
    limit = email_daily_limit()
    return limit - min(INSTRUCTOR_RESERVE, limit // 5)


def student_send_check(sheet_id, subject, class_size):
    """Raise SendRefused if a student code for `subject` shouldn't be
    emailed from this browser, for this class, right now. A student's first
    code of the day is held back only by the site's own limits."""
    hour_ago = iso(now() - timedelta(hours=1))
    scope = f"send:{sheet_id}"
    browser = "b:" + browser_id()
    asked = {r["subject"] for r in db.rows(
        "SELECT DISTINCT subject FROM auth_failures WHERE scope = :scope AND client = :client AND at > :since",
        scope=scope, client=browser, since=hour_ago,
    )}
    if subject not in asked and len(asked) >= CODE_NAMES_PER_BROWSER:
        raise SendRefused(
            "This browser has already asked for sign-in codes for several different names in the last hour. "
            "If you're helping classmates, have each of them sign in on their own phone or computer.",
            reason="browser",
        )
    today = db.rows(
        "SELECT subject, at FROM auth_failures WHERE scope = :scope AND at > :since ORDER BY at",
        scope=scope, since=iso(now() - timedelta(days=1)),
    )
    if not any(r["subject"] == subject for r in today):
        return
    hour = [r for r in today if r["at"] > hour_ago]
    per_hour = class_size + CLASS_CODES_PER_HOUR_EXTRA
    if len(hour) >= per_hour:
        until = parse_iso(hour[len(hour) - per_hour]["at"]) + timedelta(hours=1)
        raise SendRefused(
            "A lot of sign-in emails have gone out for this class in the last hour. You can ask for a new code "
            f"{at_time(until)} — or ask your instructor for a sign-in code.",
            reason="class-hour",
        )
    per_day = max(CLASS_CODES_PER_DAY_MIN, 2 * class_size)
    if len(today) >= per_day:
        until = parse_iso(today[len(today) - per_day]["at"]) + timedelta(days=1)
        raise SendRefused(
            f"This class has used its sign-in emails for today. More can go out {at_time(until)} — or ask "
            "your instructor for a sign-in code.",
            reason="class-day",
        )
    if daily_room("student") <= student_budget() // REPEAT_RESERVE_SHARE:
        raise SendRefused(
            "This site is running low on sign-in emails for today, so the ones left are kept for students who "
            "haven't had a code yet. Ask your instructor for a sign-in code.",
            reason="site-busy",
        )


def record_refusal(sheet_id, subject, reason):
    """A student who couldn't get a code, and why — for the instructor's
    list of students who couldn't get a sign-in email."""
    db.run(
        "INSERT INTO auth_failures (id, scope, subject, client, at) VALUES (:id, :scope, :subject, :client, :at)",
        id=new_id(16), scope=f"refused:{sheet_id}", subject=subject, client=f"r:{reason or 'other'}", at=iso(),
    )
    db.commit()


def refusals_today(sheet_id):
    """{name_key: (reason, at)}: students refused a sign-in email in the last
    day who haven't signed in since — the newest refusal for each."""
    found = {}
    for r in db.rows(
        "SELECT subject, client, at FROM auth_failures WHERE scope = :scope AND at > :since ORDER BY at",
        scope=f"refused:{sheet_id}", since=iso(now() - timedelta(days=1)),
    ):
        if r["subject"]:
            found[r["subject"]] = (r["client"][2:], r["at"])
    return found


def clear_refusals(sheet_id, subject):
    """They're in now. Not committed here."""
    db.run("DELETE FROM auth_failures WHERE scope = :scope AND subject = :subject",
           scope=f"refused:{sheet_id}", subject=subject)


def record_student_send(sheet_id, subject):
    db.run(
        "INSERT INTO auth_failures (id, scope, subject, client, at) VALUES (:id, :scope, :subject, :client, :at)",
        id=new_id(16), scope=f"send:{sheet_id}", subject=subject, client="b:" + browser_id(), at=iso(),
    )


# ---------------------------------------------------------------------------
# One-click sign-in links
# ---------------------------------------------------------------------------
def _link_hash(token):
    return keyed_hash("link|" + token)


def new_link(scope, subject, email, from_instructor=False):
    """A fresh link token for one email — the site's, or (`from_instructor`)
    one the instructor sends from their own account. Not committed here."""
    token = secrets.token_urlsafe(24)
    at = now()
    db.run(
        "INSERT INTO login_links (token_hash, scope, subject, email, created_at, expires_at, from_instructor) "
        "VALUES (:hash, :scope, :subject, :email, :at, :expires, :mine)",
        hash=_link_hash(token), scope=scope, subject=subject, email=email, at=iso(at),
        expires=iso(at + timedelta(hours=LINK_EXPIRY_HOURS)), mine=1 if from_instructor else 0,
    )
    return token


def find_link(scope, token):
    """The unused, unexpired link with this token, for this sheet — or None."""
    if not token or len(token) > 100:
        return None
    found = db.row(
        "SELECT * FROM login_links WHERE token_hash = :hash AND scope = :scope",
        hash=_link_hash(token), scope=scope,
    )
    return found if found and parse_iso(found["expires_at"]) > now() else None


def use_link(found):
    """Links work once. Not committed here."""
    db.run("DELETE FROM login_links WHERE token_hash = :hash", hash=found["token_hash"])


def newest_link(scope, subject, email):
    """The newest unexpired link emailed to this person (its created_at and
    from_instructor), or None."""
    return db.row(
        "SELECT created_at, from_instructor FROM login_links WHERE scope = :scope AND subject = :subject "
        "AND email = :email AND expires_at > :now ORDER BY created_at DESC LIMIT 1",
        scope=scope, subject=subject, email=email, now=iso(),
    )


def instructor_links(scope):
    """{subject: when}: the newest unused sign-in link the instructor made
    for each student, for the list of students who couldn't get an email."""
    return {r["subject"]: r["made"] for r in db.rows(
        "SELECT subject, MAX(created_at) AS made FROM login_links WHERE scope = :scope AND from_instructor = 1 "
        "AND expires_at > :now GROUP BY subject",
        scope=scope, now=iso(),
    )}


def sending_trouble():
    """Why student sign-in emails can't go out right now: "used-up" (the
    site's emails for today are gone) or "failing" (the last try failed,
    and nothing has gone out since) — or "" when they can."""
    if not codes_live():
        return ""
    if daily_room("student") <= 0:
        return "used-up"
    failed = db.scalar(
        "SELECT MAX(created_at) FROM error_log WHERE method = 'EMAIL' AND error LIKE 'Sending a sign-in code%' "
        "AND created_at > :since",
        since=iso(now() - timedelta(hours=1)),
    )
    if failed and failed > (db.scalar("SELECT MAX(sent_at) FROM email_log") or ""):
        return "failing"
    return ""


def issue_code(*, scope, subject, display_name, email, email_subject, email_body, kind, reply_to=None,
               precheck=None, on_sent=None, link_url=None, force_email=False):
    """Email a fresh code for this sign-in — unless one that still works went
    out moments ago, or a limit stops a new one while an earlier code or
    link still works, in which case the person is pointed at that one
    (status "recent"). If the instructor made a code for this person that
    still works, nothing is sent (status "instructor") unless `force_email`.

    email_subject and email_body may contain "{code}", and the body "{link}",
    filled in from `link_url(token)` with a one-click sign-in link. `precheck`
    may raise SendRefused to stop a send (it's only called when a send would
    really happen); `on_sent` runs after one. Raises SendRefused with a
    friendly message when it can't send and nothing earlier works. Commits.
    """
    mode = email_mode()
    if mode == "missing":
        raise SendRefused(
            "Sign-in emails aren't set up on this site yet. If you run it, add WORKOS_API_KEY (or the SMTP "
            "settings) as the README describes.",
            reason="setup",
        )

    paused = _paused_until(scope, subject)
    if paused:
        raise SendRefused(
            f"Signing in as {display_name} is paused until {clock(paused)}, because many wrong codes "
            "were tried. " + ("Ask your instructor to unlock it." if scope != INSTRUCTOR_SCOPE else "Please try again then."),
            reason="paused",
        )
    existing = pending_code(scope, subject)
    working = existing if code_works(existing) and existing["email"] == email else None
    if working and working["from_instructor"] and not force_email:
        return Issued("instructor")
    locked = _browser_locked_until(scope, subject)
    if working and not locked:
        elapsed = (now() - parse_iso(working["last_sent_at"])).total_seconds()
        if elapsed < RESEND_COOLDOWN_SECONDS:
            return Issued("recent", note=(
                "We emailed a code a moment ago — use that one. It can take a minute to arrive "
                "(check your spam folder too)."
            ))
    try:
        if precheck:
            precheck()
        if mode in ("smtp", "workos"):
            _check_limits(email, kind)
    except SendRefused as refused:
        linked = newest_link(scope, subject, email) if link_url else None
        if not working and not linked:
            raise
        if working and working["from_instructor"]:
            return Issued("recent", note=f"No email was sent: {refused} Type the code your instructor gave you, or "
                                         "click the sign-in link in their email.")
        if locked and not linked:
            raise SendRefused(
                f"No new code can be sent right now: {refused} This browser had too many wrong tries, so it can try "
                f"a code again {at_time(locked)} — use the code in your newest email then."
                + (" Or ask your instructor for a sign-in code." if scope != INSTRUCTOR_SCOPE else ""),
                reason=refused.reason,
            ) from refused
        if linked:
            sender = "your instructor" if linked["from_instructor"] else code_sender() or "us"
            until = iso(parse_iso(linked["created_at"]) + timedelta(hours=LINK_EXPIRY_HOURS))
            return Issued("recent", note=(
                f"No new email was sent: {refused} But the sign-in link in your newest email from {sender} "
                f"still works (until [[at:{until}]]) — click it to sign in."
            ))
        return Issued("recent", note=(
            f"No new code was sent: {refused} The code we already sent still works until "
            f"[[at:{working['expires_at']}]]."
        ))

    sent_at = now()
    expires = None
    if mode == "workos":
        try:
            made = workos_send_code(email)
            code, expires = made["code"], parse_iso(made["expires_at"])
        except Exception as exc:
            db.record_error("EMAIL", f"Sending a sign-in code through WorkOS failed: {_why(exc)}")
            if not settings.SMTP_HOST:
                raise SendRefused("We couldn't send the email just now. Please try again in a minute.",
                                  reason="failed") from exc
            mode = "smtp"  # this once, the site's own mailbox sends it
        else:
            _remember_remote(made.get("user_id"), email)
    if mode != "workos":
        code = "".join(secrets.choice("0123456789") for _ in range(CODE_LENGTH))
    _store_code(scope, subject, display_name, email, code, sent_at, keep=working, from_instructor=False,
                expires=expires)

    # A fresh code gives this browser a fresh set of tries.
    db.run(
        "DELETE FROM auth_failures WHERE scope = :scope AND subject = :subject AND client = :client",
        scope=f"code:{scope}", subject=_code_subject(scope, subject), client="b:" + browser_id(),
    )
    if mode == "dev":
        if on_sent:
            on_sent()
        db.commit()
        print(f"[dev mode, no email sent] sign-in code for {email}: {code}", flush=True)
        return Issued("sent", dev_code=code)

    if mode == "smtp":
        link = link_url(new_link(scope, subject, email)) if link_url else ""
        try:
            # replace(), not format(): a subject can contain a sheet title, and
            # a title with braces in it would break str.format.
            send_email(email, email_subject.replace("{code}", code),
                       email_body.replace("{code}", code).replace("{link}", link), reply_to=reply_to)
        except Exception as exc:
            db.rollback()
            db.record_error("EMAIL", f"Sending a sign-in code failed: {exc!r}")
            raise SendRefused("We couldn't send the email just now. Please try again in a minute.",
                              reason="failed") from exc

    log_email(email, kind)
    if on_sent:
        on_sent()
    db.commit()
    return Issued("sent")


def _store_code(scope, subject, display_name, email, code, sent_at, keep, from_instructor, expires=None):
    db.run(
        """
        INSERT INTO login_codes (scope, subject, display_name, email, code_hash, expires_at,
            prev_hash, prev_expires_at, attempts, last_sent_at, from_instructor)
        VALUES (:scope, :subject, :name, :email, :hash, :expires, :prev, :prev_expires, 0, :sent, :mine)
        ON CONFLICT (scope, subject) DO UPDATE SET
            display_name = excluded.display_name,
            email = excluded.email,
            code_hash = excluded.code_hash,
            expires_at = excluded.expires_at,
            prev_hash = excluded.prev_hash,
            prev_expires_at = excluded.prev_expires_at,
            attempts = 0,
            last_sent_at = excluded.last_sent_at,
            from_instructor = excluded.from_instructor
        """,
        scope=scope, subject=subject, name=display_name, email=email,
        hash=hash_code(scope, subject, code),
        expires=iso(expires or sent_at + timedelta(minutes=CODE_EXPIRY_MINUTES)),
        # The code before this one keeps working until it expires, so an
        # email that arrives late (or out of order) never makes a right
        # code fail.
        prev=keep["code_hash"] if keep else "",
        prev_expires=keep["expires_at"] if keep else None,
        sent=iso(sent_at), mine=1 if from_instructor else 0,
    )


def _paused_until(scope, subject):
    """If this name (or instructor address) is paused after too many wrong
    codes from anywhere: when that lifts; else None."""
    since = iso(now() - timedelta(hours=1))
    rows = db.rows(
        "SELECT at FROM auth_failures WHERE scope = :scope AND subject = :subject AND client LIKE 'b:%' "
        "AND at > :since ORDER BY at",
        scope=f"code:{scope}", subject=_code_subject(scope, subject), since=since,
    )
    if len(rows) < CODE_TRIES_PER_SUBJECT:
        return None
    return parse_iso(rows[-CODE_TRIES_PER_SUBJECT]["at"]) + timedelta(hours=1)


def _browser_locked_until(scope, subject):
    since = iso(now() - timedelta(minutes=CODE_LOCK_MINUTES))
    rows = db.rows(
        "SELECT at FROM auth_failures WHERE scope = :scope AND subject = :subject AND client = :client "
        "AND at > :since ORDER BY at",
        scope=f"code:{scope}", subject=_code_subject(scope, subject), client="b:" + browser_id(), since=since,
    )
    if len(rows) < CODE_TRIES_PER_BROWSER:
        return None
    return parse_iso(rows[-CODE_TRIES_PER_BROWSER]["at"]) + timedelta(minutes=CODE_LOCK_MINUTES)


def issue_code_for_instructor_handoff(sheet_id, row):
    """The backup for a student the site's email can't reach — its emails
    ran out or failed, a typo in the address, a slow inbox: a one-click
    sign-in link the instructor emails from their own account (it works
    once, for LINK_EXPIRY_HOURS), and a code they can read out instead. The
    code works like an emailed one; until it expires, signing in as that
    student asks for it instead of sending an email. Returns (code,
    expires_at, link token). Commits."""
    code = "".join(secrets.choice("0123456789") for _ in range(CODE_LENGTH))
    existing = pending_code(sheet_id, row["name_key"])
    at = now()
    _store_code(sheet_id, row["name_key"], row["display_name"], row["email"] or "", code, at,
                keep=existing if code_works(existing) else None, from_instructor=True)
    db.run(
        "DELETE FROM auth_failures WHERE scope IN (:code, :pin) AND subject = :key",
        code=f"code:{sheet_id}", pin=f"pin:{sheet_id}", key=row["name_key"],
    )
    token = new_link(sheet_id, row["name_key"], row["email"] or "", from_instructor=True)
    db.commit()
    return code, iso(at + timedelta(minutes=CODE_EXPIRY_MINUTES)), token


def code_for_other_class(scope, email, entered):
    """If a typed code belongs to the same person's sign-in for another
    class, that class's sheet id (so we can say which email to use)."""
    digits = _code_digits(entered)
    if len(digits) != CODE_LENGTH or not email:
        return None
    for r in db.rows(
        "SELECT * FROM login_codes WHERE email = :email AND scope <> :scope AND scope <> :teach",
        email=email, scope=scope, teach=INSTRUCTOR_SCOPE,
    ):
        typed = hash_code(r["scope"], r["subject"], digits)
        if code_works(r) and (hmac.compare_digest(typed, r["code_hash"]) or (
                r["prev_hash"] and hmac.compare_digest(typed, r["prev_hash"]))):
            return r["scope"]
    return None


def _code_digits(entered):
    """Digits from what was typed, reading the letters people mix up with
    them (l, I → 1; O → 0)."""
    text = (entered or "").translate(str.maketrans({"l": "1", "I": "1", "i": "1", "O": "0", "o": "0"}))
    return "".join(ch for ch in text if ch.isdigit())


def _code_subject(scope, subject):
    """How a sign-in shows up in the guess counters: a student's name key
    (so the instructor can unlock it), an instructor's address only hashed."""
    return keyed_hash("teach|" + subject) if scope == INSTRUCTOR_SCOPE else subject


def check_code(scope, subject, entered):
    """Check a typed code. Wrong guesses count against the browser making
    them; a typo in the length doesn't count. Commits."""
    record = pending_code(scope, subject)
    if not record:
        return Check("missing")
    digits = _code_digits(entered)
    if len(digits) != CODE_LENGTH:
        return Check("length", record, typed=len(digits))
    counter_scope = f"code:{scope}"
    who = _code_subject(scope, subject)
    browser = "b:" + browser_id()
    network = "n:" + keyed_hash("ip|" + (client_ip() or "unknown"))
    hour_ago = iso(now() - timedelta(hours=1))
    mine = _failures(counter_scope, iso(now() - timedelta(minutes=CODE_LOCK_MINUTES)), subject=who, client=browser)
    if _failures(counter_scope, hour_ago, subject=who, browsers_only=True) >= CODE_TRIES_PER_SUBJECT:
        return Check("paused", record, until=_paused_until(scope, subject))
    if mine >= CODE_TRIES_PER_BROWSER:
        return Check("locked", record, until=_browser_locked_until(scope, subject))
    if _failures(counter_scope, hour_ago, client=network) >= CODE_TRIES_PER_NETWORK:
        return Check("locked", record)

    current_ok = parse_iso(record["expires_at"]) > now()
    previous_ok = bool(record["prev_hash"]) and bool(record["prev_expires_at"]) and (
        parse_iso(record["prev_expires_at"]) > now()
    )
    typed_hash = hash_code(scope, subject, digits)
    if (current_ok and hmac.compare_digest(typed_hash, record["code_hash"])) or (
        previous_ok and hmac.compare_digest(typed_hash, record["prev_hash"])
    ):
        db.run("DELETE FROM login_codes WHERE scope = :scope AND subject = :subject", scope=scope, subject=subject)
        db.run(
            "DELETE FROM auth_failures WHERE scope = :scope AND subject = :subject AND client = :client",
            scope=counter_scope, subject=who, client=browser,
        )
        db.commit()
        forget_remote(record["email"])
        return Check("ok", record)
    if not current_ok:
        return Check("expired", record)

    at = iso()
    db.run_many(
        "INSERT INTO auth_failures (id, scope, subject, client, at) VALUES (:id, :scope, :subject, :client, :at)",
        [
            {"id": new_id(16), "scope": counter_scope, "subject": who, "client": browser, "at": at},
            {"id": new_id(16), "scope": counter_scope, "subject": who, "client": network, "at": at},
        ],
    )
    db.commit()
    left = CODE_TRIES_PER_BROWSER - (mine + 1)
    if left <= 0:
        return Check("locked", record, until=_browser_locked_until(scope, subject))
    return Check("wrong", record, tries_left=left)


def check_message(check, for_student=True):
    if check.status == "expired":
        return (f"That code has expired — codes work for {CODE_EXPIRY_MINUTES} minutes. Press “Send a new code” "
                "below." + (" (The sign-in link in that email works longer — try clicking it.)" if for_student else ""))
    if check.status == "locked":
        when = f"until {clock(check.until)}" if check.until else "for a little while"
        return (
            f"Too many wrong tries from this browser, so it can't try a code again {when}. "
            "Or press “Send a new code” now — a new code comes with new tries."
            + (" Your instructor can also give you a sign-in code." if for_student else "")
        )
    if check.status == "paused":
        when = f"until {clock(check.until)}" if check.until else "for up to an hour"
        return (
            f"Signing in with this name is paused {when}, because too many wrong codes were tried. "
            + ("Ask your instructor to unlock it." if for_student else "Please try again then.")
        )
    if check.status == "length":
        if not check.typed:
            return f"Type the {CODE_LENGTH}-digit code from the email."
        return f"Codes have {CODE_LENGTH} digits — you typed {check.typed}. Check the email and try again."
    if check.status == "wrong":
        message = "That code isn't right. Make sure it's from the newest email"
        if check.tries_left <= 3:
            message += f" — {plural(check.tries_left, 'try', 'tries')} left before you'll need a new code"
        return message + "."
    return ""


# ---------------------------------------------------------------------------
# PINs, for students who sign in by name alone
# ---------------------------------------------------------------------------
# A student with no email on the class list (or not on it at all) can't get
# a code. The first time a name is used, its owner picks a PIN; after that,
# signing in as that name anywhere new takes the PIN, so a classmate who
# knows your name can't read or change your ranking. The instructor can
# reset a forgotten PIN.
#
# Wrong guesses are counted per browser, so someone guessing locks out
# themselves rather than the real student — with looser limits per name and
# per network address so nobody can just keep switching browsers.
PIN_MIN_DIGITS = 4
PIN_MAX_DIGITS = 8
PIN_LOCK_MINUTES = 15
PIN_TRIES_PER_BROWSER = 5  # per name, per PIN_LOCK_MINUTES
PIN_TRIES_PER_NAME = 20  # per name per hour, from anywhere
PIN_TRIES_PER_NETWORK = 50  # per sheet per hour, from one network address
COMMON_PINS = {"1212", "1122", "1313", "2580", "0852", "1004", "2000", "6969", "1010", "0101", "5683", "4321"}


@dataclass
class PinCheck:
    status: str  # ok, wrong, locked, name-locked, empty, missing
    tries_left: int = 0


def pin_digits(value):
    return "".join(ch for ch in (value or "") if ch.isdigit())[:PIN_MAX_DIGITS + 4]


def weak_pin(digits):
    if len(set(digits)) == 1:
        return True  # 0000, 7777
    steps = {int(b) - int(a) for a, b in zip(digits, digits[1:])}
    if steps in ({1}, {-1}):
        return True  # 1234, 98765
    if len(digits) % 2 == 0 and digits == digits[:2] * (len(digits) // 2):
        return True  # 1212, 343434
    if len(digits) == 6 and digits[:3] == digits[3:]:
        return True  # 123123
    return digits in COMMON_PINS


def read_new_pin(value):
    """A PIN someone is choosing. Returns (pin, problem) — problem is ''
    when it's fine, or a sentence saying what to change."""
    raw = "".join((value or "").split())
    digits = pin_digits(raw)
    if not raw:
        return "", f"Type a PIN of {PIN_MIN_DIGITS} to {PIN_MAX_DIGITS} digits."
    if len(digits) != len(raw.replace("-", "")):
        return "", "Use only numbers (0–9) in your PIN."
    if not PIN_MIN_DIGITS <= len(digits) <= PIN_MAX_DIGITS:
        return "", f"Your PIN needs {PIN_MIN_DIGITS} to {PIN_MAX_DIGITS} digits — you typed {len(digits)}."
    if weak_pin(digits):
        return "", "That PIN is too easy to guess. Pick something less obvious than 1234 or 0000."
    return digits, ""


def _pin_hash(sheet_id, name_key, pin):
    return keyed_hash(f"pin|{sheet_id}|{name_key}|{pin}")


def get_pin(sheet_id, name_key):
    return db.row(
        "SELECT * FROM name_pins WHERE sheet_id = :sid AND name_key = :key", sid=sheet_id, key=name_key
    )


def set_pin(sheet_id, name_key, pin):
    """Claim a name with a PIN. False if someone else claimed it first.
    Commits."""
    created = db.run(
        "INSERT INTO name_pins (sheet_id, name_key, pin_hash, created_at) "
        "VALUES (:sid, :key, :hash, :at) ON CONFLICT (sheet_id, name_key) DO NOTHING",
        sid=sheet_id, key=name_key, hash=_pin_hash(sheet_id, name_key, pin), at=iso(),
    )
    if created:
        clear_pin_failures(sheet_id, name_key)
    db.commit()
    return created == 1


def clear_pin_failures(sheet_id, name_key):
    """Forget wrong guesses at one student's PIN and codes (after a reset,
    or to unlock their sign-in). Not committed here."""
    db.run(
        "DELETE FROM auth_failures WHERE scope IN (:pin, :code) AND subject = :key",
        pin=f"pin:{sheet_id}", code=f"code:{sheet_id}", key=name_key,
    )


def _failures(scope, since, subject=None, client=None, browsers_only=False):
    sql = "SELECT COUNT(*) FROM auth_failures WHERE scope = :scope AND at > :since"
    params = {"scope": scope, "since": since}
    if subject is not None:
        sql += " AND subject = :subject"
        params["subject"] = subject
    if client is not None:
        sql += " AND client = :client"
        params["client"] = client
    if browsers_only:
        sql += " AND client LIKE 'b:%'"
    return db.scalar(sql, **params) or 0


def check_pin(sheet_id, name_key, entered):
    """Check a typed PIN. Commits."""
    record = get_pin(sheet_id, name_key)
    if not record:
        return PinCheck("missing")
    digits = pin_digits(entered)
    if not digits:
        return PinCheck("empty")
    scope = f"pin:{sheet_id}"
    browser = "b:" + browser_id()
    network = "n:" + keyed_hash("ip|" + (client_ip() or "unknown"))
    hour_ago = iso(now() - timedelta(hours=1))
    mine = _failures(scope, iso(now() - timedelta(minutes=PIN_LOCK_MINUTES)), subject=name_key, client=browser)
    if _failures(scope, hour_ago, subject=name_key, browsers_only=True) >= PIN_TRIES_PER_NAME:
        return PinCheck("name-locked")
    if mine >= PIN_TRIES_PER_BROWSER or _failures(scope, hour_ago, client=network) >= PIN_TRIES_PER_NETWORK:
        return PinCheck("locked")

    if hmac.compare_digest(_pin_hash(sheet_id, name_key, digits), record["pin_hash"]):
        db.run(
            "DELETE FROM auth_failures WHERE scope = :scope AND subject = :key AND client = :client",
            scope=scope, key=name_key, client=browser,
        )
        db.commit()
        return PinCheck("ok")

    at = iso()
    db.run_many(
        "INSERT INTO auth_failures (id, scope, subject, client, at) VALUES (:id, :scope, :subject, :client, :at)",
        [
            {"id": new_id(16), "scope": scope, "subject": name_key, "client": browser, "at": at},
            {"id": new_id(16), "scope": scope, "subject": name_key, "client": network, "at": at},
        ],
    )
    db.commit()
    left = PIN_TRIES_PER_BROWSER - (mine + 1)
    return PinCheck("locked") if left <= 0 else PinCheck("wrong", tries_left=left)


def pin_message(check):
    if check.status == "wrong":
        if check.tries_left <= 3:
            return (
                f"That PIN isn't right — {plural(check.tries_left, 'try', 'tries')} left before a "
                f"{PIN_LOCK_MINUTES}-minute pause."
            )
        return "That PIN isn't right — try again."
    if check.status == "locked":
        return (
            f"Too many wrong tries. Wait {PIN_LOCK_MINUTES} minutes and try again — or, if you've "
            "forgotten your PIN, ask your instructor to reset it."
        )
    if check.status == "name-locked":
        return (
            "Too many wrong PINs have been tried for this name, so signing in with it is paused for up to an "
            "hour. Ask your instructor to unlock it."
        )
    if check.status == "empty":
        return "Type your PIN."
    return ""


def locked_names(sheet_id):
    """Names whose sign-in is paused for everyone (too many wrong PINs or
    codes from all over in the last hour), for the instructor's page."""
    since = iso(now() - timedelta(hours=1))
    locked = set()
    for scope, limit in ((f"pin:{sheet_id}", PIN_TRIES_PER_NAME), (f"code:{sheet_id}", CODE_TRIES_PER_SUBJECT)):
        found = db.rows(
            """
            SELECT subject FROM auth_failures
            WHERE scope = :scope AND client LIKE 'b:%' AND at > :since
            GROUP BY subject HAVING COUNT(*) >= :limit
            """,
            scope=scope, since=since, limit=limit,
        )
        locked |= {r["subject"] for r in found}
    return locked
