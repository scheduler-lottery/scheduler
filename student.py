"""
Student pages for one sign-up sheet, all under /c/<sheet id>.

Students sign in per sheet: being signed in to one class's sheet says
nothing about any other. A student whose class-list entry has an email
address confirms with an emailed code; anyone else protects their name with
a PIN they choose.
"""

import json
from datetime import timedelta

from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, session, url_for

import auth
import compose
import db
import settings
import sheets
import signin
from matching_engine import compute_assignment
from roster import SAME_NAME_SUFFIX, base_key, match_name
from util import clean_text, fold, iso, mask_email, new_id, normalize_name, now, parse_iso, tidy_name

bp = Blueprint("student", __name__, url_prefix="/c/<sid>")

COMMENT_LIMIT = 1000
NAME_LOOKUPS_PER_10_MINUTES = 60
NEW_NAME = "__new__"  # the "No, my name really is …" button on the Did-you-mean page

UNAVAILABLE = (
    "This sign-up isn't available right now. Anything you already submitted isn't lost — check with "
    "your instructor."
)


def _clean_sid(sid):
    return "".join(ch for ch in (sid or "").lower() if ch.isalnum())


@bp.before_request
def load_sheet():
    asked = (request.view_args or {}).get("sid", "")
    sid = _clean_sid(asked)
    sheet = sheets.get_sheet(sid)
    if not sheet or sheet["owner_disabled"]:
        gone = sheet is not None or bool(
            db.scalar("SELECT 1 FROM snapshots WHERE sheet_id = :sid AND kind = 'deleted'", sid=sid)
        )
        if request.endpoint == "student.save":
            error = UNAVAILABLE if gone else "This sign-up doesn't exist any more."
            return jsonify(ok=False, retry=False, error=error), 410 if gone else 404
        if gone:
            return render_template(
                "error.html", code=410, title="This sign-up isn't available right now", message=UNAVAILABLE,
            ), 410
        return render_template(
            "error.html", code=404, title="We couldn't find that sign-up",
            message="Check that you copied the whole link your instructor shared (it ends in a 12-character "
                    "code), or type the code below.",
            show_join=True,
        ), 404
    if asked != sheet["id"] and request.method == "GET":
        # A link pasted with a trailing period or in capitals.
        return redirect(url_for(request.endpoint, **{**request.view_args, "sid": sheet["id"]}))
    g.sheet = sheet
    g.days = sheets.get_days(sheet["id"])
    g.student = auth.current_student(sheet)
    if g.get("signed_out_because") and request.endpoint != "student.save":
        flash(g.signed_out_because, "info")
    return None


def _here(endpoint, **kwargs):
    return url_for(endpoint, sid=g.sheet["id"], **kwargs)


def _need_student():
    return None if g.student else redirect(_here("student.signin"))


def _pending(name):
    pending = session.get(name)
    return pending if pending and pending.get("sid") == g.sheet["id"] else None


def _roster_rows():
    return db.rows(
        "SELECT name_key, display_name, email, is_test FROM roster WHERE sheet_id = :sid",
        sid=g.sheet["id"],
    )


def _roster_row(key):
    return db.row(
        "SELECT name_key, display_name, email, is_test FROM roster WHERE sheet_id = :sid AND name_key = :key",
        sid=g.sheet["id"], key=key,
    )


def _owner_is_viewing():
    instructor = auth.current_instructor()
    return bool(instructor and instructor["id"] == g.sheet["owner_id"])


# ---------------------------------------------------------------------------
# Signing in
# ---------------------------------------------------------------------------
@bp.route("", strict_slashes=False, endpoint="signin")
def signin_page(sid):
    pending = _pending("pending_student")
    if pending:
        if signin.code_works(signin.pending_code(g.sheet["id"], pending["key"])):
            return redirect(_here("student.verify"))
        session.pop("pending_student", None)
        if not pending.get("refused"):
            flash("Your last sign-in code expired. Type your name again to get a new one.", "info")
    if g.student:
        return redirect(_here("student.home"))
    counts = sheets.counts(g.sheet["id"])
    return render_template(
        "student_signin.html",
        sheet=g.sheet,
        counts=counts,
        not_ready=not g.sheet["allow_unlisted"] and counts["roster"] + counts["test_students"] == 0,
        published=_published(),
        typed=clean_text(request.args.get("name"), 120),
    )


