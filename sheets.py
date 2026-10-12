"""
Loading and saving sign-up sheets: settings, days, class list, rankings,
the schedule — and the restore points that make all of it recoverable.

Restore points ("snapshots") are full copies of one sheet. They're taken
automatically once a day when something changed, and right before anything
destructive: deleting rankings, replacing the class list, removing a day,
re-making a schedule over hand-made moves, going back to an earlier
version, or deleting the sheet. An instructor can download any of them or
go back to it.
"""

import csv
import hashlib
import io
import json
import secrets
import zipfile
from collections import Counter
from datetime import timedelta
from types import SimpleNamespace

import db
from matching_engine import ALGORITHMS, DEFAULT_ALGORITHM, EXCLUDED, MANUAL, OVERFLOW, UNRANKED, build_student_prefs
from roster import SAME_NAME_SUFFIX, base_key, numbered
from util import ID_ALPHABET, in_zone, iso, iso_precise, new_id, normalize_name, now, plural, read_date, slugify

MAX_DAYS = 30
MAX_BACKUP_JSON_BYTES = 5 * 1024 * 1024
SNAPSHOTS_KEPT = 40
# Restore points taken as a matter of routine; when there are too many,
# these go first, so ones taken before a delete or replace last longest.
ROUTINE_SNAPSHOTS = ("daily", "closed", "rerun", "undo", "restore", "place", "method")
DELETED_SHEETS_KEPT_DAYS = 30
TEST_STUDENT_NAMES = ("Test Student 1", "Test Student 2", "Test Student 3")
EXPORT_FORMAT = "scheduler-sheet-v1"

SHEET_FIELDS = (
    "id", "title", "note", "rank_by", "capacity_per_day", "algorithm", "bidding_open",
    "allow_unlisted", "show_preview", "include_unranked", "lottery_seed", "last_algorithm",
    "scheduled_at", "schedule_signature", "published_at", "roster_updated_at", "created_at",
    "updated_at", "canvas_course_id", "canvas_course", "short_code", "progress_emails",
)
# What a restore puts back. Whether sign-ups are open, and whether the
# schedule is published, are left as they are now: going back to an earlier
# version should never quietly close sign-ups or un-publish a schedule.
RESTORED_SETTINGS = (
    "title", "note", "rank_by", "capacity_per_day", "algorithm", "allow_unlisted",
    "show_preview", "include_unranked", "last_algorithm", "scheduled_at", "schedule_signature",
)


def get_sheet(sid):
    if not sid:
        return None
    return db.row(
        """
        SELECT s.*, i.email AS owner_email, i.disabled AS owner_disabled, i.timezone AS owner_timezone
        FROM sheets s JOIN instructors i ON i.id = s.owner_id
        WHERE s.id = :sid
        """,
        sid=sid.lower(),
    )


# A class's link ends in its code. 12 characters from 31 (no look-alikes
# such as 0/o or 1/l) is about 8 x 10^17 codes, so nobody guesses one, and
# each new code is checked against every sheet there is — or was, while a
# deleted sheet can still be brought back — so no two classes ever share
# one. Older sheets keep their 8-character codes.
SHEET_ID_LENGTH = 12
SHEET_ID_LENGTHS = (8, 12)
# A class's short link, on this site (no outside link-shortener, so no ads
# and nobody else told the link): example.com/<8 characters>, always with a
# digit, so it's never a word one of the site's own pages uses. With 31^8 of
# them, trying codes until one is a class's gets nowhere, even from many
# network addresses at once (each held to app.py's limit on wrong codes).
# The first short links were 6 characters; those keep working.
SHORT_CODE_LENGTH = 8
SHORT_CODE_LENGTHS = (6, 8)


def short_code(sid):
    """The sheet's short-link code, made the first time it's asked for. None
    if the sheet isn't there (deleted from another tab while its page was
    loading), rather than trying for ever."""
    for _ in range(20):
        found = db.row("SELECT short_code FROM sheets WHERE id = :sid", sid=sid)
        if not found or found["short_code"]:
            return found["short_code"] if found else None
        made = new_id(SHORT_CODE_LENGTH)
        if _short_code_free(made):
            db.run("UPDATE sheets SET short_code = :c WHERE id = :sid AND short_code IS NULL", c=made, sid=sid)
            db.commit()
    return None


def _short_code_free(code):
    """Whether `code` can be a sheet's short code: shaped like one, and not
    another class's short code or class code (older classes have 8-character
    codes too, and those are looked up first), even a deleted class's."""
    return (
        isinstance(code, str) and len(code) in SHORT_CODE_LENGTHS and all(ch in ID_ALPHABET for ch in code)
        and any(ch.isdigit() for ch in code)
        and not db.scalar("SELECT 1 FROM sheets WHERE short_code = :c OR id = :c", c=code)
        and not db.scalar("SELECT 1 FROM snapshots WHERE sheet_id = :c", c=code)
    )


def by_short_code(code):
    """The sheet whose short link ends in `code` (its id), or None."""
    return db.scalar("SELECT id FROM sheets WHERE short_code = :c", c=(code or "").lower())


def new_sheet_id():
    while True:
        sid = new_id(SHEET_ID_LENGTH)
        if not db.scalar("SELECT 1 FROM sheets WHERE id = :sid", sid=sid) and not db.scalar(
            "SELECT 1 FROM snapshots WHERE sheet_id = :sid", sid=sid
        ):
            return sid


def new_lottery_seed():
    """The fixed random number a sheet's draws come from (see matching_engine)."""
    return secrets.randbelow(2_000_000_000) + 1


def touch(sid):
    db.run("UPDATE sheets SET updated_at = :at WHERE id = :sid", at=iso(), sid=sid)


def file_time(value, zone, seconds=False):
    """'Oct 8, 2026, 3:42 PM' in the instructor's time zone, for files."""
    if not value:
        return ""
    dt = in_zone(value, zone)
    hour = dt.strftime("%I").lstrip("0") or "12"
    clock = dt.strftime("%M:%S %p" if seconds else "%M %p")
    return f"{dt.strftime('%b')} {dt.day}, {dt.year}, {hour}:{clock}"


# ---------------------------------------------------------------------------
# Days
# ---------------------------------------------------------------------------
def get_days(sid):
    return db.rows(
        "SELECT day_key AS key, label, day_date AS date FROM sheet_days WHERE sheet_id = :sid ORDER BY sort_order",
        sid=sid,
    )


def save_days(sid, entries):
    """Make the sheet's days match `entries`, a list of (key, label, date)
    in display order (date is YYYY-MM-DD, or None for a day saved before
    days were picked on a calendar). Existing keys are kept, so rankings
    that point at a day stay put; unknown keys become new days. Returns the
    days that were removed, as [{key, label}]."""
    current = {d["key"]: d["label"] for d in get_days(sid)}
    kept = set()
    for position, (key, label, day_date) in enumerate(entries):
        if key not in current or key in kept:
            key = "d" + secrets.token_hex(3)
        kept.add(key)
        db.run(
            """
            INSERT INTO sheet_days (sheet_id, day_key, label, sort_order, day_date)
            VALUES (:sid, :key, :label, :pos, :date)
            ON CONFLICT (sheet_id, day_key) DO UPDATE SET
                label = excluded.label, sort_order = excluded.sort_order, day_date = excluded.day_date
            """,
            sid=sid, key=key, label=label, pos=position, date=day_date,
        )
    removed = [{"key": k, "label": current[k]} for k in current if k not in kept]
    for day in removed:
        db.run("DELETE FROM sheet_days WHERE sheet_id = :sid AND day_key = :key", sid=sid, key=day["key"])
        # Anyone scheduled on a day that no longer exists needs a new one;
        # the sheet page lists them.
        db.run("DELETE FROM assignments WHERE sheet_id = :sid AND day_key = :key", sid=sid, key=day["key"])
    return removed


def day_impact(sid, day_keys):
    """For a warning before days are removed: who ranked them, who left a
    note or a can't-do mark on them, and who is scheduled on them."""
    keys = set(day_keys)
    impact = {"ranked": 0, "notes": [], "cant": [], "scheduled": []}
    for r in get_submissions(sid):
        if keys & set(json.loads(r["ranking"])):
            impact["ranked"] += 1
        if keys & {k for k, v in json.loads(r["comments"]).items() if str(v).strip()}:
            impact["notes"].append(r["display_name"])
        if keys & set(json.loads(r["excluded_days"])):
            impact["cant"].append(r["display_name"])
    impact["scheduled"] = [a["display_name"] for a in get_assignments(sid) if a["day_key"] in keys]
    return impact


