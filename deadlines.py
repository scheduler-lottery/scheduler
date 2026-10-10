"""
A sheet's deadline: the moment sign-ups close by themselves.

There's no clock running on a serverless site, so a passed deadline is acted
on the first time anyone opens the sheet afterwards (every student and
instructor page checks), and by the daily job for sheets nobody opens. The
close is one conditional UPDATE, so however many pages open at once, only
one does it.

What happens then is the instructor's choice (sheets.at_close):
  ask      - if everyone on the class list has ranked, close and make the
             schedule; otherwise keep sign-ups open and ask the instructor
             (an email and a question on their page): close now and give
             those who didn't rank the seats left over, or keep it open.
  schedule - close and make the schedule (those who didn't rank get the
             seats left over).
  publish  - close, make the schedule, and publish it.
Once closed, students can't change their rankings and the draft schedule
goes away, as with any close. The instructor can always reopen.

The instructor is also told, once, when everyone on their list has ranked.
These notes go by the site's own email when it has a mailbox (WorkOS only
sends sign-in codes); the same news is always on their class page.
"""

from datetime import datetime, timedelta

from flask import url_for

import db
import settings
import sheets
import signin
from matching_engine import compute_assignment
from util import date_label, in_zone, iso, parse_iso, plural

AT_CLOSE = ("ask", "schedule", "publish")
LATEST_DAYS = 400  # a deadline further off than this is surely a typo


def time_label(hour, minute):
    return f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


# The times to choose from: every half hour ("17:00", "5:00 PM"), and a
# minute to midnight, for "by the end of the day".
TIMES = [(f"{h:02d}:{m:02d}", time_label(h, m)) for h in range(24) for m in (0, 30)] + [("23:59", "11:59 PM")]


def slots(chosen):
    """TIMES, plus the chosen time ("17:15") if it isn't one of them (a
    deadline saved before), in order."""
    if not chosen or chosen in dict(TIMES):
        return TIMES
    try:
        hour, minute = (int(part) for part in chosen.split(":"))
        extra = (f"{hour:02d}:{minute:02d}", time_label(hour, minute))
    except ValueError:
        return TIMES
    return sorted(TIMES + [extra])


def zone_label(zone):
    """"CDT" (or the zone's name, if it has no short one)."""
    try:
        return in_zone(datetime.now().astimezone(), zone).tzname() or zone
    except Exception:  # noqa: BLE001
        return zone


def to_utc(day, clock, zone):
    """A picked date ("2026-10-10") and time ("17:00") in the instructor's
    time zone, as an aware UTC datetime; None if either isn't real."""
    from zoneinfo import ZoneInfo

    try:
        hour, minute = (int(part) for part in (clock or "").split(":"))
        local = datetime.fromisoformat((day or "").strip()[:10]).replace(hour=hour, minute=minute)
        return local.replace(tzinfo=ZoneInfo(zone or "UTC")).astimezone(parse_iso(iso()).tzinfo)
    except (ValueError, TypeError, KeyError, LookupError):
        return None


def local_parts(closes_at, zone):
    """("2026-10-10", "17:00") for a stored deadline, in the instructor's zone."""
    if not closes_at:
        return "", "17:00"
    local = in_zone(closes_at, zone)
    return local.date().isoformat(), f"{local.hour:02d}:{local.minute:02d}"


def describe(closes_at, zone):
    """"Fri, Oct 10 at 5:00 PM CDT", for messages written in the instructor's zone."""
    local = in_zone(closes_at, zone)
    return f"{date_label(local.date())} at {time_label(local.hour, local.minute)} {zone_label(zone)}".strip()


def missing(sheet):
    """Names on the class list (not test students) with no ranking yet;
    empty when there's no class list."""
    return [r["display_name"] for r in db.rows(
        "SELECT display_name FROM roster r WHERE sheet_id = :sid AND is_test = 0 AND NOT EXISTS "
        "(SELECT 1 FROM submissions s WHERE s.sheet_id = r.sheet_id AND s.name_key = r.name_key) "
        "ORDER BY lower(display_name)",
        sid=sheet["id"],
    )]


def due(sheet):
    return bool(sheet and sheet["bidding_open"] and sheet["closes_at"] and sheet["closes_at"] <= iso())


def waiting_for_decision(sheet):
    """The deadline passed with students missing, and the instructor asked to be asked."""
    return bool(sheet["bidding_open"] and sheet["deadline_asked_at"] and sheet["closes_at"])


def _class_page(sheet):
    return url_for("teach.sheet", sid=sheet["id"], _external=True)


def _tell(sheet, subject, body):
    """Email the sheet's instructor, if the site has a mailbox. Never raises."""
    if not signin.smtp_ready() or signin.daily_room("notice") <= 0:
        return
    try:
        signin.send_email(sheet["owner_email"], subject, body)
        signin.log_email(sheet["owner_email"], "notice")
        db.commit()
    except Exception as exc:  # noqa: BLE001 - the class page says the same
        db.rollback()
        db.record_error("EMAIL", f"Sending a deadline note failed: {exc!r}")