def _code_email(row):
    title = g.sheet["title"]
    who = row["display_name"]
    subject = "{code} is your sign-in code for “" + title + "”"
    if row["is_test"]:
        subject = f"{who}: " + subject
    link = url_for("student.signin", sid=g.sheet["id"], _external=True)
    body = (
        f"Hi {who},\n\n"
        f"Your code to sign in to “{title}” is:\n\n"
        "    {code}\n\n"
        f"Type it on the sign-in page. It works for {signin.CODE_EXPIRY_MINUTES} minutes.\n\n"
        f"Or sign in with one click — this link works once, for the next {signin.LINK_EXPIRY_HOURS} hours, on "
        "whatever phone or computer you open it on:\n"
        "{link}\n\n"
        "Don't share the code or the link with anyone.\n\n"
        f"Your class's sign-up page: {link}\n\n"
        "Didn't ask for this? Someone may have typed your name by mistake. Nothing happens unless the "
        "code is typed in, so you can ignore this email — and if it keeps you from signing in, tell your "
        "instructor.\n\n"
        f"Questions? Just reply — replies go to your instructor ({g.sheet['owner_email']}).\n\n"
        f"— {settings.APP_NAME}, the sign-up site your instructor uses\n"
    )
    return subject, body


def _send_code(row, force_email=False):
    subject, body = _code_email(row)
    sid, key = g.sheet["id"], row["name_key"]
    class_size = db.scalar("SELECT COUNT(*) FROM roster WHERE sheet_id = :sid", sid=sid) or 0
    try:
        return signin.issue_code(
            scope=sid, subject=key, display_name=row["display_name"], email=row["email"],
            email_subject=subject, email_body=body, kind="student", reply_to=g.sheet["owner_email"],
            precheck=lambda: signin.student_send_check(sid, key, class_size),
            on_sent=lambda: signin.record_student_send(sid, key),
            link_url=lambda token: url_for("student.link_sign_in", sid=sid, token=token, _external=True),
            force_email=force_email,
        )
    except signin.SendRefused as refused:
        db.rollback()
        signin.record_refusal(sid, key, refused.reason)
        raise


def _ask_for_link(name, email):
    """A ready-made email from a student to their instructor asking for a
    sign-in link: the backup when the site's own email can't reach them.
    Whoever typed the name sees this page, so it carries no more of the
    student's address than the domain (to pick where it opens)."""
    title = g.sheet["title"]
    return {
        "to": g.sheet["owner_email"],
        "subject": f"Sign-in link for {title}",
        "body": (
            f"Hi,\n\nI couldn't get a sign-in email from {settings.APP_NAME} for “{title}”. Could you send me a "
            "sign-in link from your email?\n\n"
            f"On the class's page ({url_for('teach.sheet', sid=g.sheet['id'], _external=True)}), press "
            "“Sign-in link” next to my name, then “Open the email”, and send it.\n\n"
            f"My name on the class list: {name}\n\nThank you!\n"
        ),
        "service": compose.service_for(email),
    }


def _signed_in(key):
    """Bookkeeping after any successful sign-in. Commits."""
    signin.clear_refusals(g.sheet["id"], key)
    _note_first_sign_in(key)
    db.commit()


def _start_listed(row):
    """Someone on the class list: an emailed code if we have their address,
    otherwise a PIN."""
    if not row["email"]:
        session["pending_pin"] = {"sid": g.sheet["id"], "key": row["name_key"], "name": row["display_name"],
                                  "why": "no-email"}
        return redirect(_here("student.pin"))
    pending = {"sid": g.sheet["id"], "key": row["name_key"], "name": row["display_name"]}
    try:
        issued = _send_code(row)
    except signin.SendRefused as refused:
        if refused.reason in ("setup", "paused"):
            flash(str(refused), "error")
            return redirect(_here("student.signin"))
        # No email could go out. Stay on the code page anyway, so a code
        # from the instructor has somewhere to go.
        session["pending_student"] = {**pending, "refused": str(refused)}
        return redirect(_here("student.verify"))
    session["pending_student"] = pending
    if issued.dev_code:
        session["dev_code"] = issued.dev_code
    if issued.status == "recent":
        flash(issued.note, "info")
    return redirect(_here("student.verify"))


