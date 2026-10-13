"""The mobile-forward pass and its review: each thing said once on a screen,
one name per thing, and the pieces a phone relies on (the tab bar's More,
Share counting as sharing, cards for the professor's tables)."""

import re

from test_app import raw, text


def test_the_front_page_says_instructor_once_and_sign_in_once(browser):
    page = text(raw(browser().get("/")).split('<main')[1].split('</main>')[0])
    assert page.lower().count("instructor") == 1, page  # (the "I'm an instructor" card)
    assert page.count("get started or sign in") + page.count("Get started or sign in") == 1


def test_the_more_window_has_a_name_nothing_else_on_a_page_uses(prof):
    sid = prof.create_sheet()
    prof.upload(sid)
    for path in (f"/teach/s/{sid}", f"/teach/s/{sid}/settings", "/teach/"):
        page = raw(prof.get(path))
        assert len(re.findall(r'id="account"', page)) == 1
        assert 'data-popover-target="settings"' not in page  # (the Settings tab's own card is #settings)


def test_share_from_the_tab_bar_counts_as_sharing_and_waits_for_students(prof):
    sid = prof.create_sheet(allow_unlisted=False)
    assert "data-share-url" not in raw(prof.get(f"/teach/s/{sid}"))  # (nobody to share it with yet)
    prof.upload(sid)
    page = raw(prof.get(f"/teach/s/{sid}"))
    tab = re.search(r'<button type="button" class="tabbar-item"[^>]*data-share-url[^>]*>', page).group(0)
    assert f'data-mark-shared="/teach/s/{sid}/shared"' in tab and "data-share-text=" in tab


def test_status_words_match_between_the_dashboard_and_the_sheet(prof):
    sid = prof.create_sheet(allow_unlisted=False)
    assert ">Needs students<" in raw(prof.get("/teach/")) and ">Needs students<" in raw(prof.get(f"/teach/s/{sid}"))
    prof.upload(sid)
    assert ">Sign-ups open<" in raw(prof.get("/teach/")) and ">Sign-ups open<" in raw(prof.get(f"/teach/s/{sid}"))
    assert "Pending" not in text(prof.get("/teach/"))


def test_messages_share_on_a_phone_and_say_so(prof):
    sid = prof.create_sheet()
    prof.upload(sid)
    page = raw(prof.get(f"/teach/s/{sid}/setup?step=share"))
    assert re.search(r'data-copy="#announcement" data-share-message[^>]*>.*?Share the message', page, re.S)


def test_the_sheet_header_is_short_on_the_schedule_and_settings_tabs(prof):
    sid = prof.create_sheet()
    prof.upload(sid)
    assert 'class="card sheet-head on-class"' in raw(prof.get(f"/teach/s/{sid}"))
    assert 'class="card sheet-head"' in raw(prof.get(f"/teach/s/{sid}/schedule"))


def test_the_phone_top_bar_names_the_class_only_on_a_class_s_own_pages(prof, browser):
    sid = prof.create_sheet()
    prof.upload(sid)
    assert re.search(r'<span class="topbar-title">CS &amp; Law', raw(browser().get(f"/c/{sid}")))
    assert '<span class="topbar-title" aria-hidden="true"></span>' in raw(prof.get(f"/teach/s/{sid}"))  # (its h1 says it)


def test_the_stylesheet_keeps_hover_to_devices_that_hover():
    css = open("public/static/style.css").read()
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    depth, at_hover, outside = 0, [], []
    for token in re.finditer(r"@media[^{]*\{|[^{}]*\{|\}", css):
        chunk = token.group(0)
        if chunk == "}":
            depth -= 1
            if at_hover and at_hover[-1] == depth:
                at_hover.pop()
            continue
        if chunk.startswith("@media"):
            if "hover: hover" in chunk:
                at_hover.append(depth)
        elif ":hover" in chunk and not at_hover and depth == 0:
            outside.append(chunk.strip()[:60])
        depth += 1
    assert outside == []


def test_making_the_schedule_says_how_many_got_their_first_choice_and_never_that_everyone_has_a_day_when_not(
        prof, browser):
    from conftest import day_keys

    from test_app import follow

    sid = prof.create_sheet(capacity=1, days=("Oct 27", "Nov 10"), allow_unlisted=False)
    prof.upload(sid, "Name,Email\nAna Lima,ana@school.edu\nBo Chen,bo@school.edu\nCy Cole,cy@school.edu\n")
    days = day_keys(sid)
    for name in ("Ana Lima", "Bo Chen"):
        browser().sign_in_student(sid, name).rank(sid, days)
    said = follow(prof, prof.post(f"/teach/s/{sid}/close-and-schedule"))
    # Two seats, three students (Cy didn't rank): someone is left without a day, and it says so.
    assert "1 of 2 got their first choice, but 1 student has no day yet" in said and "Everyone has a day" not in said
