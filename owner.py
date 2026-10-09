"""
The site owner's page, /owner.

Deliberately thin. It shows whether the site is healthy — counts, today's
email use, recent errors — and lets the owner switch off an account that's
being abused. It never lists instructors, sheets, or students, and there's
no route anywhere that lets the owner open someone else's sheet.
"""

from flask import Blueprint, flash, redirect, render_template, request, url_for

import db
import settings
import signin
from auth import owner_required
from util import valid_email

bp = Blueprint("owner", __name__, url_prefix="/owner")


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
    return render_template(
        "owner.html", stats=stats, checks=checks, errors=errors,
        email_limit=settings.EMAIL_DAILY_LIMIT, domains=settings.INSTRUCTOR_EMAIL_DOMAINS,
    )


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