def _known_unlisted(key):
    """A name that isn't on the class list but has been used here before:
    its stored spelling, or None."""
    found = db.row(
        "SELECT display_name FROM submissions WHERE sheet_id = :sid AND name_key = :key",
        sid=g.sheet["id"], key=key,
    )
    if found:
        return found["display_name"]
    return None


def _start_unlisted(typed):
    """Someone not on the class list (when the instructor allows that), or
    coming back after signing up that way: a PIN protects the name."""
    name = tidy_name(typed)
    key = normalize_name(name)
    if not key:
        flash("Type your name to continue.", "error")
        return redirect(_here("student.signin"))
    if not g.sheet["allow_unlisted"]:
        flash(f"“{name}” isn't on the class list for this sign-up. If it should be, email your instructor.", "error")
        return redirect(_here("student.signin"))
    known = _known_unlisted(key)
    has_pin = signin.get_pin(g.sheet["id"], key) is not None
    if not known and not has_pin:
        if not g.sheet["bidding_open"]:
            flash(
                f"Sign-ups are closed, and “{name}” isn't on the class list. If you still need to rank, "
                "email your instructor.",
                "error",
            )
            return redirect(_here("student.signin"))
        if len(key.split()) < 2:
            flash("Please type your first and last name.", "error")
            return redirect(_here("student.signin"))
    session["pending_pin"] = {"sid": g.sheet["id"], "key": key, "name": known or name, "why": "unlisted"}
    return redirect(_here("student.pin"))


def _twins(rows, row):
    """Everyone on the list who shares this student's name (with an email to
    tell them apart), when there's more than one."""
    found = [r for r in rows if base_key(r["name_key"]) == base_key(row["name_key"])
             and r["email"] and not r["is_test"]]
    return found if len(found) > 1 else []


def _masks(emails):
    """Partly hidden addresses, each different from the others: just enough
    for a student to recognize their own."""
    for shown in range(1, 30):
        masks = []
        for e in emails:
            local, _, domain = e.partition("@")
            n = min(len(local) - 1, max(shown, 3 if len(local) > 4 else 1))
            masks.append(f"{local[:n]}{'•' * max(3, len(local) - n)}@{domain}")
        if len(set(masks)) == len(masks):
            return masks
    return [mask_email(e) for e in emails]


def _which_one(typed, twins):
    """Two (or more) students share this name: the one signing in picks
    their own email, and the code goes only there."""
    twins = sorted(twins, key=lambda r: r["name_key"])
    return render_template(
        "student_which.html", sheet=g.sheet, typed=typed, name=SAME_NAME_SUFFIX.sub("", twins[0]["display_name"]),
        options=list(zip([r["name_key"] for r in twins], _masks([r["email"] for r in twins]))),
    )


@bp.route("/login", methods=["GET"])
def login_again(sid):
    """Reopening the "Did you mean…?" page (a refresh, the back button)."""
    return redirect(_here("student.signin"))


@bp.route("/login", methods=["POST"])
def login(sid):
    rows = _roster_rows()
    if not g.sheet["allow_unlisted"] and not rows:
        flash("This sign-up isn't ready yet — your instructor is still adding the class list.", "error")
        return redirect(_here("student.signin"))
    typed = clean_text(request.form.get("name"), 120)
    pick = request.form.get("pick", "")
    if pick == NEW_NAME:
        return _start_unlisted(typed)
    if pick:
        row = _roster_row(pick)
        if not row or (row["is_test"] and not _owner_is_viewing() and fold(typed) != row["name_key"]):
            flash("Pick your name again.", "error")
            return redirect(_here("student.signin"))
        twins = _twins(rows, row) if request.form.get("which") != "1" else []
        return _which_one(typed, twins) if twins else _start_listed(row)
    if not typed:
        flash("Type your name to continue.", "error")
        return redirect(_here("student.signin"))

    row, suggestions = match_name(typed, rows)
    if row:
        twins = _twins(rows, row)
        return _which_one(typed, twins) if twins else _start_listed(row)
    key = normalize_name(typed)
    if _known_unlisted(key) or signin.get_pin(g.sheet["id"], key):
        return _start_unlisted(typed)
    if not rows:
        return _start_unlisted(typed)
    # Not on the class list: before making a new, separate person, ask.
    suggestions = [s for s in suggestions if not s["is_test"] or _owner_is_viewing()]
    can_add = bool(g.sheet["allow_unlisted"]) and bool(g.sheet["bidding_open"]) and len(key.split()) >= 2
    return render_template(
        "student_match.html", sheet=g.sheet, typed=tidy_name(typed), suggestions=suggestions,
        can_add=can_add, new_name=NEW_NAME,
        needs_full_name=bool(g.sheet["allow_unlisted"]) and len(key.split()) < 2,
    )