# ---------------------------------------------------------------------------
# Class list
# ---------------------------------------------------------------------------
def counts(sid):
    """Real students only — test students never count toward "X of Y".
    Rankings are split into those from names on the class list and those
    from anyone else, so "X of Y have ranked" can never exceed Y."""
    roster = db.row(
        """
        SELECT
            COALESCE(SUM(CASE WHEN is_test = 0 THEN 1 ELSE 0 END), 0) AS roster,
            COALESCE(SUM(CASE WHEN is_test = 0 AND email <> '' THEN 1 ELSE 0 END), 0) AS with_email,
            COALESCE(SUM(CASE WHEN is_test = 1 THEN 1 ELSE 0 END), 0) AS tests
        FROM roster WHERE sheet_id = :sid
        """,
        sid=sid,
    )
    ranked = db.row(
        """
        SELECT
            COALESCE(SUM(CASE WHEN r.name_key IS NOT NULL AND r.is_test = 0 THEN 1 ELSE 0 END), 0) AS listed,
            COALESCE(SUM(CASE WHEN r.name_key IS NULL THEN 1 ELSE 0 END), 0) AS unlisted,
            COALESCE(SUM(CASE WHEN r.is_test = 1 THEN 1 ELSE 0 END), 0) AS tests
        FROM submissions s
        LEFT JOIN roster r ON r.sheet_id = s.sheet_id AND r.name_key = s.name_key
        WHERE s.sheet_id = :sid
        """,
        sid=sid,
    )
    found = {
        "roster": int(roster["roster"]),
        "roster_with_email": int(roster["with_email"]),
        "test_students": int(roster["tests"]),
        "listed_submissions": int(ranked["listed"]),
        "unlisted_submissions": int(ranked["unlisted"]),
        "test_submissions": int(ranked["tests"]),
    }
    found["submissions"] = found["listed_submissions"] + found["unlisted_submissions"]
    found["waiting"] = found["roster"] - found["listed_submissions"]
    return found


def get_roster(sid):
    return db.rows(
        "SELECT name_key, display_name, email, is_test, name_pending FROM roster WHERE sheet_id = :sid "
        "ORDER BY is_test, lower(display_name)",
        sid=sid,
    )


def test_keys(sid):
    return {
        r["name_key"]
        for r in db.rows("SELECT name_key FROM roster WHERE sheet_id = :sid AND is_test = 1", sid=sid)
    }


def _pinned(sid):
    return {r["name_key"] for r in db.rows("SELECT name_key FROM name_pins WHERE sheet_id = :sid", sid=sid)}


def _signin_changes(before, after, pinned=()):
    """Name keys whose way of signing in differs between two {key: email}
    lists: taken off, or given a different email — and names someone had
    claimed with a PIN while they weren't listed, now listed with an email
    (so that sign-in has to be redone with an emailed code). Someone just
    put back on the list, with the email they had, isn't a change."""
    changed = []
    for key in set(before) | set(after):
        if key in before and (key not in after or before[key] != after[key]):
            changed.append(key)
        elif key not in before and after.get(key) and key in pinned:
            changed.append(key)
    return changed


def note_removed(sid, name_key, email, display_name):
    """A student the instructor took off the list by hand: the next update
    from Canvas leaves them off (compare_rosters' `took_off`). Not committed."""
    db.run("DELETE FROM removed_by_hand WHERE sheet_id = :sid AND name_key = :key", sid=sid, key=name_key)
    db.run("INSERT INTO removed_by_hand (sheet_id, name_key, email, display_name, at) "
           "VALUES (:sid, :key, :email, :name, :at)",
           sid=sid, key=name_key, email=email or "", name=display_name, at=iso())


def removed_by_hand(sid, listed=None):
    """Students taken off the list by hand who aren't back on it. The records
    of anyone back (added again, an undo, a restore) go here. `listed`: a
    whole class list from Canvas (or its file) just taken: the records of
    anyone it no longer has go too, since Canvas has caught up, so if they
    enrol again, they're new. Not committed."""
    rows = db.rows("SELECT * FROM removed_by_hand WHERE sheet_id = :sid ORDER BY at", sid=sid)
    if not rows:
        return []
    on = [r for r in get_roster(sid) if not r["is_test"]]
    keys, emails = {r["name_key"] for r in on}, {r["email"] for r in on if r["email"]}
    stale = [r for r in rows if r["name_key"] in keys or (r["email"] and r["email"] in emails)]
    if listed is not None:
        listed_keys, listed_emails = {s["name_key"] for s in listed}, {s["email"] for s in listed if s["email"]}
        bare = {s["name_key"] for s in listed if not s["email"]}  # (a list with no email for them still has them)
        stale += [r for r in rows if r not in stale and not (
            (r["email"] in listed_emails or r["name_key"] in bare) if r["email"] else r["name_key"] in listed_keys)]
    for r in stale:
        db.run("DELETE FROM removed_by_hand WHERE sheet_id = :sid AND name_key = :key", sid=sid, key=r["name_key"])
    return [r for r in rows if r not in stale]


def without_removed(students, took_off):
    """A new class list less the students compare_rosters says were taken
    off by hand (by email, which two students who share a name never do)."""
    emails = {s["email"] for s in took_off if s["email"]}
    keys = {s["name_key"] for s in took_off if not s["email"]}
    return [s for s in students if not (s["email"] in emails if s["email"] else s["name_key"] in keys)]


def renamed_by_email(current_rows, incoming):
    """Students a new list calls by another name, with the email the list
    already has for them (a preferred name, a new surname, in Canvas): the
    same email is the same student. (row, student) pairs."""
    by_email = {r["email"].lower(): r for r in current_rows if r["email"] and not r["is_test"]}
    out = []
    for s in incoming:
        row = by_email.pop((s["email"] or "").lower(), None)
        if row and not s.get("name_pending") and _same_name(row["display_name"]) != _same_name(s["display_name"]):
            out.append((row, s))
    return out


def as_known(students, renames):
    """A new list with each renamed student (renamed_by_email) under the name
    the list has for them."""
    old = {id(s): row for row, s in renames}
    return [{**s, "name_key": old[id(s)]["name_key"], "display_name": old[id(s)]["display_name"]}
            if id(s) in old else s for s in students]


def rename_students(sid, renames):
    """Give renamed students (renamed_by_email) their new name, moving their
    ranking, seat and PIN with them (rekey_student), unless the new name is
    someone else's already. Returns the pairs renamed. Not committed."""
    taken = {r["name_key"] for r in get_roster(sid)} | {r["name_key"] for r in get_submissions(sid)}
    done = []
    for row, s in renames:
        if s["name_key"] in taken or s["name_key"] == row["name_key"]:
            continue
        db.run("UPDATE roster SET name_key = :new, display_name = :name WHERE sheet_id = :sid AND name_key = :old",
               new=s["name_key"], name=s["display_name"], sid=sid, old=row["name_key"])
        rekey_student(sid, row["name_key"], s["name_key"], s["display_name"])
        taken.add(s["name_key"])
        done.append((row, s))
    return done


def compare_rosters(current_rows, new_students, removed=()):
    """How a new class list differs from the current one, for the review
    page shown before anything is replaced. Students are matched up by
    carry_over, as replace_roster does, so it shows just what Replace would
    do. `unsure`: names with fewer students on the new list, where nothing
    says which one left; Replace keeps all of them as they are. `removed`:
    removed_by_hand's records; those still on the new list are `took_off`,
    not new (they stay off unless the instructor puts them back)."""
    current = {r["name_key"]: r for r in current_rows if not r["is_test"]}
    tests = {r["name_key"] for r in current_rows if r["is_test"]}
    new_students = [s for s in new_students if s["name_key"] not in tests]
    # Renamed in Canvas (the same email): the same student, under the name
    # they have here until the list is updated (_apply_roster renames them).
    renames = renamed_by_email(current_rows, new_students)
    new_students = as_known(new_students, renames)
    carried, unsure = carry_over(current_rows, new_students)
    incoming = {s["name_key"]: s for s in carried}
    adding = [s for k, s in incoming.items() if k not in current]
    gone_emails = {r["email"] for r in removed if r["email"]}
    gone_keys = {r["name_key"]: r for r in removed}

    def was_removed(s):  # by email first: two students who share a name have different ones
        if s["email"] and s["email"] in gone_emails:
            return True
        return s["name_key"] in gone_keys and not (s["email"] and gone_keys[s["name_key"]]["email"])

    took_off = [s for s in adding if current and was_removed(s)]
    adding = [s for s in adding if s not in took_off]
    dropping = [r for k, r in current.items() if k not in incoming]
    taken = set(current) | tests
    same_person = [(row, s) for row, s in renames if s["name_key"] not in taken]
    # Already on the list: as many of each name as the new list has (an
    # unsure name keeps more of them than that).
    room = Counter(_same_name(s["display_name"]) for s in new_students)
    same = []
    for k, s in incoming.items():
        if k in current and room[_same_name(s["display_name"])] > 0:
            room[_same_name(s["display_name"])] -= 1
            same.append(s)
    return {
        "current": len(current),
        "incoming": len(new_students),
        "same": same,
        "adding": adding,
        "dropping": dropping,
        "renamed": same_person,
        "new_emails": [
            (current[k], s) for k, s in incoming.items()
            if k in current and s["email"] and s["email"] != current[k]["email"]
        ],
        # On the current list with an email, in the new one without: replacing
        # keeps the email we have rather than quietly dropping it.
        "keeps_email": [
            current[k] for k, s in incoming.items() if k in current and current[k]["email"] and not s["email"]
        ],
        "unsure": unsure,
        "took_off": took_off,
    }


