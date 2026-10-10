"""
A stand-in for Canvas, for trying the Canvas import end to end on your own
computer (there's no real Canvas account to test with).

    .venv/bin/python tools/fake_canvas.py          # http://127.0.0.1:5077

It behaves the way Canvas does where it matters to Scheduler:
- you have to "sign in" first (a session cookie), and signing in lands on
  the Dashboard, whatever page you asked for (as SSO often does);
- course pages at /courses/<id>, with the course menu (Course Analytics);
- the REST API under /api/v1, answering only with that session cookie,
  each JSON answer starting with "while(1);", in pages (per_page, at most
  50 here, and a Link header with rel="next");
- emails only for teachers, and login IDs that aren't emails;
- X-Frame-Options: SAMEORIGIN, so it can't be shown inside another site.
FAKE_CANVAS_CSP=1 adds a strict Content-Security-Policy, to check that the
import still runs on a Canvas that enforces one. FAKE_CANVAS_NO_EMAIL=1 is a
school whose class list leaves emails out (each profile still has one).
"""

import json
import os
import secrets
from urllib.parse import urlencode

from flask import Flask, make_response, redirect, request

app = Flask(__name__)
SESSIONS = set()

COURSES = {
    "101": {"name": "2026FA_BUSCOM_615_SEC1", "course_code": "BUSCOM 615", "term": "2026 Fall"},
    "202": {"name": "2026FA_LAW_540_SEC20", "course_code": "LAW 540", "term": "2026 Fall"},
}
FIRST = ["Alex", "Sam", "Riya", "Jordan", "Maya", "Diego", "Priya", "Noah", "Zoë", "Chen", "Fatima", "Liam"]
LAST = ["Johnson", "Lee", "Patel", "Kim", "García", "Nguyen", "O'Brien", "Smith", "Ångström", "Okafor"]
STUDENTS = {
    "101": [{"id": 1000 + i, "name": f"{FIRST[i % len(FIRST)]} {LAST[i % len(LAST)]}",
             "sortable_name": f"{LAST[i % len(LAST)]}, {FIRST[i % len(FIRST)]}",
             "login_id": f"abc{100 + i}",
             "email": f"{FIRST[i % len(FIRST)].lower()}.{LAST[i % len(LAST)].lower().replace(chr(39), '')}{i}@u.example.edu"}
            for i in range(57)],  # more than one page
    "202": [{"id": 2000 + i, "name": f"Student {i}", "sortable_name": f"{i}, Student", "login_id": f"s{i}",
             "email": f"student{i}@u.example.edu"} for i in range(8)],
}


@app.after_request
def canvas_headers(response):
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    if os.environ.get("FAKE_CANVAS_CSP"):
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; connect-src 'self'"
    return response


def signed_in():
    return request.cookies.get("canvas_session") in SESSIONS


def page(title, body):
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font-family:Lato,Helvetica,sans-serif;margin:0;display:flex}}nav{{width:90px;background:#f5f5f5;min-height:100vh}}
.course-nav{{width:180px;padding:16px}}.course-nav a{{display:block;padding:6px 0;color:#2d3b45}}main{{padding:24px;flex:1}}
.card{{display:inline-block;width:200px;height:140px;margin:8px;border:1px solid #ccc;border-radius:6px;vertical-align:top}}
.card a{{display:block;padding:80px 12px 0}}</style></head>
<body><nav aria-label="Global">&nbsp;</nav>{body}</body></html>"""


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        token = secrets.token_hex(16)
        SESSIONS.add(token)
        response = make_response(redirect("/"))  # SSO: always the Dashboard
        response.set_cookie("canvas_session", token, samesite="Lax", httponly=True)
        return response
    return page("Log in", '<main><h1>Fake Canvas sign-in</h1><form method="post"><button>Sign in</button></form></main>')


@app.route("/")
def dashboard():
    if not signed_in():
        return redirect("/login")
    cards = "".join(f'<div class="card"><a href="/courses/{cid}">{c["name"]}</a></div>' for cid, c in COURSES.items())
    return page("Dashboard", f"<main><h1>Dashboard</h1>{cards}</main>")


@app.route("/courses")
def courses_page():
    if not signed_in():
        return redirect("/login")
    rows = "".join(f'<li><a href="/courses/{cid}">{c["name"]}</a></li>' for cid, c in COURSES.items())
    return page("Courses", f"<main><h1>All Courses</h1><ul>{rows}</ul></main>")


@app.route("/courses/<cid>")
@app.route("/courses/<cid>/<path:rest>")
def course(cid, rest=""):
    if not signed_in():
        return redirect("/login")
    if cid not in COURSES:
        return page("Not found", "<main><h1>Page not found</h1></main>"), 404
    menu = "".join(f'<a href="/courses/{cid}">{item}</a>' for item in
                   ("Home", "Announcements", "Assignments", "Grades", "People", "Files", "Course Analytics"))
    return page(COURSES[cid]["name"], f'<div class="course-nav">{menu}</div><main><h1>{COURSES[cid]["name"]}</h1>'
                                      f"<p>{rest or 'Course home'}</p></main>")


def api(data, status=200, link=None):
    response = make_response("while(1);" + json.dumps(data), status)
    response.headers["Content-Type"] = "application/json; charset=utf-8"
    if link:
        response.headers["Link"] = link
    return response


@app.route("/api/v1/courses")
def api_courses():
    if not signed_in():
        return api({"errors": [{"message": "user authorization required"}]}, 401)
    return api([{"id": int(cid), "name": c["name"], "course_code": c["course_code"],
                 "term": {"name": c["term"]}, "enrollments": [{"type": "teacher", "role": "TeacherEnrollment"}]}
                for cid, c in COURSES.items()])


@app.route("/api/v1/courses/<cid>")
def api_course(cid):
    if not signed_in():
        return api({"errors": [{"message": "user authorization required"}]}, 401)
    if cid not in COURSES:
        return api({"errors": [{"message": "The specified resource does not exist."}]}, 404)
    c = COURSES[cid]
    return api({"id": int(cid), "name": c["name"], "course_code": c["course_code"]})


@app.route("/api/v1/courses/<cid>/users")
def api_users(cid):
    if not signed_in():
        return api({"errors": [{"message": "user authorization required"}]}, 401)
    if cid not in STUDENTS:
        return api({"errors": [{"message": "The specified resource does not exist."}]}, 404)
    per_page = min(int(request.args.get("per_page", 10)), 50)
    number = int(request.args.get("page", 1))
    everyone = STUDENTS[cid]
    chunk = everyone[(number - 1) * per_page:number * per_page]
    with_email = "email" in request.args.getlist("include[]") and not os.environ.get("FAKE_CANVAS_NO_EMAIL")
    out = [{k: v for k, v in s.items() if k != "email" or with_email} for s in chunk]
    link = None
    if number * per_page < len(everyone):
        args = request.args.to_dict(flat=False)
        args["page"] = [str(number + 1)]
        link = f'<{request.host_url.rstrip("/")}{request.path}?{urlencode(args, doseq=True)}>; rel="next"'
    return api(out, link=link)


@app.route("/api/v1/users/<int:uid>/profile")
def api_profile(uid):
    if not signed_in():
        return api({"errors": [{"message": "user authorization required"}]}, 401)
    for people in STUDENTS.values():
        for s in people:
            if s["id"] == uid:
                return api({"id": uid, "name": s["name"], "primary_email": s["email"], "login_id": s["login_id"]})
    return api({"errors": [{"message": "The specified resource does not exist."}]}, 404)


if __name__ == "__main__":
    app.run(port=5077, debug=False)