@bp.route("/pin", methods=["GET", "POST"])
def pin(sid):
    """Choose a PIN the first time a name is used; enter it after that."""
    pending = _pending("pending_pin")
    if not pending:
        return redirect(_here("student.signin"))
    sheet_id, key, name = g.sheet["id"], pending["key"], pending["name"]
    listed = _roster_row(key)
    if listed and listed["email"]:
        # The instructor added their email since: confirm by email instead.
        session.pop("pending_pin", None)
        return _start_listed(listed)
    if not listed and not g.sheet["allow_unlisted"]:
        session.pop("pending_pin", None)
        flash(f"“{name}” isn't on the class list for this sign-up. If it should be, email your instructor.", "error")
        return redirect(_here("student.signin"))

    existing = signin.get_pin(sheet_id, key)
    reset_note = ""
    if request.method == "POST":
        mode = request.form.get("mode")
        shared = request.form.get("shared") == "1"
        if mode == "enter":
            if not existing:
                # The PIN was reset while this page was open: never turn a
                # guess at the old PIN into the new one.
                reset_note = "Your instructor reset your PIN, so choose a new one below."
            else:
                check = signin.check_pin(sheet_id, key, request.form.get("pin"))
                if check.status == "ok":
                    auth.sign_in_student(sheet_id, name, key, "pin", shared=shared)
                    _signed_in(key)
                    flash(f"Welcome back, {name}.", "success")
                    return redirect(_here("student.home"))
                flash(signin.pin_message(check), "error")
        elif existing:
            flash(
                "A PIN was just set for this name, so enter it below. If that wasn't you, tell your instructor.",
                "error",
            )
        else:
            chosen, problem = signin.read_new_pin(request.form.get("pin"))
            again = signin.pin_digits(request.form.get("pin_again"))
            if problem:
                flash(problem, "error")
            elif again != chosen:
                flash("The two PINs don't match — type the same PIN in both boxes.", "error")
            elif signin.set_pin(sheet_id, key, chosen):
                auth.sign_in_student(sheet_id, name, key, "pin", shared=shared)
                _signed_in(key)
                flash("You're signed in. Remember your PIN — you'll need it on any other device.", "success")
                return redirect(_here("student.home"))
            else:
                flash("Someone just chose a PIN for this name. If that wasn't you, tell your instructor.", "error")
        existing = signin.get_pin(sheet_id, key)
    has_ranking = db.scalar(
        "SELECT 1 FROM submissions WHERE sheet_id = :sid AND name_key = :key", sid=sheet_id, key=key
    )
    has_roster = bool(db.scalar("SELECT 1 FROM roster WHERE sheet_id = :sid AND is_test = 0 LIMIT 1", sid=sheet_id))
    return render_template(
        "student_pin.html", sheet=g.sheet, name=name, choosing=existing is None, why=pending.get("why"),
        reset_note=reset_note, has_ranking=bool(has_ranking), has_roster=has_roster,
        min_digits=signin.PIN_MIN_DIGITS, max_digits=signin.PIN_MAX_DIGITS,
    )