def _drop_pins(sid, keys):
    """Students who now have an email sign in with a code; their old PIN
    would only confuse things."""
    for key in keys:
        db.run("DELETE FROM name_pins WHERE sheet_id = :sid AND name_key = :key", sid=sid, key=key)


def _same_name(display_name):
    """What students who share a name are listed under: "Sam Rivera (2)" ->
    "sam rivera". (Not base_key, which would put "Student 1" with "Student
    2".)"""
    return normalize_name(SAME_NAME_SUFFIX.sub("", display_name))


def _number(display_name):
    found = SAME_NAME_SUFFIX.search(display_name)
    return int(found.group(1)) if found else 1


def _by_elimination(rows, students):
    """Pairs (current row, new-list student) among those of one name that no
    email matched: first those with no email on both lists, then those with
    one on both, then the rest, in order. (So a newcomer with an email
    doesn't take the place of a student we know only by their PIN while
    someone with no email is there to be them.)"""
    pairs, rows, students = [], list(rows), list(students)
    for has_email in (False, True):
        found = list(zip([r for r in rows if bool(r["email"]) == has_email],
                         [s for s in students if bool(s["email"]) == has_email]))
        pairs += found
        rows = [r for r in rows if all(r is not row for row, _ in found)]
        students = [s for s in students if all(s is not new for _, new in found)]
    return pairs + list(zip(rows, students))


def carry_over(current_rows, incoming):
    """Who on a new class list is who on the current one. A student only
    ever keeps their own name, and with it their PIN, email and ranking:
    never another student's.

    Students who share a name ("Sam Rivera", "Sam Rivera (2)") are numbered
    in each list's own order, so a number says nothing about who is who. An
    email does; those left over pair up by elimination (_by_elimination),
    and any more on the new list are newcomers, numbered around everyone
    already on the list. When the new list has fewer left over and one of
    them has no email, nothing says who left: all of them on the current
    list stay just as they are, and the name (as shown, "Sam Rivera") is in
    `unsure`. Returns (students, unsure)."""
    groups = {}
    for r in current_rows:
        if not r["is_test"]:
            groups.setdefault(_same_name(r["display_name"]), []).append(r)
    new_groups = {}
    for s in incoming:
        new_groups.setdefault(_same_name(s["display_name"]), []).append(s)
    out, newcomers, unsure = [], [], []
    for name, new in new_groups.items():
        cur = sorted(groups.get(name, []), key=lambda r: _number(r["display_name"]))
        by_email = {r["email"]: r for r in cur if r["email"]}
        pairs, left = [], []
        for s in new:
            old = by_email.pop(s["email"], None) if s["email"] else None
            if old:
                pairs.append((old, s))
            else:
                left.append(s)
        free = [r for r in cur if all(r is not old for old, _ in pairs)]
        if left and len(left) < len(free) and not all(p["email"] for p in free + left):
            unsure.append(SAME_NAME_SUFFIX.sub("", cur[0]["display_name"]))
            out += [{k: r[k] for k in ("name_key", "display_name", "email", "name_pending")} for r in free]
            left = []
        else:
            found = _by_elimination(free, left)
            pairs += found
            left = [s for s in left if all(s is not new for _, new in found)]
        # The new list's spelling, unless that would change the student's name.
        out += [s if s["name_key"] == old["name_key"] else
                {**s, "name_key": old["name_key"], "display_name": old["display_name"]} for old, s in pairs]
        newcomers += left
    # Students already on the list have their names; a newcomer keeps the
    # one the new list gave them if no one has it (now or before), or else
    # gets the next free number.
    taken = {s["name_key"] for s in out} | {r["name_key"] for r in current_rows}
    clash = []
    for s in newcomers:
        if s["name_key"] in taken:
            clash.append(s)
        else:
            out.append(s)
            taken.add(s["name_key"])
    for s in clash:
        n = 2
        while normalize_name(numbered(s["display_name"], n)) in taken:
            n += 1
        name = numbered(s["display_name"], n)
        taken.add(normalize_name(name))
        out.append({**s, "name_key": normalize_name(name), "display_name": name})
    return out, unsure


def adopt_known_names(sid, students):
    """Students from a list that had only their email, matched by that email
    to someone already on this class's list: they keep the name they have
    (a student who typed theirs keeps it)."""
    known = {r["email"].lower(): r for r in get_roster(sid) if r["email"] and not r["is_test"]}
    adopted = []
    for s in students:
        here = known.get((s["email"] or "").lower()) if s.get("name_pending") else None
        adopted.append({**s, "name_key": here["name_key"], "display_name": here["display_name"],
                        "name_pending": here["name_pending"]} if here else s)
    return adopted


def replace_roster(sid, students):
    """Swap in a new class list. Test students stay put, each student keeps
    their own name (carry_over), and a student the new list has no email
    for keeps the one already on file: their own, never another's, since
    it's looked up by the name carry_over kept for them. Returns the name
    keys whose way of signing in changed, so their sign-ins can be ended."""
    current = get_roster(sid)
    before = {r["name_key"]: r["email"] for r in current if not r["is_test"]}
    pinned = _pinned(sid)
    taken = test_keys(sid)
    incoming, _ = carry_over(current, [s for s in students if s["name_key"] not in taken])
    incoming = [{**s, "email": s["email"] or before.get(s["name_key"], "")} for s in incoming]
    db.run("DELETE FROM roster WHERE sheet_id = :sid AND is_test = 0", sid=sid)
    db.run_many(
        "INSERT INTO roster (sheet_id, name_key, display_name, email, is_test, name_pending) "
        "VALUES (:sid, :name_key, :display_name, :email, 0, :name_pending)",
        [{"sid": sid, "name_pending": 0, **s} for s in incoming],
    )
    db.run(
        "UPDATE sheets SET roster_updated_at = :at, updated_at = :at WHERE id = :sid",
        at=iso(), sid=sid,
    )
    _drop_pins(sid, [s["name_key"] for s in incoming if s["email"]])
    return _signin_changes(before, {s["name_key"]: s["email"] for s in incoming}, pinned)


