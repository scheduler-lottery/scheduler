"""The assignment algorithms (pure functions — no database)."""

import json
from collections import Counter

from matching_engine import ALGORITHMS, build_student_prefs, compare_algorithms, compute_assignment

DAYS = ["a", "b", "c"]


def submission(key, ranking, excluded=()):
    return {
        "name_key": key, "display_name": key.title(),
        "ranking": json.dumps(list(ranking)), "excluded_days": json.dumps(list(excluded)),
    }


def test_everyone_gets_their_first_choice_when_it_fits():
    students = build_student_prefs([submission("ana", "abc"), submission("ben", "bca"), submission("cleo", "cab")], DAYS)
    for algorithm in ALGORITHMS:
        result = compute_assignment(students, DAYS, cap=1, algorithm=algorithm, seed=1)
        assert {k: d for k, _n, d, _m in result} == {"ana": "a", "ben": "b", "cleo": "c"}
        assert all(m == "preference" for *_rest, m in result)


def test_a_cant_do_day_is_never_given_even_when_it_has_room():
    students = build_student_prefs([submission("ana", "abc", excluded="a"), submission("ben", "abc")], ["a"])
    result = compute_assignment(students, ["a"], cap=2, seed=3)
    assert {k: m for k, _n, _d, m in result} == {"ben": "preference"}  # Ana is left for the instructor


def test_when_seats_run_out_the_extra_person_is_left_for_the_instructor():
    students = build_student_prefs([submission(k, "ab") for k in ("ana", "ben", "cleo")], ["a", "b"])
    result = compute_assignment(students, ["a", "b"], cap=1, seed=5)
    assert len(result) == 2  # nobody squeezed onto a full day
    assert all(m == "preference" for *_rest, m in result)


def test_stale_day_keys_are_ignored():
    students = build_student_prefs([submission("ana", ["gone", "b", "a"])], ["a", "b"])
    assert students[0]["pref"] == ["b", "a"]


def test_compare_reports_every_algorithm():
    students = build_student_prefs([submission(k, "abc") for k in ("ana", "ben", "cleo", "dev")], DAYS)
    stats, trials = compare_algorithms(students, DAYS, cap=2, trials=10)
    assert trials == 10
    assert set(stats) == set(ALGORITHMS)
    assert all(0 <= s["pct_top1"] <= 100 for s in stats.values())


def test_same_seed_same_schedule_and_different_seeds_can_differ():
    students = build_student_prefs([submission(k, "abc") for k in ("ana", "ben", "cleo", "dev", "eve", "fay")], DAYS)
    runs = {tuple(compute_assignment(students, DAYS, cap=2, seed=42)) for _ in range(5)}
    assert len(runs) == 1
    seen = {tuple(compute_assignment(students, DAYS, cap=2, seed=s)) for s in range(20)}
    assert len(seen) > 1


def test_cant_do_is_never_used_and_nobody_is_bumped_for_it():
    # One seat a day. Ana can't do "a"; Ben ranked "b" first. Whoever the draw
    # favors gets "b" — a can't-do mark never moves Ben — and if that's Ben,
    # Ana is left for the instructor rather than put on "a".
    outcomes = set()
    for seed in range(40):
        students = build_student_prefs([submission("ana", "ba", excluded="a"), submission("ben", "ba")], ["a", "b"])
        result = {k: d for k, _n, d, _m in compute_assignment(students, ["a", "b"], cap=1, seed=seed)}
        assert result.get("ana") != "a", seed
        outcomes.add(tuple(sorted(result.items())))
    assert outcomes == {(("ana", "b"), ("ben", "a")), (("ben", "b"),)}


def test_false_cant_do_marks_dont_beat_the_draw():
    # Everyone wants "b"; the draw gives it to someone. Marking every other
    # day can't-do must never take it away from them.
    for seed in range(40):
        honest = build_student_prefs([submission(k, "bac") for k in ("ana", "ben", "cy")], DAYS)
        winner = next(k for k, _n, d, _m in compute_assignment(honest, DAYS, cap=1, seed=seed) if d == "b")
        loser = next(k for k in ("ana", "ben", "cy") if k != winner)
        gaming = [submission(k, "bac", excluded="ac" if k == loser else "") for k in ("ana", "ben", "cy")]
        result = {k: d for k, _n, d, _m in compute_assignment(build_student_prefs(gaming, DAYS), DAYS, cap=1, seed=seed)}
        assert result.get(loser) != "b" or result.get(winner) == "b", seed


def test_a_student_who_rules_out_every_day_is_left_for_the_instructor():
    from matching_engine import fill_open_seats

    students = build_student_prefs([submission("ana", "ab", excluded="ab")], ["a", "b"])
    assert compute_assignment(students, ["a", "b"], cap=1, seed=1) == []
    assert fill_open_seats(students, ["a", "b"], 1, {}, seed=1) == []