@bp.route("/verify", methods=["GET", "POST"])
def verify(sid):
    pending = _pending("pending_student")
    if not pending:
        return redirect(_here("student.signin"))
    record = signin.pending_code(g.sheet["id"], pending["key"])
    # Before anything below writes: it may save a lookup on its own connection.
    ask = _ask_for_link(pending["name"], (record or _roster_row(pending["key"]) or {}).get("email") or "")
    if request.method == "POST":
        check = signin.check_code(g.sheet["id"], pending["key"], request.form.get("code"))
        if check.status == "ok":
            auth.sign_in_student(g.sheet["id"], pending["name"], pending["key"], "code",
                                 shared=request.form.get("shared") == "1")
            _signed_in(pending["key"])
            flash("You're signed in.", "success")
            return redirect(_here("student.home"))
        if check.status == "missing":
            flash("That code isn't right. Your instructor can send you a sign-in link from their own email, or "
                  "give you a code to type here — ask them with the button below.", "error")
        else:
            other = (signin.code_for_other_class(g.sheet["id"], record["email"], request.form.get("code"))
                     if check.status == "wrong" and record else None)
            other_sheet = sheets.get_sheet(other) if other else None
            if other_sheet:
                flash(f"That code is for “{other_sheet['title']}”. For this class, use the email titled "
                      f"“… sign-in code for “{g.sheet['title']}””.", "error")
            else:
                flash(signin.check_message(check, for_student=True), "error")
            record = signin.pending_code(g.sheet["id"], pending["key"])
    if not record and not pending.get("refused"):
        session.pop("pending_student", None)
        flash("That sign-in attempt expired — please type your name again.", "error")
        return redirect(_here("student.signin"))
    from_instructor = bool(record and record["from_instructor"] and signin.code_works(record))
    return render_template(
        "verify.html",
        for_student=True,
        sheet=g.sheet,
        who=pending["name"],
        shown_email=mask_email(record["email"]) if record else "",
        sent_at=record["last_sent_at"] if record and not from_instructor else None,
        expires_at=record["expires_at"] if record else None,
        expired=bool(record) and parse_iso(record["expires_at"]) <= now(),
        no_email=None if record else pending.get("refused"),
        from_instructor=from_instructor,
        locked=False,
        verify_url=_here("student.verify"),
        resend_url=_here("student.resend"),
        restart_url=_here("student.restart"),
        restart_label=f"Not {pending['name']}? Pick a different name",
        expiry=signin.CODE_EXPIRY_MINUTES,
        link_hours=signin.LINK_EXPIRY_HOURS,
        code_length=signin.CODE_LENGTH,
        dev_code=session.get("dev_code"),
        sender=signin.code_sender(),
        links=signin.codes_have_links(),
        ask=ask,
    )


@bp.route("/verify/resend", methods=["POST"])
def resend(sid):
    pending = _pending("pending_student")
    if not pending:
        return redirect(_here("student.signin"))
    row = _roster_row(pending["key"])
    if not row or not row["email"]:
        session.pop("pending_student", None)
        flash("Something about your sign-in changed — please type your name again.", "info")
        return redirect(_here("student.signin"))
    try:
        issued = _send_code(row, force_email=True)
    except signin.SendRefused as refused:
        flash(str(refused), "error")
        return redirect(_here("student.verify"))
    session["pending_student"] = {k: v for k, v in pending.items() if k != "refused"}
    if issued.dev_code:
        session["dev_code"] = issued.dev_code
    if issued.status == "recent":
        flash(issued.note, "info")
    else:
        flash("Sent a new code — use the newest email.", "success")
    return redirect(_here("student.verify"))


@bp.route("/link/<token>", methods=["GET", "POST"])
def link_sign_in(sid, token):
    """The one-click link from a code email. Opening it only shows a button
    (email scanners open links too); pressing the button signs in."""
    found = signin.find_link(g.sheet["id"], token)
    row = _roster_row(found["subject"]) if found else None
    if not row or (row["email"] or "").lower() != found["email"].lower():
        flash("That sign-in link has expired or was already used. Type your name to get a new code.", "error")
        return redirect(_here("student.signin"))
    if request.method == "POST":
        signin.use_link(found)
        auth.sign_in_student(g.sheet["id"], row["display_name"], row["name_key"], "code",
                             shared=request.form.get("shared") == "1")
        _signed_in(row["name_key"])
        flash("You're signed in.", "success")
        return redirect(_here("student.home"))
    return render_template("student_link.html", sheet=g.sheet, name=row["display_name"])


@bp.route("/restart", methods=["POST"])
def restart(sid):
    """Leave a half-finished sign-in and type a different name."""
    auth.sign_out_student(g.sheet["id"])
    return redirect(_here("student.signin"))


@bp.route("/logout", methods=["POST"])
def logout(sid):
    ranked = g.student and db.scalar(
        "SELECT 1 FROM submissions WHERE sheet_id = :sid AND name_key = :key", sid=g.sheet["id"], key=g.student["k"]
    )
    auth.sign_out_student(g.sheet["id"])
    flash("You're signed out. Your ranking is saved." if ranked
          else "You're signed out. (You haven't saved a ranking yet.)", "success")
    return redirect(_here("student.signin"))