def add_to_roster(sid, students):
    """Add the names that aren't on the list yet, and fill in emails that
    were missing. Never changes or removes anything else. Returns (added,
    emails_filled_in, keys whose way of signing in changed).

    Students who share a name: where `students` has as many of a name as
    the list does (or more), who's who is carry_over's, just as the review
    page shows it, so a newcomer never takes anyone's name, and a student
    with no email gets one only by elimination ("Sam Rivera (2)", when the
    other Sam Rivera's email is there too). Where it has fewer (one typed
    in, or someone left), nothing says which of them a new email is, so
    it's someone new, numbered: never filled in for one of them."""
    rows = get_roster(sid)
    current = {r["name_key"]: r for r in rows}
    emails = {r["email"] for r in rows if r["email"]}
    have = Counter(_same_name(r["display_name"]) for r in rows if not r["is_test"])
    listed = Counter(_same_name(s["display_name"]) for s in students)
    whole = {name for name, n in listed.items() if 0 < have[name] <= n}
    unclear = {name for name, n in have.items() if n > 1 and listed[name] < n}
    students = carry_over(rows, [s for s in students if _same_name(s["display_name"]) in whole])[0] + [
        s for s in students if _same_name(s["display_name"]) not in whole]
    added, filled, changed = 0, 0, []
    for s in students:
        if s["email"] and any(r["email"] == s["email"] and base_key(r["name_key"]) == base_key(s["name_key"])
                              for r in current.values()):
            continue  # already on the list
        here = current.get(s["name_key"])
        email = s["email"] if s["email"] not in emails else ""
        if here is not None and not here["is_test"] and email and here["email"] != email and (
                here["email"] or _same_name(here["display_name"]) in unclear):
            # A different student with the same name (or nothing says it's this one): add them too, numbered.
            n = 2
            while normalize_name(numbered(s["display_name"], n)) in current:
                n += 1
            name = numbered(s["display_name"], n)
            s, here = {**s, "name_key": normalize_name(name), "display_name": name}, None
        if here is None:
            db.run(
                "INSERT INTO roster (sheet_id, name_key, display_name, email, is_test, name_pending) "
                "VALUES (:sid, :key, :name, :email, 0, :pending)",
                sid=sid, key=s["name_key"], name=s["display_name"], email=email,
                pending=s.get("name_pending", 0) if email else 0,
            )
            current[s["name_key"]] = {**s, "email": email, "is_test": 0}
            added += 1
        elif not here["is_test"] and email and not here["email"]:
            db.run(
                "UPDATE roster SET email = :email WHERE sheet_id = :sid AND name_key = :key",
                email=email, sid=sid, key=s["name_key"],
            )
            current[s["name_key"]] = {**here, "email": email}
            filled += 1
        else:
            continue
        if email:
            emails.add(email)
            changed.append(s["name_key"])
    db.run(
        "UPDATE sheets SET roster_updated_at = :at, updated_at = :at WHERE id = :sid",
        at=iso(), sid=sid,
    )
    _drop_pins(sid, changed)
    return added, filled, changed


def clear_roster(sid):
    """Remove the class list (test students stay). Returns the removed keys."""
    removed = [r["name_key"] for r in get_roster(sid) if not r["is_test"]]
    db.run("DELETE FROM roster WHERE sheet_id = :sid AND is_test = 0", sid=sid)
    db.run(
        "UPDATE sheets SET roster_updated_at = NULL, updated_at = :at WHERE id = :sid",
        at=iso(), sid=sid,
    )
    return removed


def add_test_students(sid, email, names=TEST_STUDENT_NAMES, tried=True):
    """Add the pretend students an instructor uses to try the student side
    (every new sheet starts with them: tried=False, so "See it as a student"
    isn't ticked off yet). Their codes go to `email`. Returns the names added."""
    for name in names:
        db.run(
            """
            INSERT INTO roster (sheet_id, name_key, display_name, email, is_test)
            VALUES (:sid, :key, :name, :email, 1)
            ON CONFLICT (sheet_id, name_key) DO UPDATE SET email = excluded.email, is_test = 1
            """,
            sid=sid, key=normalize_name(name), name=name, email=email,
        )
    if tried:
        db.run("UPDATE sheets SET tested_at = :at, updated_at = :at WHERE id = :sid", at=iso(), sid=sid)
    return list(names)


def remove_test_students(sid):
    """Remove the test students and everything they submitted. Returns
    their keys."""
    keys = test_keys(sid)
    for key in keys:
        for table in ("submissions", "assignments", "name_pins"):
            db.run(f"DELETE FROM {table} WHERE sheet_id = :sid AND name_key = :key", sid=sid, key=key)
        db.run("DELETE FROM login_codes WHERE scope = :sid AND subject = :key", sid=sid, key=key)
    db.run("DELETE FROM roster WHERE sheet_id = :sid AND is_test = 1", sid=sid)
    touch(sid)
    return keys


def rekey_student(sid, old_key, new_key, new_name):
    """Move everything filed under one name to another — for fixing a
    misspelled name, or linking a sign-up to the right person on the class
    list. The caller checks new_key is free. Not committed here."""
    if db.scalar("SELECT 1 FROM assignments WHERE sheet_id = :sid AND name_key = :old", sid=sid, old=old_key):
        # Their own seat wins over one handed out because the name hadn't ranked.
        db.run("DELETE FROM assignments WHERE sheet_id = :sid AND name_key = :new", sid=sid, new=new_key)
    for table in ("submissions", "assignments"):
        db.run(
            f"UPDATE {table} SET name_key = :new, display_name = :name WHERE sheet_id = :sid AND name_key = :old",
            new=new_key, name=new_name, sid=sid, old=old_key,
        )
    if db.scalar("SELECT 1 FROM name_pins WHERE sheet_id = :sid AND name_key = :new", sid=sid, new=new_key):
        db.run("DELETE FROM name_pins WHERE sheet_id = :sid AND name_key = :old", sid=sid, old=old_key)
    else:
        db.run(
            "UPDATE name_pins SET name_key = :new WHERE sheet_id = :sid AND name_key = :old",
            new=new_key, sid=sid, old=old_key,
        )
    db.run("DELETE FROM login_codes WHERE scope = :sid AND subject = :old", sid=sid, old=old_key)


# ---------------------------------------------------------------------------
# Rankings and the schedule
# ---------------------------------------------------------------------------
def get_submissions(sid):
    return db.rows(
        "SELECT * FROM submissions WHERE sheet_id = :sid ORDER BY lower(display_name)", sid=sid
    )


def get_assignments(sid):
    return db.rows(
        "SELECT * FROM assignments WHERE sheet_id = :sid ORDER BY lower(display_name)", sid=sid
    )


def schedule_inputs(sheet, viewer_is_test=False):
    """Everything a schedule is made from, as it stands right now.

    Test students' rankings count only until a real student has ranked (or
    when one of them is the one looking), so trying the site out never
    takes a real student's seat. `signature` changes whenever anything that
    would change the schedule does — that's how the sheet page knows a
    saved schedule is out of date.
    """
    sid = sheet["id"]
    days = get_days(sid)
    day_keys = [d["key"] for d in days]
    roster = get_roster(sid)
    tests = {r["name_key"] for r in roster if r["is_test"]}
    known = set(day_keys)
    # A ranking that mentions none of today's days (every day it listed was
    # removed) says nothing: treat that student as not having ranked.
    submissions = [r for r in get_submissions(sid) if known & set(json.loads(r["ranking"]))]
    real = [r for r in submissions if r["name_key"] not in tests]
    use_tests = viewer_is_test or not real
    rows = submissions if use_tests else real
    ranked = {r["name_key"] for r in rows}
    unranked = [
        (r["name_key"], r["display_name"]) for r in roster
        if not r["is_test"] and r["name_key"] not in ranked
    ] if sheet["include_unranked"] else []
    algorithm = sheet["algorithm"] if sheet["algorithm"] in ALGORITHMS else DEFAULT_ALGORITHM
    signature = hashlib.sha256(json.dumps({
        "capacity": sheet["capacity_per_day"],
        "algorithm": algorithm,
        "seed": sheet["lottery_seed"],
        "days": day_keys,
        "rankings": sorted(
            [r["name_key"], [d for d in json.loads(r["ranking"]) if d in known],
             sorted(d for d in json.loads(r["excluded_days"]) if d in known)]
            for r in rows
        ),
        "unranked": sorted(k for k, _ in unranked),
    }).encode()).hexdigest()[:20]
    return SimpleNamespace(
        days=days, day_keys=day_keys, rows=rows, students=build_student_prefs(rows, day_keys),
        unranked=unranked, algorithm=algorithm, signature=signature,
        tests_counted=bool(tests & ranked), tests_left_out=bool(tests) and not use_tests and any(
            r["name_key"] in tests for r in submissions
        ),
    )


def save_assignment(sid, results, algorithm, signature):
    at = iso()
    db.run("DELETE FROM assignments WHERE sheet_id = :sid", sid=sid)
    db.run_many(
        "INSERT INTO assignments (sheet_id, name_key, display_name, day_key, method, assigned_at) "
        "VALUES (:sid, :key, :name, :day, :method, :at)",
        [
            {"sid": sid, "key": key, "name": name, "day": day, "method": method, "at": at}
            for key, name, day, method in results
        ],
    )
    db.run(
        "UPDATE sheets SET last_algorithm = :algo, scheduled_at = :at, schedule_signature = :sig, "
        "updated_at = :at WHERE id = :sid",
        algo=algorithm, at=at, sig=signature, sid=sid,
    )