def test_unranked_students_fill_open_seats_after_everyone_who_ranked():
    students = build_student_prefs([submission("ana", "ab"), submission("ben", "ab")], ["a", "b"])
    with_late = compute_assignment(students, ["a", "b"], cap=2, seed=7, unranked=[("zed", "Zed"), ("yan", "Yan")])
    without = compute_assignment(students, ["a", "b"], cap=2, seed=7)
    # Placing the late students never moves anyone who ranked.
    assert [r for r in with_late if r[0] in ("ana", "ben")] == without
    late = {k: (d, m) for k, _n, d, m in with_late if k in ("zed", "yan")}
    assert all(m == "unranked" for _d, m in late.values())
    counts = {}
    for _k, _n, d, _m in with_late:
        counts[d] = counts.get(d, 0) + 1
    assert counts == {"a": 2, "b": 2}


def test_verdict_is_plain():
    from matching_engine import verdict
    students = build_student_prefs([submission(k, "abc") for k in ("ana", "ben", "cleo", "dev")], DAYS)
    stats, _ = compare_algorithms(students, DAYS, cap=2, trials=10)
    assert "recommended" in verdict(stats)


def _rank_of(student, day):
    return student["ranking"].index(day)


def test_marking_every_day_cant_do_never_moves_anyone_else():
    # The draw is fixed per student, so one student's marks can only ever
    # free up room for others — never re-roll the draw against them.
    import random

    days = ["a", "b", "c", "d"]
    for trial in range(60):
        rnd = random.Random(trial)
        keys = [f"s{i}" for i in range(rnd.randint(4, 9))]
        rows = [submission(k, rnd.sample(days, len(days)), excluded=rnd.sample(days, rnd.randint(0, 2))) for k in keys]
        cap = rnd.randint(1, 2)
        for algorithm in ("da_independent", "da_single_lottery"):
            honest = build_student_prefs(rows, days)
            before = {k: d for k, _n, d, _m in compute_assignment(honest, days, cap, algorithm=algorithm, seed=trial)}
            gamer = rnd.choice(keys)
            marked = [submission(r["name_key"], json.loads(r["ranking"]), excluded=days if r["name_key"] == gamer
                                 else json.loads(r["excluded_days"])) for r in rows]
            after = {k: d for k, _n, d, _m in compute_assignment(build_student_prefs(marked, days), days, cap,
                                                                 algorithm=algorithm, seed=trial)}
            by_key = {s["key"]: s for s in honest}
            for k, day in before.items():
                if k == gamer or day in json.loads(next(r for r in rows if r["name_key"] == k)["excluded_days"]):
                    continue
                assert k in after, (trial, algorithm, k)
                assert _rank_of(by_key[k], after[k]) <= _rank_of(by_key[k], day), (trial, algorithm, k)


def test_a_newcomer_on_other_days_leaves_everyone_else_alone():
    days = ["a", "b", "c"]
    rows = [submission(k, "abc") for k in ("ana", "ben", "cy", "dee")]
    for seed in range(30):
        before = compute_assignment(build_student_prefs(rows, days), days, 2, seed=seed)
        after = compute_assignment(build_student_prefs(rows + [submission("zed", "cba", excluded="ab")], days),
                                   days, 2, seed=seed)
        assert [r for r in after if r[0] != "zed"] == before, seed


def test_filling_open_seats_moves_nobody():
    from matching_engine import fill_open_seats

    days = ["a", "b"]
    students = build_student_prefs([submission("ana", "ab"), submission("ben", "ba"), submission("cy", "ab", excluded="b"),
                                    submission("dee", "ab", excluded="ab")], days)
    existing = {"ana": "a", "ben": "a"}  # say the instructor moved Ben by hand
    new = fill_open_seats(students, days, 3, existing, seed=4, unranked=[("zed", "Zed"), ("yan", "Yan")])
    placed = {k: (d, m) for k, _n, d, m in new}
    assert "ana" not in placed and "ben" not in placed
    assert placed["cy"] == ("a", "preference")  # the one day Cy can do
    assert "dee" not in placed  # ruled out every day: the instructor decides
    counts = Counter(existing.values())
    counts.update(d for d, _m in placed.values())
    assert max(counts.values()) <= 3
    assert sum(1 for d, m in placed.values() if m == "unranked") == 2  # both fit in the 3 seats left


def test_filling_never_uses_a_cant_do_day_for_someone_with_options():
    from matching_engine import fill_open_seats

    days = ["a", "b"]
    students = build_student_prefs([submission("cy", "ab", excluded="b")], days)
    assert fill_open_seats(students, days, 1, {"x": "a"}, seed=1) == []  # only "b" has room, and Cy can't do it