@bp.route("/logout-everywhere", methods=["POST"])
def logout_all(sid):
    """Sign out of every class on this computer (for shared computers)."""
    auth.sign_out_all_students()
    flash("You're signed out of every class on this computer.", "success")
    return redirect(_here("student.signin"))


def _note_first_sign_in(key):
    """A real student signing in means the link got shared."""
    if not g.sheet["shared_at"] and key not in sheets.test_keys(g.sheet["id"]):
        db.run("UPDATE sheets SET shared_at = :at WHERE id = :sid AND shared_at IS NULL", at=iso(), sid=g.sheet["id"])
        db.commit()


NAME_LOOKUPS_PER_NETWORK = 600


def _lookup_allowed():
    """A light limit on name suggestions — per browser, with a much higher
    ceiling per network address (a whole class shares campus Wi-Fi) — so
    the class list can't be read out a few letters at a time."""
    scope = f"names:{g.sheet['id']}"
    browser = "b:" + signin.browser_id()
    network = "n:" + signin.keyed_hash("ip|" + (signin.client_ip() or "unknown"))
    since = iso(now() - timedelta(minutes=10))
    count = lambda client: db.scalar(  # noqa: E731
        "SELECT COUNT(*) FROM auth_failures WHERE scope = :scope AND client = :client AND at > :since",
        scope=scope, client=client, since=since,
    ) or 0
    if count(browser) >= NAME_LOOKUPS_PER_10_MINUTES or count(network) >= NAME_LOOKUPS_PER_NETWORK:
        return False
    at = iso()
    db.run_many(
        "INSERT INTO auth_failures (id, scope, subject, client, at) VALUES (:id, :scope, '', :client, :at)",
        [{"id": new_id(16), "scope": scope, "client": browser, "at": at},
         {"id": new_id(16), "scope": scope, "client": network, "at": at}],
    )
    db.commit()
    return True


@bp.route("/names")
def names(sid):
    """Name suggestions as a student types, from the class list only (never
    from names people typed in themselves). Three letters minimum, five
    results at most, matching the start of a name — enough to find
    yourself, not to page through the list."""
    q = fold(request.args.get("q") or "")
    if len(q.replace(" ", "")) < 3 or not _lookup_allowed():
        return jsonify([])
    show_tests = _owner_is_viewing()
    rows = [r for r in _roster_rows() if show_tests or not r["is_test"]]
    typed = q.split()
    scored = []
    for r in rows:
        theirs = r["name_key"].split()
        if r["name_key"].startswith(q):
            score = 0
        elif all(any(word.startswith(t) for word in theirs) for t in typed):
            score = 1
        else:
            continue
        name = SAME_NAME_SUFFIX.sub("", r["display_name"])
        scored.append((score, name.lower(), name))
    scored.sort()
    found = list(dict.fromkeys(name for _, _, name in scored))[:5]
    if not found and len(q.replace(" ", "")) >= 4:
        # A typo ("alex jonson"): offer the close matches "Did you mean" would.
        _row, close = match_name(q, rows)
        found = [r["display_name"] for r in close[:3]]
    return jsonify(found)


# ---------------------------------------------------------------------------
# Signed-in pages
# ---------------------------------------------------------------------------
def _my_submission():
    return db.row(
        "SELECT * FROM submissions WHERE sheet_id = :sid AND name_key = :key",
        sid=g.sheet["id"], key=g.student["k"],
    )


def _is_test_viewer():
    return g.student["k"] in sheets.test_keys(g.sheet["id"])


def _published():
    return bool(g.sheet["published_at"]) and bool(
        db.scalar("SELECT 1 FROM assignments WHERE sheet_id = :sid LIMIT 1", sid=g.sheet["id"])
    )


def _my_day():
    """(day key, label) of this student's day on the saved schedule, or (None, None)."""
    found = db.row(
        "SELECT day_key FROM assignments WHERE sheet_id = :sid AND name_key = :key",
        sid=g.sheet["id"], key=g.student["k"],
    )
    labels = {d["key"]: d["label"] for d in g.days}
    if found and found["day_key"] in labels:
        return found["day_key"], labels[found["day_key"]]
    return None, None