def schedule_rows(days, assignment_rows, submission_rows, roster_rows, capacity):
    """Everyone on the schedule, in day order, with what the instructor
    needs to know about each placement: which of their choices it was,
    whether it's a day they said they can't do, whether the day is over its
    seats, and their note about it."""
    labels = {d["key"]: d["label"] for d in days}
    order = {d["key"]: i for i, d in enumerate(days)}
    subs = {r["name_key"]: r for r in submission_rows}
    listed = {r["name_key"]: r for r in roster_rows}
    per_day = Counter(a["day_key"] for a in assignment_rows if a["day_key"] in labels)
    out = []
    for a in assignment_rows:
        day = a["day_key"]
        if day not in labels:
            continue
        sub = subs.get(a["name_key"])
        ranking = [d for d in json.loads(sub["ranking"]) if d in labels] if sub else []
        excluded = set(json.loads(sub["excluded_days"])) if sub else set()
        comments = json.loads(sub["comments"]) if sub else {}
        person = listed.get(a["name_key"])
        out.append({
            "key": a["name_key"],
            "name": a["display_name"],
            "day": day,
            "label": labels[day],
            "method": a["method"],
            "squeezed": a["method"] == OVERFLOW or (per_day[day] > capacity and a["method"] == MANUAL),
            "choice": ranking.index(day) + 1 if day in ranking else None,
            "cant": day in excluded,
            "over": per_day[day] > capacity,
            "note": str(comments.get(day, "")).strip(),
            "ranked": sub is not None,
            "is_test": bool(person and person["is_test"]),
            "email": person["email"] if person else "",
            "unlisted": bool(listed) and person is None,
        })
    out.sort(key=lambda r: (order[r["day"]], r["name"].lower()))
    return out


def needs_day(rankers, days, assignment_rows, capacity, scheduled_at=None):
    """For people who ranked but have no day: why, as it stands right now.
    `reason` is "late" (they ranked, or changed their ranking, after the
    schedule was made), "ruled-out" (they marked every day as one they
    can't do), "room" (a day they can do has a seat now — say, after seats
    per day went up), or "full" (every day they can do is full, so the
    instructor decides)."""
    labels = {d["key"]: d["label"] for d in days}
    load = Counter(a["day_key"] for a in assignment_rows if a["day_key"] in labels)
    out = []
    for r in rankers:
        excluded = set(json.loads(r["excluded_days"]))
        comments = json.loads(r["comments"])
        can_do = [d for d in json.loads(r["ranking"]) if d in labels and d not in excluded]
        can_do += [d for d in labels if d not in can_do and d not in excluded]
        room = [d for d in can_do if load[d] < capacity]
        if scheduled_at and r["updated_at"] > scheduled_at:
            reason = "late"
        elif not can_do:
            reason = "ruled-out"
        elif room:
            reason = "room"
        else:
            reason = "full"
        out.append({
            "key": r["name_key"],
            "name": r["display_name"],
            "reason": reason,
            "full": reason == "full",
            "room": [labels[d] for d in room],
            "cant": [labels[d] for d in labels if d in excluded],
            "notes": [(labels[d], str(comments[d]).strip()) for d in labels if str(comments.get(d, "")).strip()],
        })
    return out


def unplaced(assignment_rows, submission_rows, roster_rows, days, tests_counted):
    """Who has no day: (people who ranked, class-list students who didn't)."""
    known = {d["key"] for d in days}
    placed = {a["name_key"] for a in assignment_rows if a["day_key"] in known}
    tests = {r["name_key"] for r in roster_rows if r["is_test"]}
    ranked = {r["name_key"] for r in submission_rows}
    rankers = [
        r for r in submission_rows
        if r["name_key"] not in placed and (tests_counted or r["name_key"] not in tests)
    ]
    didnt_rank = [
        r for r in roster_rows
        if not r["is_test"] and r["name_key"] not in placed and r["name_key"] not in ranked
    ]
    return rankers, didnt_rank


def group_by_day(days, rows):
    """{day_key: [row]} from schedule_rows(), in the sheet's day order."""
    by_day = {d["key"]: [] for d in days}
    for r in rows:
        by_day[r["day"]].append(r)
    return by_day


# ---------------------------------------------------------------------------
# Spreadsheets, backups, and backup files
# ---------------------------------------------------------------------------
def export_sheet(sheet, include_pins=False):
    """Everything about one sheet, as plain data. PIN hashes are kept only
    in restore points stored here, never in files people download."""
    sid = sheet["id"]
    data = {
        "format": EXPORT_FORMAT,
        "exported_at": iso(),
        "sheet": {k: sheet.get(k) for k in SHEET_FIELDS},
        "days": get_days(sid),
        "roster": get_roster(sid),
        "submissions": [
            {k: r.get(k) for k in ("name_key", "display_name", "ranking", "excluded_days", "comments", "updated_at",
                                   "first_at")}
            for r in get_submissions(sid)
        ],
        "assignments": [
            {k: r[k] for k in ("name_key", "display_name", "day_key", "method", "assigned_at")}
            for r in get_assignments(sid)
        ],
    }
    if include_pins:
        data["pins"] = db.rows(
            "SELECT name_key, pin_hash, created_at FROM name_pins WHERE sheet_id = :sid", sid=sid
        )
    return data


def _cell(value):
    """Spreadsheet apps run any cell starting with = + - @ as a formula, so a
    student who signs up as "=HYPERLINK(...)" could booby-trap the export.
    A leading apostrophe makes such cells plain text."""
    text_value = "" if value is None else str(value)
    return "'" + text_value if text_value[:1] in ("=", "+", "-", "@", "\t", "\r") else text_value


def _csv(rows):
    """CSV text Excel opens correctly, accents included (the leading byte-
    order mark tells it the file is UTF-8)."""
    out = io.StringIO()
    writer = csv.writer(out)
    for row in rows:
        writer.writerow([_cell(v) for v in row])
    return "﻿" + out.getvalue()


def _day_names(keys, labels, order):
    return [labels[k] for k in sorted((k for k in keys if k in labels), key=order.get)]


def rankings_csv(days, submission_rows, roster_rows, zone):
    labels = {d["key"]: d["label"] for d in days}
    order = {d["key"]: i for i, d in enumerate(days)}
    listed = {r["name_key"]: r for r in roster_rows}
    rows = [["Student", "Email", "On your class list?", "Ranking (favorite first)", "Can't do",
             "Notes", "First ranked", "Last saved"]]
    for r in submission_rows:
        ranking = [labels[d] for d in json.loads(r["ranking"]) if d in labels]
        missing = [d["label"] for d in days if d["label"] not in ranking]
        if missing:
            ranking.append("(not ranked: " + ", ".join(missing) + ")")
        comments = json.loads(r["comments"])
        notes = " | ".join(
            f"{labels[d]}: {str(comments[d]).strip()}" for d in sorted(comments, key=lambda k: order.get(k, 999))
            if d in labels and str(comments[d]).strip()
        )
        person = listed.get(r["name_key"])
        on_list = "test student" if person and person["is_test"] else ("yes" if person else "no")
        rows.append([
            r["display_name"], person["email"] if person else "", on_list, " > ".join(ranking),
            "; ".join(_day_names(json.loads(r["excluded_days"]), labels, order)), notes,
            file_time(r.get("first_at") or r["updated_at"], zone), file_time(r["updated_at"], zone),
        ])
    return _csv(rows)


PLACED_HOW = {
    MANUAL: "Moved by you",
    UNRANKED: "Didn't rank — given an open seat",
    OVERFLOW: "From their ranking — every day they could do was full, so put on a full day",
    EXCLUDED: "On a day they said they can't do (an older schedule)",
}


def schedule_csv(days, assignment_rows, submission_rows, roster_rows, capacity):
    placed = schedule_rows(days, assignment_rows, submission_rows, roster_rows, capacity)
    tests = {r["name_key"] for r in roster_rows if r.get("is_test")}
    tests_counted = not any(r["name_key"] not in tests for r in submission_rows)
    rows = [["Day", "Student", "Email", "Their choice (1 = favorite)", "Heads-up", "How they got this day",
             "Their note about this day"]]
    for r in placed:
        heads_up = []
        if r["cant"]:
            heads_up.append("Said they can't do this day")
        if r["over"]:
            heads_up.append("Day is over its seat limit")
        if r["unlisted"]:
            heads_up.append("Not on your class list")
        if r["is_test"]:
            heads_up.append("Test student")
        if r["ranked"] and r["choice"] is None:
            heads_up.append("Day was added after they ranked")
        rows.append([
            r["label"], r["name"], r["email"], r["choice"] or "", "; ".join(heads_up),
            PLACED_HOW.get(r["method"], "From their ranking"), r["note"],
        ])
    rankers, didnt_rank = unplaced(assignment_rows, submission_rows, roster_rows, days, tests_counted)
    for r in rankers:
        rows.append(["(no day yet)", r["display_name"], "", "", "Ranked, but has no day — make the schedule again", "", ""])
    for r in didnt_rank:
        rows.append(["(no day yet)", r["display_name"], r["email"], "", "Didn't rank", "", ""])
    return _csv(rows)


