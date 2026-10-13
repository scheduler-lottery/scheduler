"""The Canvas step as a professor reads it: words for their kind of computer,
pages that say what really happens, and warnings only when something is
really wrong (from walking it through as professors who don't know tech)."""

import re

import settings
from test_app import _connectable_canvas, follow, raw, text


def test_a_section_named_in_words_or_by_the_same_number_is_no_warning(prof):
    analytics = "Student Name,Student ID,Email,Section Name\n"
    rows = "Ana Lima,1,ana@school.edu,{0}\nBo Chen,2,bo@school.edu,{0}\n"
    # A title in words: nothing to compare Canvas's section code with.
    sid = prof.create_sheet(title="Evidence Seminar")
    page = follow(prof, prof.upload(sid, analytics + rows.format("LAW 540 SEC20")))
    assert "Added 2 students" in page and "report-warn" not in page
    # The same course number, written Canvas's way.
    sid = prof.create_sheet(title="LAW 540 Evidence")
    page = follow(prof, prof.upload(sid, analytics + rows.format("2026FA_LAW_540-0_SEC20")))
    assert "report-warn" not in page
    # A different course number: the wrong course's list, likely.
    sid = prof.create_sheet(title="LAW 540 Evidence")
    page = follow(prof, prof.upload(sid, analytics + "Ana Lima,1,ana@school.edu,LAW 610 SEC1\n"
                                                     "Bo Chen,2,bo@school.edu,LAW 610 SEC2\n"))
    assert "report-warn" in page and "sections “LAW 610 SEC1”, “LAW 610 SEC2”" in page
    assert "Every row" not in page


def test_the_canvas_step_has_words_for_each_kind_of_computer(prof):
    sid = prof.create_sheet(allow_unlisted=False)
    page = raw(prof.get(f"/teach/s/{sid}/setup?step=students"))
    for marker in ("data-phone-only", "data-desktop-only", "data-safari-only"):
        assert marker in page
    # The box says which box it is, so a wrong file comes back to it.
    assert f'name="box" value="canvas-file-{sid}"' in page
    assert f'aria-labelledby="canvas-file-{sid}-title"' in page
    words = text(page)
    assert "its name ends in .zip" in words and "Downloads" in words
    assert "Drag in" not in words and "Drag in" not in text(prof.get("/"))
    # The grades in Canvas's file: said before the file is chosen, not after.
    assert words.index("It has grades in it too") < words.index("here to choose Canvas's file")


def test_the_pages_about_canvas_say_what_happens_on_this_site(prof, monkeypatch):
    privacy = text(prof.get("/privacy"))
    assert "comes to this site as soon as the instructor chooses it" in privacy
    assert "Scheduler Helper" in privacy and "Connect Canvas" not in privacy  # the helper is in Chrome's store
    assert "isn't offered on this site yet" not in text(prof.get("/helper"))
    monkeypatch.setattr(settings, "CANVAS_HELPER_IDS", [])
    assert "Scheduler Helper" not in text(prof.get("/privacy"))
    assert "isn't offered on this site yet" in text(prof.get("/helper"))


def test_connect_canvas_shows_only_for_the_school_whose_key_this_site_has(prof, monkeypatch):
    _connectable_canvas(monkeypatch)
    sid = prof.create_sheet(allow_unlisted=False)
    page = raw(prof.get(f"/teach/s/{sid}/setup?step=students"))
    assert 'data-connect-host="canvas.test"' in page
    assert re.search(r"<section[^>]*data-connect[^>]*>", page) and "Authorize" in text(page)
    assert "Canvas says that for every app" in text(page)  # what Authorize means, before Canvas asks
    r = prof.post_json("/teach/canvas/school", {"domain": "canvas.other.edu", "name": "Other University"})
    assert r.status_code == 200, raw(r)
    page = raw(prof.get(f"/teach/s/{sid}/setup?step=students"))
    section = re.search(r"<section[^>]*data-connect[^>]*>", page).group(0)
    assert " hidden" in section  # another school's professor isn't sent to this one's Canvas


def test_connect_canvas_brings_the_course_the_sheet_is_named_for(prof, monkeypatch):
    from test_app import _authorize, q, told_course

    canvas, fake = _connectable_canvas(monkeypatch)
    sid = prof.create_sheet(title="LAW 540 Evidence", allow_unlisted=False)
    back = _authorize(canvas, prof.get(f"/teach/s/{sid}/canvas/connect").headers["Location"])
    r = prof.get(back.path + "?" + back.query)
    assert r.headers["Location"].endswith(f"/teach/s/{sid}#class-list")  # no course to pick: straight to the list
    assert q(prof.app, "SELECT COUNT(*) FROM roster WHERE sheet_id = :sid AND is_test = 0", sid=sid) == 8
    assert told_course(prof.get(r.headers["Location"]))["id"] == "202"  # (for the browser; not kept here)
    assert not fake.TOKENS  # the permission given back
    # A name that doesn't say which course: the list of courses, to pick from.
    other = prof.create_sheet(title="Seminar on Privacy", allow_unlisted=False)
    back = _authorize(canvas, prof.get(f"/teach/s/{other}/canvas/connect").headers["Location"])
    assert prof.get(back.path + "?" + back.query).headers["Location"].endswith("/teach/canvas/oauth/pick")


