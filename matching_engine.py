"""
Turning everyone's rankings into a schedule.

The heart of it is a peer-reviewed solver — the `matching` package's
Hospital/Residents game (many-to-one Gale–Shapley deferred acceptance), the
family of algorithm school-choice systems use to assign students to schools
with limited seats. Days have no opinions of their own, so each option only
controls how the random tie-breaking order is drawn.

Around the solver, three things it doesn't know about:

1. A day a student says they can't do is never given to them — not even
   if they marked every day. If every day they *can* do fills up (or they
   ruled out all of them), they're left for the instructor to place, with
   their notes — never squeezed in, and never swapped with a classmate.
   Moving other people around on someone's say-so would let a false "can't
   do" mark beat the draw; leaving it to a person, who can read their note,
   can't be gamed.
2. Students on the class list who never ranked get whatever seats are left
   — only open seats; anyone who doesn't fit stays off the schedule for the
   instructor to place.
3. The draw is fixed. Each student's place in line comes from the sheet's
   saved seed and their own name — never from who else ranked or what they
   marked — so the same rankings always give the same schedule, nobody can
   re-roll until they like the result, the student preview matches the real
   thing, and one student's can't-do marks can't reshuffle anyone else.

`fill_open_seats` does the same for a schedule students can already see:
it gives the open seats to people without a day and moves nobody.

Everything here is a pure function of its arguments: no database.
"""

import hashlib
import json
import time
from collections import Counter

DEFAULT_ALGORITHM = "da_independent"

ALGORITHMS = {
    "da_independent": {
        "label": "Fair draw for each day (recommended)",
        "short": "Fair draw (recommended)",
        "description": (
            "Everyone's list is considered at once, and each student gets a day as high on "
            "their list as possible. When more students want a day than it has seats, a "
            "random draw decides — a separate draw for each day. Students gain nothing by "
            "ranking dishonestly."
        ),
        "technical": (
            "Student-proposing deferred acceptance (resident-optimal) with an independent "
            "random priority order per day. Stable and strategy-proof for students."
        ),
    },
    "da_single_lottery": {
        "label": "One shared draw",
        "short": "One shared draw",
        "description": (
            "Students are put in one random order, and each, in turn, gets the best day still "
            "open on their list. Also fair; results are a little different."
        ),
        "technical": (
            "Deferred acceptance with a single random priority order shared by every day — "
            "equivalent to random serial dictatorship. Strategy-proof for students."
        ),
    },
    "da_day_favorable": {
        "compare_only": True,
        "label": "Comparison only (not recommended)",
        "short": "Comparison only",
        "description": (
            "A variation that is usually less generous to students' first choices. It's "
            "here so you can compare; most people never use it."
        ),
        "technical": (
            "Day-proposing deferred acceptance (hospital-optimal) with independent per-day "
            "priorities: the other end of the set of stable matchings. Not strategy-proof "
            "for students."
        ),
    },
}

# The options an instructor can actually use. The third is shown only on the
# Compare page: it isn't strategy-proof, and students are promised that
# honest ranking is always their best move.
CHOOSABLE = [k for k, v in ALGORITHMS.items() if not v.get("compare_only")]

COMPARE_TRIALS = 200
# A big class makes each solve slower; stop early rather than make the
# instructor wait (and spend the free plan's compute) on diminishing returns.
COMPARE_TIME_BUDGET_SECONDS = 8.0

# Methods: how each person ended up on their day.
PREFERENCE = "preference"   # from their own list
UNRANKED = "unranked"       # never ranked; given an open seat
OVERFLOW = "overflow"       # squeezed onto a full day (older schedules only; no longer made)
EXCLUDED = "excluded-forced"  # on a day they said they can't do (older schedules only; no longer made)
MANUAL = "manual"           # moved by the instructor
FLAGGED_METHODS = (EXCLUDED, OVERFLOW)


