"""
The site owner's page, /owner.

Deliberately thin. It shows whether the site is healthy — counts, today's
email use, recent errors — and lets the owner switch off an account that's
being abused. It never lists instructors, sheets, or students, and there's
no route anywhere that lets the owner open someone else's sheet.
"""

import base64

from flask import Blueprint, flash, redirect, render_template, request, url_for

import db
import settings
import signin
import usage
from auth import owner_required
from util import iso, valid_email

bp = Blueprint("owner", __name__, url_prefix="/owner")

# The web fonts style.css asks for (as /fonts/<name>). Each falls back to a
# free look-alike until it's uploaded here.
SITE_FONTS = (
    "roslindale-variable.woff2",  # headings: every weight, italics and optical sizes in one file
    "roslindale-display-condensed-regular.woff2",  # stands in until that one is uploaded
    "yalenew-roman.woff2",  # text
    "yalenew-italic.woff2",
    "yalenew-bold.woff2",
    "yalenew-bolditalic.woff2",
    "oldstyle7-roman.woff2",  # subtitles and lead lines
)
MAX_FONT_BYTES = 600 * 1024


@bp.route("/")
@owner_required
def index():
    count = lambda sql: db.scalar(sql) or 0  # noqa: E731
    stats = {
        "instructors": count("SELECT COUNT(*) FROM instructors"),
        "sheets": count("SELECT COUNT(*) FROM sheets"),
        "students": count("SELECT COUNT(*) FROM roster WHERE is_test = 0"),
        "rankings": count("SELECT COUNT(*) FROM submissions"),
        "emails_today": signin.emails_sent_today(),
    }
    checks = [
        ("Database", "Postgres" if db.is_postgres() else "SQLite (local file)", db.is_postgres() or not settings.IS_VERCEL),
        ("Sign-in email", {"smtp": f"Sending from {settings.SMTP_FROM}", "dev": "Local dev mode (codes shown on screen)", "missing": "Not set up — nobody can sign in"}[signin.email_mode()], signin.email_mode() != "missing"),
        ("Daily job", "Set up — once a day it saves a copy of every changed sheet and keeps the database awake" if settings.CRON_SECRET else "CRON_SECRET isn't set, so daily backups and the keep-awake ping won't run", bool(settings.CRON_SECRET)),
        ("Sign-in key", "Set — it protects everyone's sign-in" if settings.SECRET_KEY else "Not set — fine on your own computer, but required on Vercel (see the README)", bool(settings.SECRET_KEY) or not settings.IS_VERCEL),
    ]
    errors = db.rows(
        "SELECT created_at, method, path, error FROM error_log ORDER BY created_at DESC LIMIT 25"
    )
    uploaded = {r["name"]: r["updated_at"] for r in db.rows("SELECT name, updated_at FROM site_assets")}
    return render_template(
        "owner.html", stats=stats, checks=checks, errors=errors,
        email_limit=settings.EMAIL_DAILY_LIMIT, domains=settings.INSTRUCTOR_EMAIL_DOMAINS,
        fonts=[(name, uploaded.get(name)) for name in SITE_FONTS],
        meters=usage.meters(), measured_since=usage.first_day(), daily_job=usage.last_daily_job(),
        alert_at=int(usage.ALERT_AT * 100),
    )


@bp.route("/fonts", methods=["POST"])
@owner_required
def upload_fonts():
    """Web font files for the site, uploaded here so they never sit in the
    public code. Only the names style.css asks for are accepted."""
    saved, refused = [], []
    for upload in request.files.getlist("fonts"):
        name = (upload.filename or "").rsplit("/", 1)[-1].strip().lower()
        data = upload.read(MAX_FONT_BYTES + 1)
        if name not in SITE_FONTS:
            refused.append(f"{name or 'a file'} (not one of the expected names)")
        elif len(data) > MAX_FONT_BYTES or not data.startswith(b"wOF2"):
            refused.append(f"{name} (not a .woff2 font)")
        else:
            db.run(
                "INSERT INTO site_assets (name, content_type, data, updated_at) VALUES (:n, 'font/woff2', :d, :at) "
                "ON CONFLICT (name) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
                n=name, d=base64.b64encode(data).decode("ascii"), at=iso(),
            )
            saved.append(name)
    db.commit()
    if saved:
        flash(f"Uploaded {len(saved)} font file{'s' if len(saved) != 1 else ''}. Pages use them as soon as they "
              "reload.", "success")
    if refused:
        flash("Skipped " + "; ".join(refused[:6]) + ".", "error")
    if not saved and not refused:
        flash("Choose the .woff2 font files to upload.", "error")
    return redirect(url_for("owner.index") + "#fonts")


@bp.route("/account", methods=["POST"])
@owner_required
def account():
    """Switch an instructor account off (or back on) by its email address.
    You have to already know the address — there's no list to browse."""
    email = (request.form.get("email") or "").strip().lower()
    disable = request.form.get("action") == "disable"
    if not valid_email(email):
        flash("Enter the account's full email address.", "error")
    elif email == settings.OWNER_EMAIL:
        flash("That's your own account.", "error")
    else:
        current = db.scalar("SELECT disabled FROM instructors WHERE email = :e", e=email)
        if current is None:
            flash(f"No instructor account uses {email}. Check the spelling — nothing was changed.", "error")
        elif bool(current) == disable:
            flash(f"{email} was already switched {'off' if disable else 'on'}.", "info")
        else:
            db.run("UPDATE instructors SET disabled = :d WHERE email = :e", d=int(disable), e=email)
            db.commit()
            flash(
                f"Switched off {email}: they can't sign in, and their students' links say the sign-up isn't "
                "available. Nothing was deleted." if disable else f"Switched {email} back on.",
                "success",
            )
    return redirect(url_for("owner.index"))


@bp.route("/errors/clear", methods=["POST"])
@owner_required
def clear_errors():
    db.run("DELETE FROM error_log")
    db.commit()
    flash("Cleared the error log.", "success")
    return redirect(url_for("owner.index"))