def test_a_list_from_canvas_without_emails_is_not_taken_for_the_wrong_file(prof):
    import json

    sid = prof.create_sheet(allow_unlisted=False)
    prof.upload(sid, "Name,Email\nAna Lima,ana@school.edu\n")  # a list already there: the next one is looked over
    listed = json.dumps({"type": "scheduler-class-list", "v": 2, "courseId": "77", "course": "LAW 540",
                         "students": [{"name": "Ana Lima", "email": ""}, {"name": "Bo Chen", "email": ""}]})
    r = prof.post(f"/teach/s/{sid}/canvas-list", {"list": listed, "how": "helper"})
    page = text(prof.get(r.headers["Location"]))
    assert "no Name or Email column" not in page


def test_signed_out_in_another_tab_says_so_and_comes_back_to_the_sheet(prof):
    import json

    sid = prof.create_sheet(allow_unlisted=False)
    page = f"http://localhost/teach/s/{sid}/setup?step=students"
    csrf = prof.csrf()
    prof.post("/teach/logout")  # (another tab)
    listed = json.dumps({"type": "scheduler-class-list", "v": 2, "courseId": "202", "course": "LAW 540",
                         "students": [{"name": "Ana Lima", "email": "ana@school.edu"}]})
    r = prof.client.post(f"/teach/s/{sid}/canvas-list", data={"csrf_token": csrf, "list": listed},
                         headers={"Referer": page})
    assert r.headers["Location"].endswith("/teach/login")
    assert "your class list wasn't added yet" in text(prof.get(r.headers["Location"]))
    r = prof.post("/teach/login", {"email": "prof@school.edu"})
    r = prof.post("/teach/verify", {"code": prof.last_code("prof@school.edu")})
    assert r.headers["Location"].endswith(f"/teach/s/{sid}/setup?step=students")  # straight back to the sheet
    # A link from somewhere else is never followed back.
    prof.post("/teach/logout")
    prof.client.post(f"/teach/s/{sid}/canvas-list", data={"csrf_token": prof.csrf(), "list": listed},
                     headers={"Referer": "https://evil.example/teach/s/x"})
    prof.post("/teach/login", {"email": "prof@school.edu"})
    r = prof.post("/teach/verify", {"code": prof.last_code("prof@school.edu")})
    assert r.headers["Location"].endswith("/teach/")


def test_after_a_list_the_helper_brought_the_report_offers_to_remove_it(prof):
    import json

    sid = prof.create_sheet(allow_unlisted=False)
    listed = json.dumps({"type": "scheduler-class-list", "v": 2, "courseId": "202", "course": "LAW 540",
                         "students": [{"name": "Ana Lima", "email": "ana@school.edu"}]})
    r = prof.post(f"/teach/s/{sid}/canvas-list", {"list": listed, "how": "helper", "setup": "1"})
    page = raw(prof.get(r.headers["Location"]))
    # The checklist, its first two steps done; the last one, "All done: delete it?", in one click.
    assert "data-helper-remove" in page and "Keep it for next time" in page
    assert "Delete Scheduler Helper" in text(page) and page.count('class="helper-check is-done"') == 2
    assert "data-helper-remove" not in raw(prof.get(r.headers["Location"]))  # (said once, with its report)
    # A list from Canvas's file: nothing to remove.
    other = prof.create_sheet(allow_unlisted=False)
    page = raw(follow(prof, prof.upload(other, "Name,Email\nBo Chen,bo@school.edu\n")) and prof.get(f"/teach/s/{other}"))
    assert "data-helper-remove" not in page


def test_in_safari_or_firefox_the_step_says_how_to_open_it_in_chrome(prof):
    sid = prof.create_sheet(allow_unlisted=False)
    page = raw(prof.get(f"/teach/s/{sid}/setup?step=students"))
    words = text(page)
    assert "data-helper-chrome" in page and "Copy this page's link" in words and "it emails you a new code" in words
    assert "Paste and go" in words and "click Edit" in words  # (Windows, and a Mac: shown by app.js)
    assert "https://www.google.com/chrome/" in page
    for gone in ("App Store", "Always Allow", "data-helper-safari", "data-helper-reload", "Add Scheduler Helper to Safari"):
        assert gone not in page  # (no Safari helper: Safari's words are only about Canvas's file)
    # The link, pasted into Chrome before signing in there: back on this very step once signed in.
    prof.post("/teach/logout")
    assert prof.get(f"/teach/s/{sid}/setup?step=students").headers["Location"].endswith("/teach/login")
    prof.post("/teach/login", {"email": "prof@school.edu"})
    r = prof.post("/teach/verify", {"code": prof.last_code("prof@school.edu")})
    assert r.headers["Location"].endswith(f"/teach/s/{sid}/setup?step=students")


def test_the_store_build_is_chromes_alone_and_asks_for_nothing_new(tmp_path, monkeypatch):
    import importlib.util
    import json
    import zipfile

    spec = importlib.util.spec_from_file_location("build_extension", "tools/build_extension.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    monkeypatch.setattr(build, "DIST", str(tmp_path))
    build.build("store")
    with zipfile.ZipFile(tmp_path / "scheduler-helper.zip") as z:
        manifest = json.loads(z.read("manifest.json"))
        code = z.read("background.js").decode()
    assert manifest["version"] == "1.1.0" and manifest["permissions"] == ["scripting"]
    assert manifest["action"]["default_popup"] == "popup.html"  # (its own box: "Delete me"; no permission needed)
    # No website until the professor allows their school's Canvas, in its own window (allow.html): adding it asks
    # nothing, and no warning names anyone else's Canvas.
    assert "host_permissions" not in manifest and manifest["optional_host_permissions"] == ["https://*/*"]
    assert len(manifest["description"]) <= 132  # (the store's limit)
    assert "background" in manifest and list(manifest["background"]) == ["service_worker"]
    assert "safari" not in code.lower() and "allow.html" in code and "chrome.storage" not in code
    assert "uninstallSelf" in code  # (needs no permission of its own)