def build_student_prefs(submission_rows, day_keys):
    """Turn submission rows into what the solver needs: {key, name, pref,
    ranking, excluded}. `pref` lists only days the student can do — their
    ranked order first, then any days they didn't place. `ranking` is their
    full order, for the comparison stats."""
    known = set(day_keys)
    students = []
    for r in submission_rows:
        # Day keys that no longer exist (a day was removed after this student
        # ranked) are dropped, so a stale ranking can't break a run.
        ranking = [d for d in json.loads(r["ranking"]) if d in known]
        ranking += [d for d in day_keys if d not in ranking]
        excluded = {d for d in json.loads(r["excluded_days"]) if d in known}
        students.append({
            "key": r["name_key"],
            "name": r["display_name"],
            "pref": [d for d in ranking if d not in excluded],
            "ranking": ranking,
            "excluded": excluded,
        })
    return students


def _draw(seed, *parts):
    """A student's place in one random draw: fixed by the sheet's seed and
    the student (and day), never by who else is in it — so a classmate
    ranking, leaving, or marking days can't move anyone else's place."""
    return hashlib.sha256("|".join(str(p) for p in (seed, *parts)).encode()).digest()


def _solve(students, day_keys, seats, algorithm, seed):
    """Deferred acceptance over each student's acceptable days, with
    `seats` = {day: seats available}. Returns {key: day}."""
    from matching.games import HospitalResident  # numpy is slow to import; most pages never need it

    open_days = {d for d in day_keys if seats.get(d, 0) > 0}
    prefs = {s["key"]: [d for d in s["pref"] if d in open_days] for s in students}
    keys = [k for k, p in prefs.items() if p]
    if not keys:
        return {}
    shared = algorithm == "da_single_lottery"
    priority = {d: sorted(keys, key=lambda k: _draw(seed, "all" if shared else d, k)) for d in open_days}
    wants = {d: {k for k in keys if d in prefs[k]} for d in open_days}
    hospital_prefs = {d: [k for k in priority[d] if k in wants[d]] for d in open_days}
    used_days = [d for d in day_keys if d in open_days and hospital_prefs[d]]
    game = HospitalResident.create_from_dictionaries(
        {k: prefs[k] for k in keys},
        {d: hospital_prefs[d] for d in used_days},
        {d: seats[d] for d in used_days},
    )
    optimal = "hospital" if algorithm == "da_day_favorable" else "resident"
    assigned = {}
    for hospital, residents in game.solve(optimal=optimal).items():
        for resident in residents:
            assigned[resident.name] = hospital.name
    return assigned


def _place(students, day_keys, cap, algorithm, seed, unranked, existing):
    """Everyone in `students` and `unranked` who isn't in `existing`
    ({key: day}, never changed), placed as fairly as the open seats allow.
    Returns [(name_key, display_name, day_key, method)] for the new
    placements: people who ranked in list order, then the rest."""
    if algorithm not in ALGORITHMS:
        algorithm = DEFAULT_ALGORITHM
    seed = 0 if seed is None else seed
    counts = Counter(d for d in existing.values() if d in day_keys)
    waiting = [s for s in students if s["key"] not in existing]
    assigned = _solve(waiting, day_keys, {d: cap - counts[d] for d in day_keys}, algorithm, seed)
    method = {k: PREFERENCE for k in assigned}
    counts.update(assigned.values())

    # Never ranked: only open seats, the emptiest days first.
    names = {s["key"]: s["name"] for s in students}
    taken = set(existing) | set(assigned)
    late = sorted(((k, n) for k, n in unranked if k not in taken), key=lambda p: _draw(seed, "late", p[0]))
    placed_late = []
    for key, name in late:
        open_days = [d for d in day_keys if counts[d] < cap]
        if not open_days:
            break  # no seats left: the instructor decides what to do with the rest
        low = min(counts[d] for d in open_days)
        day = min((d for d in open_days if counts[d] == low), key=lambda d: _draw(seed, "late-day", key, d))
        assigned[key], method[key], names[key] = day, UNRANKED, name
        counts[day] += 1
        placed_late.append(key)

    order = [s["key"] for s in students if s["key"] in assigned] + placed_late
    return [(k, names[k], assigned[k], method[k]) for k in order]