@bp.route("/home")
def home(sid):
    gate = _need_student()
    if gate:
        return gate
    mine = _my_submission()
    labels = {d["key"]: d["label"] for d in g.days}
    order = [d["key"] for d in g.days]
    summary = None
    new_days = []
    if mine:
        ranked = [d for d in json.loads(mine["ranking"]) if d in labels]
        excluded = {d for d in json.loads(mine["excluded_days"]) if d in labels}
        summary = {
            "ranking": [labels[d] for d in ranked],
            "cant": [labels[d] for d in order if d in excluded],
            "saved_at": mine["updated_at"],
        }
        new_days = [labels[d] for d in order if d not in ranked]
    published = _published()
    my_day_key, my_day = _my_day() if published else (None, None)
    my_method = db.scalar(
        "SELECT method FROM assignments WHERE sheet_id = :sid AND name_key = :key", sid=g.sheet["id"], key=g.student["k"]
    ) if my_day_key else None
    cant_do_my_day = bool(my_day_key and mine and my_day_key in json.loads(mine["excluded_days"]))
    day_added_later = bool(my_day_key and mine and my_day_key not in json.loads(mine["ranking"]))
    return render_template(
        "student_home.html",
        sheet=g.sheet,
        name=g.student["n"],
        has_submitted=mine is not None,
        summary=summary,
        new_days=new_days,
        published=published,
        my_day=my_day,
        cant_do_my_day=cant_do_my_day,
        day_added_later=day_added_later,
        my_method=my_method,
        counts=sheets.counts(g.sheet["id"]),
        is_test=_is_test_viewer(),
        shared=bool(g.student.get("x")),
        other_classes=len(session.get("students") or {}) - 1,
    )


@bp.route("/rank")
def rank(sid):
    gate = _need_student()
    if gate:
        return gate
    mine = _my_submission()
    day_keys = [d["key"] for d in g.days]
    if mine:
        ranking = [d for d in json.loads(mine["ranking"]) if d in day_keys]
        excluded = [d for d in json.loads(mine["excluded_days"]) if d in day_keys]
        comments = {k: v for k, v in json.loads(mine["comments"]).items() if k in day_keys}
    else:
        ranking, excluded, comments = day_keys[:], [], {}
    new_days = [d["label"] for d in g.days if mine and d["key"] not in ranking]
    return render_template(
        "student_rank.html",
        sheet=g.sheet,
        name=g.student["n"],
        days=g.days,
        bidding_open=bool(g.sheet["bidding_open"]),
        has_submitted=mine is not None,
        new_days=new_days,
        is_test=_is_test_viewer(),
        init_data={
            "days": g.days,
            "ranking": ranking,
            "excluded": excluded,
            "comments": comments,
            "biddingOpen": bool(g.sheet["bidding_open"]),
            "hasSubmitted": mine is not None,
            "savedAt": mine["updated_at"] if mine else None,
            "saveUrl": _here("student.save"),
            "csrfToken": auth.csrf_token(),
            "commentLimit": COMMENT_LIMIT,
            "newDays": [d["key"] for d in g.days if mine and d["key"] not in ranking],
        },
    )


def _bad(message, status=400):
    return jsonify(ok=False, retry=False, error=message), status