def describe_export(data):
    """'3 rankings · 24 on the class list · 4 seats a day · schedule made (2 moved by hand)'."""
    tests = {r["name_key"] for r in data["roster"] if r.get("is_test")}
    rankings = sum(1 for s in data["submissions"] if s["name_key"] not in tests)
    listed = sum(1 for r in data["roster"] if not r.get("is_test"))
    parts = [plural(rankings, "ranking"), f"{listed} on the class list"]
    seats = data["sheet"].get("capacity_per_day")
    if seats:
        parts.append(f"{plural(seats, 'seat')} a day")
    if data["assignments"]:
        moved = sum(1 for a in data["assignments"] if a.get("method") == MANUAL)
        parts.append(("schedule published" if data["sheet"].get("published_at") else "schedule made")
                     + (f" ({moved} moved by hand)" if moved else ""))
    return " · ".join(parts)


def build_zip(data, reason, zone):
    """A backup of one sheet anyone can open. Returns (filename, bytes)."""
    sheet = data["sheet"]
    taken = in_zone(data["exported_at"], zone)
    stamp = taken.strftime("%Y-%m-%d-%I%M%S%p").lower()
    why = slugify(reason or "", fallback="")[:30]
    filename = f"{slugify(sheet['title'])}-backup-{stamp}{'-' + why if why else ''}.zip"
    capacity = sheet.get("capacity_per_day") or 1
    public = {k: v for k, v in data.items() if k != "pins"}

    files = {"rankings.csv": rankings_csv(data["days"], data["submissions"], data["roster"], zone)}
    if data["assignments"]:
        files["schedule.csv"] = schedule_csv(data["days"], data["assignments"], data["submissions"],
                                             data["roster"], capacity)
    real_roster = [r for r in data["roster"] if not r.get("is_test")]
    if real_roster:
        files["class-list.csv"] = _csv([["Name", "Email"]] + [[r["display_name"], r["email"]] for r in real_roster])
    files["sheet.json"] = json.dumps(public, indent=2)

    guide = {
        "rankings.csv": "everyone's rankings, can't-do days and notes",
        "schedule.csv": "the schedule, with each student's day",
        "class-list.csv": "your class list (names and emails)",
        "sheet.json": "the exact data, for putting it back",
    }
    readme = (
        f"Backup of “{sheet['title']}”\n"
        f"Taken: {file_time(data['exported_at'], zone)}\n"
        f"Why: {reason or 'downloaded'}\n"
        f"Holds: {describe_export(data)}\n\n"
        "What's inside:\n"
        + "".join(f"  {name} — {guide[name]}\n" for name in files)
        + "\nThe .csv files open in Excel, Numbers or Google Sheets.\n\n"
        + ("To put this back: on your list of sheets, use “Recently deleted” → “Bring it\n"
           "back” (for 30 days after deleting), or “Bring back a sheet from a backup\n"
           "file” and pick this .zip file (not the folder).\n"
           if (reason or "").startswith("Deleted") else
           "To put this back: open the sheet on the site, go to “Backups”, choose\n"
           "“Restore from a backup file”, and pick this .zip file (not the folder).\n")
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in files.items():
            z.writestr(name, content)
        z.writestr("README.txt", readme)
    return filename, buf.getvalue()


def read_backup_file(data):
    """Pull sheet.json back out of a backup .zip (or accept the bare JSON).
    Returns (export, problem): the export dict or None, and if None, a
    sentence saying what's wrong."""
    not_ours = (
        "That isn't a backup from this site. Choose the .zip file from “Download a backup” or a "
        "backup email. (If your computer unzipped it into a folder, choose the file called "
        "sheet.json inside that folder.)"
    )
    try:
        if data.startswith(b"PK\x03\x04"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if "sheet.json" not in z.namelist():
                    return None, not_ours
                info = z.getinfo("sheet.json")
                # A tiny zip can claim to unpack to gigabytes; the declared
                # size also caps what reading will actually produce.
                if info.file_size > MAX_BACKUP_JSON_BYTES:
                    return None, not_ours
                data = z.read(info)
        export = json.loads(data.decode("utf-8"))
    except Exception:
        return None, not_ours
    if not isinstance(export, dict) or export.get("format") != EXPORT_FORMAT:
        return None, not_ours
    if not _valid_export(export):
        return None, "This backup file looks edited or damaged, so it wasn't used. Try the original .zip file."
    export.pop("pins", None)  # PINs only ever come back from restore points stored here
    return export, ""


def _valid_export(export):
    """A backup file comes from the instructor's computer, so check its shape
    before any of it goes near the database."""
    def text(value, limit):
        if not isinstance(value, str) or len(value) > limit:
            return False
        try:
            value.encode("utf-8")  # no lone surrogates, which databases refuse
        except UnicodeEncodeError:
            return False
        return True

    def strings(item, keys, limit=2000):
        return isinstance(item, dict) and all(text(item.get(k), limit) for k in keys)

    def json_text(value, kind):
        try:
            return isinstance(json.loads(value), kind)
        except (TypeError, ValueError):
            return False

    try:
        sheet = export["sheet"]
        if not isinstance(sheet, dict) or not text(sheet.get("title"), 200):
            return False
        days, roster = export["days"], export["roster"]
        submissions, assignments = export["submissions"], export["assignments"]
        if not all(isinstance(x, list) for x in (days, roster, submissions, assignments)):
            return False
        if len(days) > MAX_DAYS or max(len(roster), len(submissions), len(assignments)) > 3000:
            return False
        day_keys = {d.get("key") for d in days if isinstance(d, dict)}
        return (
            all(strings(d, ("key", "label"), 100) and (d.get("date") is None or text(d.get("date"), 10))
                for d in days)
            and len(day_keys) == len(days)
            and all(strings(r, ("name_key", "display_name", "email"), 320) for r in roster)
            and all(
                strings(r, ("name_key", "display_name", "ranking", "excluded_days", "comments", "updated_at"), 20000)
                and (r.get("first_at") is None or text(r.get("first_at"), 40))
                and json_text(r["ranking"], list) and json_text(r["excluded_days"], list)
                and json_text(r["comments"], dict)
                for r in submissions
            )
            and all(
                strings(r, ("name_key", "display_name", "day_key", "method", "assigned_at"), 200)
                and r["day_key"] in day_keys
                for r in assignments
            )
        )
    except (KeyError, TypeError, AttributeError):
        return False


# ---------------------------------------------------------------------------
# Restore points
# ---------------------------------------------------------------------------
def _content_hash(data):
    """Identical content, identical hash. Timestamps (when someone last
    saved, when the schedule was made) are left out, so closing sign-ups
    twice or making the same schedule again doesn't look like a change."""
    settings_part = {k: data["sheet"].get(k) for k in RESTORED_SETTINGS if k not in ("scheduled_at", "schedule_signature")}
    body = {
        "days": data.get("days"),
        "roster": data.get("roster"),
        "submissions": sorted(
            [s["name_key"], s["display_name"], s["ranking"], s["excluded_days"], s["comments"]]
            for s in data.get("submissions") or []
        ),
        "assignments": sorted([a["name_key"], a["day_key"], a["method"]] for a in data.get("assignments") or []),
        "pins": sorted([p["name_key"], p["pin_hash"]] for p in data.get("pins") or []),
    }
    return hashlib.sha256(json.dumps([settings_part, body], sort_keys=True).encode()).hexdigest()[:32]


def take_snapshot(sheet, kind, reason):
    """Save a restore point — unless the newest one already holds exactly
    this, in which case that one's id is returned. Not committed here: it
    commits together with whatever change it's protecting against."""
    data = export_sheet(sheet, include_pins=True)
    content = _content_hash(data)
    if kind != "deleted":
        newest = db.row(
            "SELECT id, content_hash FROM snapshots WHERE sheet_id = :sid AND kind <> 'deleted' "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            sid=sheet["id"],
        )
        if newest and newest["content_hash"] == content:
            return newest["id"]
    snap_id = new_id(12)
    db.run(
        "INSERT INTO snapshots (id, owner_id, sheet_id, title, kind, reason, summary, content_hash, created_at, data) "
        "VALUES (:id, :owner, :sid, :title, :kind, :reason, :summary, :hash, :at, :data)",
        id=snap_id, owner=sheet["owner_id"], sid=sheet["id"], title=sheet["title"], kind=kind,
        # To the microsecond, so "the newest one" is never a coin toss
        # between two taken in the same second.
        reason=reason, summary=describe_export(data), hash=content, at=iso_precise(),
        data=json.dumps(data),
    )
    _prune_snapshots(sheet["id"])
    return snap_id


def _prune_snapshots(sid):
    """Keep at most SNAPSHOTS_KEPT restore points per sheet, dropping the
    oldest routine ones first."""
    kept = db.rows(
        "SELECT id, kind FROM snapshots WHERE sheet_id = :sid AND kind <> 'deleted' "
        "ORDER BY created_at DESC, id DESC",
        sid=sid,
    )
    extra = len(kept) - SNAPSHOTS_KEPT
    if extra <= 0:
        return
    oldest_first = list(reversed(kept))
    doomed = [r["id"] for r in oldest_first if r["kind"] in ROUTINE_SNAPSHOTS][:extra]
    if len(doomed) < extra:
        doomed += [r["id"] for r in oldest_first if r["id"] not in doomed][:extra - len(doomed)]
    for snap_id in doomed:
        db.run("DELETE FROM snapshots WHERE id = :id", id=snap_id)


def get_snapshot(snap_id, owner_id):
    """A restore point, but only for the instructor it belongs to."""
    return db.row(
        "SELECT * FROM snapshots WHERE id = :id AND owner_id = :owner", id=snap_id, owner=owner_id
    )


def recent_snapshots(sid, limit=SNAPSHOTS_KEPT):
    return db.rows(
        "SELECT id, kind, reason, summary, created_at FROM snapshots "
        "WHERE sheet_id = :sid AND kind <> 'deleted' ORDER BY created_at DESC, id DESC LIMIT :n",
        sid=sid, n=limit,
    )


def deleted_sheets(owner_id):
    """This instructor's deleted sheets that can still be brought back."""
    return db.rows(
        """
        SELECT id, sheet_id, title, summary, created_at FROM snapshots
        WHERE owner_id = :owner AND kind = 'deleted' AND created_at > :since
          AND sheet_id NOT IN (SELECT id FROM sheets)
        ORDER BY created_at DESC
        """,
        owner=owner_id, since=iso(now() - timedelta(days=DELETED_SHEETS_KEPT_DAYS)),
    )


def _ranking_content(s):
    return (json.loads(s["ranking"]), sorted(json.loads(s["excluded_days"])), json.loads(s["comments"]))


def compare_exports(current, other):
    """What going back to `other` would do to the rankings, by name."""
    now_by_key = {s["name_key"]: s for s in current["submissions"]}
    then_by_key = {s["name_key"]: s for s in other["submissions"]}
    since = other["exported_at"]
    made_since = [s["display_name"] for k, s in now_by_key.items() if k not in then_by_key]
    changed_since = [
        s["display_name"] for k, s in now_by_key.items()
        if k in then_by_key and _ranking_content(s) != _ranking_content(then_by_key[k])
        and s["updated_at"] > since
    ]
    differs_older = [
        s["display_name"] for k, s in now_by_key.items()
        if k in then_by_key and _ranking_content(s) != _ranking_content(then_by_key[k])
        and s["updated_at"] <= since
    ]
    # Days, by key (labels can change).
    now_days = {d["key"]: d["label"] for d in current["days"]}
    then_days = {d["key"]: d["label"] for d in other["days"]}
    day_changes = [f"“{then_days[k]}” comes back" for k in then_days if k not in now_days]
    day_changes += [f"“{now_days[k]}” goes away" for k in now_days if k not in then_days]
    day_changes += [f"“{now_days[k]}” is called “{then_days[k]}” again" for k in now_days
                    if k in then_days and now_days[k] != then_days[k]]
    # Who's on which day.
    now_seat = {a["name_key"]: a for a in current["assignments"]}
    then_seat = {a["name_key"]: a for a in other["assignments"]}
    label = lambda key: then_days.get(key) or now_days.get(key) or "?"  # noqa: E731
    seat_changes = []
    for key in sorted(set(now_seat) | set(then_seat), key=lambda k: (now_seat.get(k) or then_seat.get(k))["display_name"].lower()):
        a, b = now_seat.get(key), then_seat.get(key)
        name = (a or b)["display_name"]
        if a and not b:
            seat_changes.append(f"{name} loses their day ({label(a['day_key'])})")
        elif b and not a:
            seat_changes.append(f"{name} gets {label(b['day_key'])}")
        elif a["day_key"] != b["day_key"]:
            seat_changes.append(f"{name}: {label(a['day_key'])} → {label(b['day_key'])}")
    settings_changes = []
    if current["sheet"].get("capacity_per_day") != other["sheet"].get("capacity_per_day"):
        settings_changes.append(
            f"seats per day: {current['sheet'].get('capacity_per_day')} → {other['sheet'].get('capacity_per_day')}"
        )
    return {
        "made_since": sorted(made_since, key=str.lower),
        "changed_since": sorted(changed_since, key=str.lower),
        "differs_older": sorted(differs_older, key=str.lower),
        "comes_back": sorted(
            (s["display_name"] for k, s in then_by_key.items() if k not in now_by_key), key=str.lower
        ),
        "day_changes": day_changes,
        "seat_changes": seat_changes,
        "settings_changes": settings_changes,
        "takes_down_schedule": bool(current["sheet"].get("published_at")) and bool(current["assignments"])
                               and not other["assignments"],
        "now": describe_export(current),
        "then": describe_export(other),
        "same": _content_hash(current) == _content_hash(other),
    }


def restore(sheet_id, owner_id, data, keep_newer=True, parts=None):
    """Put a sheet back the way `data` (an export) has it.

    With keep_newer (the default), rankings made or changed after `data`
    was taken stay as they are now, so going back never quietly erases a
    student's newer ranking. `parts` limits it to some of: settings, days,
    roster, submissions, assignments. Whether sign-ups are open is never
    changed. Recreates the sheet (everything, PINs included) if it was
    deleted. Returns the name keys whose way of signing in changed. Not
    committed here.
    """
    parts = set(parts or ("settings", "days", "roster", "submissions", "assignments"))
    saved = data["sheet"]
    at = iso()
    current_owner = db.scalar("SELECT owner_id FROM sheets WHERE id = :sid", sid=sheet_id)
    if current_owner is not None and current_owner != owner_id:
        raise PermissionError("That sheet belongs to someone else.")
    recreating = current_owner is None
    if recreating:
        db.run(
            "INSERT INTO sheets (id, owner_id, title, bidding_open, lottery_seed, published_at, created_at, updated_at) "
            "VALUES (:sid, :owner, :title, :open, :seed, :published, :created, :at)",
            sid=sheet_id, owner=owner_id, title=str(saved.get("title") or "Untitled sign-up")[:100],
            open=1 if saved.get("bidding_open", 1) in (1, True, "1") else 0,
            seed=_int(saved.get("lottery_seed"), 0, 2_000_000_001) or new_lottery_seed(),
            published=saved.get("published_at") if isinstance(saved.get("published_at"), str) else None,
            created=saved.get("created_at") if isinstance(saved.get("created_at"), str) else at, at=at,
        )
        parts = {"settings", "days", "roster", "submissions", "assignments"}
        keep_newer = False
        if saved.get("progress_emails") in (0, False, "0"):  # (turned off before it was deleted: still off)
            db.run("UPDATE sheets SET progress_emails = 0 WHERE id = :sid", sid=sheet_id)
        # The same short link as before (it may be on a slide or a syllabus),
        # unless another class has it now. A backup brought back as a new
        # sheet has a new id, so it never takes one.
        code = saved.get("short_code")
        if saved.get("id") == sheet_id and _short_code_free(code):
            db.run("UPDATE sheets SET short_code = :c WHERE id = :sid", c=code, sid=sheet_id)

    if "settings" in parts:
        db.run(
            """
            UPDATE sheets SET title = :title, note = :note, rank_by = :rank_by,
                capacity_per_day = :capacity_per_day, algorithm = :algorithm,
                allow_unlisted = :allow_unlisted, show_preview = :show_preview,
                include_unranked = :include_unranked, last_algorithm = :last_algorithm,
                scheduled_at = :scheduled_at, schedule_signature = :schedule_signature
            WHERE id = :sid
            """,
            sid=sheet_id, **_restorable_settings(saved),
        )
    if "days" in parts:
        db.run("DELETE FROM sheet_days WHERE sheet_id = :sid", sid=sheet_id)
        db.run_many(
            "INSERT INTO sheet_days (sheet_id, day_key, label, sort_order, day_date) "
            "VALUES (:sid, :key, :label, :pos, :date)",
            [{"sid": sheet_id, "key": d["key"], "label": d["label"], "pos": i,
              "date": d["date"] if read_date(d.get("date")) else None} for i, d in enumerate(data["days"])],
        )
    changed = []
    if "roster" in parts:
        before = {r["name_key"]: r["email"] for r in get_roster(sheet_id) if not r["is_test"]}
        pinned = _pinned(sheet_id)
        db.run("DELETE FROM roster WHERE sheet_id = :sid", sid=sheet_id)
        rows = {}
        for r in data["roster"]:
            rows[r["name_key"]] = {
                "sid": sheet_id, "name_key": r["name_key"], "display_name": r["display_name"],
                "email": r.get("email") or "", "is_test": 1 if r.get("is_test") else 0,
                "name_pending": 1 if r.get("name_pending") and r.get("email") else 0,
            }
        db.run_many(
            "INSERT INTO roster (sheet_id, name_key, display_name, email, is_test, name_pending) "
            "VALUES (:sid, :name_key, :display_name, :email, :is_test, :name_pending)",
            rows.values(),
        )
        db.run(
            "UPDATE sheets SET roster_updated_at = :rat WHERE id = :sid",
            rat=(saved.get("roster_updated_at") or at) if rows else None, sid=sheet_id,
        )
        if "canvas_course_id" in saved:  # (restore points from before sheets knew their Canvas course don't say)
            db.run("UPDATE sheets SET canvas_course_id = :cid, canvas_course = :name WHERE id = :sid",
                   cid=saved.get("canvas_course_id") or None, name=saved.get("canvas_course") or None, sid=sheet_id)
        after = {k: r["email"] for k, r in rows.items() if not r["is_test"]}
        changed = _signin_changes(before, after, pinned)
        _drop_pins(sheet_id, [k for k, email in after.items() if email])
    if "submissions" in parts:
        merged = {s["name_key"]: s for s in data["submissions"]}
        if keep_newer:
            for s in get_submissions(sheet_id):
                if s["name_key"] not in merged or s["updated_at"] > data["exported_at"]:
                    merged[s["name_key"]] = s
        db.run("DELETE FROM submissions WHERE sheet_id = :sid", sid=sheet_id)
        db.run_many(
            "INSERT INTO submissions (sheet_id, name_key, display_name, ranking, excluded_days, comments, updated_at, "
            "first_at) VALUES (:sid, :name_key, :display_name, :ranking, :excluded_days, :comments, :updated_at, "
            ":first_at)",
            [{"sid": sheet_id, "first_at": s.get("first_at") or s["updated_at"],
              **{k: s[k] for k in ("name_key", "display_name", "ranking", "excluded_days", "comments", "updated_at")}}
             for s in merged.values()],
        )
    if "assignments" in parts:
        db.run("DELETE FROM assignments WHERE sheet_id = :sid", sid=sheet_id)
        rows = {r["name_key"]: r for r in data["assignments"]}
        db.run_many(
            "INSERT INTO assignments (sheet_id, name_key, display_name, day_key, method, assigned_at) "
            "VALUES (:sid, :name_key, :display_name, :day_key, :method, :assigned_at)",
            [{"sid": sheet_id, **{k: r[k] for k in ("name_key", "display_name", "day_key", "method",
                                                      "assigned_at")}} for r in rows.values()],
        )
        if not rows:
            # Nothing left to show students.
            db.run("UPDATE sheets SET published_at = NULL WHERE id = :sid", sid=sheet_id)
        elif saved.get("published_at") and isinstance(saved.get("published_at"), str):
            # That version's schedule was published: show it again (an undo
            # of "delete all rankings" shouldn't leave students in the dark).
            db.run("UPDATE sheets SET published_at = COALESCE(published_at, :at) WHERE id = :sid",
                   at=saved["published_at"], sid=sheet_id)
    if recreating and data.get("pins"):
        db.run_many(
            "INSERT INTO name_pins (sheet_id, name_key, pin_hash, created_at) VALUES (:sid, :key, :hash, :at) "
            "ON CONFLICT (sheet_id, name_key) DO NOTHING",
            [{"sid": sheet_id, "key": p["name_key"], "hash": p["pin_hash"], "at": p["created_at"]}
             for p in data["pins"]],
        )
    db.run("UPDATE sheets SET updated_at = :at WHERE id = :sid", at=at, sid=sheet_id)
    return changed


def delete_sheet(sheet):
    """Delete a sheet, keeping a full restore point (with its other restore
    points) for 30 days so a mistaken delete can be undone from the
    dashboard. Not committed here. Returns the restore point's id."""
    sid = sheet["id"]
    snap_id = take_snapshot(sheet, "deleted", "Deleted the sheet")
    # The foreign keys cascade too; being explicit doesn't depend on that.
    for table in ("assignments", "submissions", "name_pins", "roster", "sheet_days", "student_signouts",
                  "pending_uploads", "removed_by_hand"):
        db.run(f"DELETE FROM {table} WHERE sheet_id = :sid", sid=sid)
    db.run("DELETE FROM login_codes WHERE scope = :sid", sid=sid)
    db.run("DELETE FROM login_links WHERE scope = :sid", sid=sid)
    db.run("DELETE FROM auth_failures WHERE scope = :scope", scope=f"pin:{sid}")
    db.run("DELETE FROM sheets WHERE id = :sid", sid=sid)
    return snap_id


def copy_sheet(sheet, owner_id):
    """A fresh sheet with the same days and settings — for next term. No
    class list, rankings, or schedule come along. Not committed here."""
    sid = new_sheet_id()
    at = iso()
    db.run(
        """
        INSERT INTO sheets (id, owner_id, title, note, capacity_per_day, algorithm, bidding_open,
            allow_unlisted, show_preview, include_unranked, lottery_seed, theme, font, at_close, progress_emails,
            created_at, updated_at)
        VALUES (:sid, :owner, :title, :note, :capacity, :algorithm, 1, :unlisted, :preview, :unranked,
            :seed, :theme, :font, :at_close, :progress, :at, :at)
        """,
        progress=1 if sheet.get("progress_emails", 1) else 0,
        at_close=sheet["at_close"] or "ask",
        sid=sid, owner=owner_id, title=(sheet["title"] + " (copy)")[:100], note=sheet["note"],
        capacity=sheet["capacity_per_day"], algorithm=sheet["algorithm"],
        unlisted=sheet["allow_unlisted"], preview=sheet["show_preview"],
        unranked=sheet["include_unranked"], seed=new_lottery_seed(), theme=sheet["theme"], font=sheet["font"], at=at,
    )
    save_days(sid, [("", d["label"], d["date"]) for d in get_days(sheet["id"])])
    return sid


def _int(value, low, high, default=0, clamp=False):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    if clamp:
        return max(low, min(high, number))
    return number if low <= number <= high else default


def _restorable_settings(saved):
    """Settings from a backup, with anything missing or odd replaced by a
    sensible default rather than trusted."""
    def flag(name, default):
        return 1 if saved.get(name, default) in (1, True, "1") else 0

    def stamp(name):
        value = saved.get(name)
        return value if isinstance(value, str) and len(value) <= 40 else None

    algorithm = saved.get("algorithm") if saved.get("algorithm") in ALGORITHMS else DEFAULT_ALGORITHM
    last = saved.get("last_algorithm") if saved.get("last_algorithm") in ALGORITHMS else None
    return {
        "title": str(saved.get("title") or "Untitled sign-up")[:100],
        "note": str(saved.get("note") or "")[:2000],
        "rank_by": str(saved.get("rank_by") or "")[:100],
        "capacity_per_day": _int(saved.get("capacity_per_day"), 1, 500, 4, clamp=True),
        "algorithm": algorithm,
        "allow_unlisted": flag("allow_unlisted", 0),
        "show_preview": flag("show_preview", 0),
        "include_unranked": flag("include_unranked", 1),
        "last_algorithm": last,
        "scheduled_at": stamp("scheduled_at"),
        "schedule_signature": stamp("schedule_signature"),
    }