def compute_assignment(students, day_keys, cap, algorithm=DEFAULT_ALGORITHM, seed=None, unranked=()):
    """Give everyone who can be placed fairly a day. `students` are those who
    ranked (from build_student_prefs); `unranked` is a list of (key, name)
    for class-list students who didn't. Returns [(name_key, display_name,
    day_key, method)]; anyone missing from it has no day yet and needs the
    instructor's decision."""
    if not day_keys or not (students or unranked):
        return []
    return _place(students, day_keys, cap, algorithm, seed, unranked, existing={})


def fill_open_seats(students, day_keys, cap, existing, algorithm=DEFAULT_ALGORITHM, seed=None, unranked=()):
    """For a schedule people may already have seen: give the open seats to
    whoever has no day — each person who ranked gets the highest day on
    their own list that still has a seat (never one they said they can't
    do; someone who ruled out every day stays for the instructor), then the
    rest get any open seat. Nobody in `existing` ({key: day}) moves. Returns
    the new placements, like compute_assignment."""
    if not day_keys:
        return []
    known = set(day_keys)
    return _place(students, day_keys, cap, algorithm, seed, unranked,
                  existing={k: d for k, d in existing.items() if d in known})


def compare_algorithms(students, day_keys, cap, trials=COMPARE_TRIALS,
                       time_budget=COMPARE_TIME_BUDGET_SECONDS):
    """Run every option repeatedly (a fresh draw each time) and return
    (stats, trials_run). Trials alternate between options, so if the time
    budget cuts things short they've all had the same number of runs."""
    by_key = {s["key"]: s for s in students}
    totals = {a: {"rank": 0, "top1": 0, "top2": 0, "cant": 0, "left": 0, "n": 0, "people": 0} for a in ALGORITHMS}
    started = time.monotonic()
    trials_run = 0
    while trials_run < trials:
        for algo_id in ALGORITHMS:
            t = totals[algo_id]
            results = compute_assignment(students, day_keys, cap, algorithm=algo_id,
                                         seed=trials_run * 7919 + len(algo_id))
            for key, _name, day, how in results:
                rank = by_key[key]["ranking"].index(day) + 1
                t["rank"] += rank
                t["top1"] += rank == 1
                t["top2"] += rank <= 2
                t["cant"] += how == EXCLUDED
                t["n"] += 1
            t["people"] += len(students)
            t["left"] += len(students) - len(results)
        trials_run += 1
        if time.monotonic() - started > time_budget:
            break

    stats = {}
    for algo_id, meta in ALGORITHMS.items():
        t = totals[algo_id]
        n = t["n"]
        stats[algo_id] = {
            "compare_only": bool(meta.get("compare_only")),
            "label": meta["label"],
            "short": meta["short"],
            "description": meta["description"],
            "avg_rank": round(t["rank"] / n, 2) if n else None,
            "pct_top1": round(100 * t["top1"] / n, 1) if n else None,
            "pct_top2": round(100 * t["top2"] / n, 1) if n else None,
            "pct_cant": round(100 * t["cant"] / n, 1) if n else None,
            "pct_left": round(100 * t["left"] / t["people"], 1) if t["people"] else None,
        }
    return stats, trials_run


def verdict(stats, current=DEFAULT_ALGORITHM):
    """One plain sentence about the comparison, aware of the option in use."""
    recommended = stats.get(DEFAULT_ALGORITHM) or {}
    rec_top1 = recommended.get("pct_top1") or 0
    best_id, best = max(stats.items(), key=lambda kv: kv[1]["pct_top1"] or 0)
    using = stats.get(current) or recommended
    if current != DEFAULT_ALGORITHM and rec_top1 - (using.get("pct_top1") or 0) >= 2:
        return (
            f"The recommended option gives more students their first choice ({rec_top1:g}% vs. "
            f"{using.get('pct_top1') or 0:g}%) — switch back to “{recommended.get('short', 'Fair draw')}”."
        )
    if (best["pct_top1"] or 0) - rec_top1 < 2:
        if current == DEFAULT_ALGORITHM:
            return "The recommended fair draw does as well as anything here for your class — keep it."
        return "These options do about the same for your class; the recommended fair draw is the safe choice."
    return (
        f"For your class, “{best['short']}” gives a few more students their first choice. "
        "Either is fair; the recommended one is the safe default."
    )