def make_schedule(sheet):
    """Make (and save) the schedule from the rankings so far. False if nobody
    has ranked. Not committed here."""
    if not sheet["lottery_seed"]:
        db.run("UPDATE sheets SET lottery_seed = :seed WHERE id = :sid", seed=sheets.new_lottery_seed(), sid=sheet["id"])
        sheet = sheets.get_sheet(sheet["id"])
    inputs = sheets.schedule_inputs(sheet)
    if not inputs.students:
        return False
    results = compute_assignment(
        inputs.students, inputs.day_keys, sheet["capacity_per_day"], algorithm=inputs.algorithm,
        seed=sheet["lottery_seed"], unranked=inputs.unranked,
    )
    sheets.save_assignment(sheet["id"], results, inputs.algorithm, inputs.signature)
    return True


def enforce(sheet):
    """Act on a passed deadline, once. Returns the sheet as it is now."""
    if not due(sheet):
        return sheet
    at = iso()
    zone = sheet["owner_timezone"] or settings.DEFAULT_TIMEZONE
    when = describe(sheet["closes_at"], zone)
    left = missing(sheet)
    if sheet["at_close"] == "ask" and left:
        if not sheet["deadline_asked_at"]:
            asked = db.run("UPDATE sheets SET deadline_asked_at = :at WHERE id = :sid AND deadline_asked_at IS NULL",
                           at=at, sid=sheet["id"])
            db.commit()
            if asked:
                _tell(
                    sheet, f"{sheet['title']}: the deadline passed, and {plural(len(left), 'student hasn’t', 'students haven’t')} ranked",
                    f"The deadline you set for “{sheet['title']}” ({when}) has passed, and "
                    f"{plural(len(left), 'student')} on your list {'hasn’t' if len(left) == 1 else 'haven’t'} ranked: "
                    + ", ".join(left[:12]) + (" …" if len(left) > 12 else "") + ".\n\n"
                    "Sign-ups are still open while you decide. On your class page, choose to close them now (anyone "
                    "who didn't rank gets one of the seats left over) or to keep them open a while longer:\n\n"
                    f"{_class_page(sheet)}\n",
                )
        return sheets.get_sheet(sheet["id"])
    closed = db.run(
        "UPDATE sheets SET bidding_open = 0, closes_at = NULL, auto_closed_at = :at, updated_at = :at "
        "WHERE id = :sid AND bidding_open = 1 AND closes_at IS NOT NULL AND closes_at <= :at",
        at=at, sid=sheet["id"],
    )
    if not closed:
        db.commit()
        return sheets.get_sheet(sheet["id"])
    sheets.take_snapshot(sheet, "closed", "When sign-ups closed at the deadline")
    made = make_schedule(sheet)
    published = made and sheet["at_close"] == "publish"
    if published:
        db.run("UPDATE sheets SET published_at = :at WHERE id = :sid", at=at, sid=sheet["id"])
    db.commit()
    if made:
        news = ("The schedule is made and published, so students can see their day now."
                if published else "The schedule is made: check it, then publish it so students see their day.")
    else:
        news = "Nobody had ranked, so there's no schedule to make."
    _tell(
        sheet, f"{sheet['title']}: sign-ups closed at your deadline",
        f"Sign-ups for “{sheet['title']}” closed at the deadline you set ({when}), so rankings are locked in. "
        f"{news}\n\n"
        + (f"{plural(len(left), 'student')} on your list hadn't ranked; any seats left over went to them.\n\n"
           if left and made else "")
        + f"Your class page: {_class_page(sheet)}\n\nYou can reopen sign-ups there at any time.\n",
    )
    return sheets.get_sheet(sheet["id"])


def enforce_all(owner_id=None):
    """Act on every passed deadline (one instructor's, or everyone's, for
    the daily job). Returns how many sheets it looked at."""
    found = db.rows(
        "SELECT id FROM sheets WHERE bidding_open = 1 AND closes_at IS NOT NULL AND closes_at <= :at"
        + (" AND owner_id = :owner" if owner_id else ""),
        at=iso(), owner=owner_id,
    )
    for row in found:
        enforce(sheets.get_sheet(row["id"]))
    return len(found)


def everyone_ranked(sheet):
    """After a ranking is saved: tell the instructor, once, when everyone on
    their list has ranked."""
    if not sheet["bidding_open"] or sheet["all_ranked_at"] or missing(sheet):
        return
    if not db.scalar("SELECT 1 FROM roster WHERE sheet_id = :sid AND is_test = 0", sid=sheet["id"]):
        return
    told = db.run("UPDATE sheets SET all_ranked_at = :at WHERE id = :sid AND all_ranked_at IS NULL",
                  at=iso(), sid=sheet["id"])
    db.commit()
    if not told:
        return
    count = db.scalar("SELECT COUNT(*) FROM roster WHERE sheet_id = :sid AND is_test = 0", sid=sheet["id"])
    zone = sheet["owner_timezone"] or settings.DEFAULT_TIMEZONE
    _tell(
        sheet, f"{sheet['title']}: everyone has ranked",
        f"All {plural(count, 'student')} on your list for “{sheet['title']}” have ranked their days.\n\n"
        + (f"Sign-ups close by themselves at your deadline ({describe(sheet['closes_at'], zone)}), or you can close "
           "them and make the schedule now" if sheet["closes_at"] else
           "You can close sign-ups and make the schedule whenever you're ready")
        + f":\n\n{_class_page(sheet)}\n",
    )


def latest(days=LATEST_DAYS):
    """The furthest-off deadline the form accepts, as a UTC datetime."""
    return parse_iso(iso()) + timedelta(days=days)