@bp.route("/save", methods=["POST"])
def save(sid):
    if not g.student:
        return _bad(
            g.get("signed_out_because") or "You've been signed out — refresh the page and sign in again.", 401
        )
    if not g.sheet["bidding_open"]:
        return _bad("Sign-ups are closed, so changes can't be saved now. If something needs to change, "
                    "email your instructor.", 403)

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _bad("Something's off with that order — refresh the page and try again.")
    ranking = data.get("ranking")
    excluded = data.get("excluded", [])
    comments = data.get("comments", {})
    base = data.get("base_version")
    day_keys = [d["key"] for d in g.days]
    known = set(day_keys)

    if not isinstance(ranking, list) or not all(isinstance(d, str) and d in known for d in ranking):
        return _bad("Your instructor changed the list of days — refresh the page to see the new list.", 409)
    if not ranking or len(set(ranking)) != len(ranking):
        return _bad("Something's off with that order — refresh the page and try again.")
    if not isinstance(excluded, list) or not all(isinstance(d, str) and d in known for d in excluded):
        return _bad("Your instructor changed the list of days — refresh the page to see the new list.", 409)
    if not isinstance(comments, dict) or not all(isinstance(v, str) for v in comments.values()):
        return _bad("Couldn't read your notes — refresh the page and try again.")
    clean_comments = {}
    for day in day_keys:
        text = clean_text(comments.get(day, ""), COMMENT_LIMIT + 50, keep_newlines=True)
        if len(text) > COMMENT_LIMIT:
            return _bad(f"A note is over {COMMENT_LIMIT} characters — please shorten it.")
        if text:
            clean_comments[day] = text

    mine = _my_submission()
    if mine and isinstance(base, str) and base != mine["updated_at"]:
        return jsonify(
            ok=False, conflict=True, retry=False,
            error="You changed your ranking somewhere else (another tab or device) after this page opened. "
                  "Reload the page to see your newest version.",
        ), 409

    listed = _roster_row(g.student["k"])
    name = listed["display_name"] if listed else (mine["display_name"] if mine else g.student["n"])
    saved_at = iso()
    db.run(
        """
        INSERT INTO submissions (sheet_id, name_key, display_name, ranking, excluded_days, comments, updated_at)
        VALUES (:sid, :key, :name, :ranking, :excluded, :comments, :at)
        ON CONFLICT (sheet_id, name_key) DO UPDATE SET
            display_name = excluded.display_name,
            ranking = excluded.ranking,
            excluded_days = excluded.excluded_days,
            comments = excluded.comments,
            updated_at = excluded.updated_at
        """,
        sid=g.sheet["id"], key=g.student["k"], name=name,
        ranking=json.dumps(ranking), excluded=json.dumps([d for d in day_keys if d in set(excluded)]),
        comments=json.dumps(clean_comments), at=saved_at,
    )
    sheets.touch(g.sheet["id"])
    db.commit()
    return jsonify(ok=True, saved_at=saved_at)


@bp.route("/preview")
def preview(sid):
    return redirect(_here("student.schedule"))


@bp.route("/schedule")
def schedule(sid):
    gate = _need_student()
    if gate:
        return gate
    counts = sheets.counts(g.sheet["id"])
    viewer_is_test = _is_test_viewer()
    tests = sheets.test_keys(g.sheet["id"])
    me = g.student["k"]
    context = {"sheet": g.sheet, "days": g.days, "counts": counts, "me": me, "by_day": None, "my_day": None}

    outsider = bool(sheets.counts(g.sheet["id"])["roster"]) and not viewer_is_test and not db.scalar(
        "SELECT 1 FROM roster WHERE sheet_id = :sid AND name_key = :key", sid=g.sheet["id"], key=me
    )
    context["outsider"] = outsider
    if _published():
        context["mode"] = "final"
        context["my_day"] = _my_day()[1]
        if g.sheet["show_preview"] and not outsider:
            by_day = {d["key"]: [] for d in g.days}
            for a in sheets.get_assignments(g.sheet["id"]):
                if a["day_key"] in by_day and (viewer_is_test or a["name_key"] not in tests):
                    by_day[a["day_key"]].append({"key": a["name_key"], "name": a["display_name"]})
            context["by_day"] = by_day
    elif g.sheet["bidding_open"] and g.sheet["show_preview"] and not outsider:
        context["mode"] = "draft"
        inputs = sheets.schedule_inputs(g.sheet, viewer_is_test=viewer_is_test)
        # Only people who ranked: a draft never shows who hasn't.
        results = compute_assignment(
            inputs.students, inputs.day_keys, g.sheet["capacity_per_day"], algorithm=inputs.algorithm,
            seed=g.sheet["lottery_seed"],
        )
        by_day = {d["key"]: [] for d in g.days}
        for key, name, day_key, _method in results:
            by_day[day_key].append({"key": key, "name": name})
            if key == me:
                context["my_day"] = next(d["label"] for d in g.days if d["key"] == day_key)
        context["by_day"] = by_day
        context["ranked"] = len(inputs.rows)
        context["ranked_me"] = any(s["key"] == me for s in inputs.students)
        wanted = {}
        for s in inputs.students:
            if s["pref"]:
                wanted[s["pref"][0]] = wanted.get(s["pref"][0], 0) + 1
        context["oversubscribed"] = {k for k, n in wanted.items() if n > g.sheet["capacity_per_day"]}
    elif g.sheet["bidding_open"]:
        context["mode"] = "hidden"
    else:
        context["mode"] = "waiting"
    return render_template("student_preview.html", **context)
