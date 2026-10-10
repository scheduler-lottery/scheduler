// "Send to Scheduler" (version 2): the button a professor keeps in their
// bookmarks bar. Clicked on a Canvas page, it runs in that page, signed in
// as them, and asks Canvas's own API (same site, their session) for a
// course's students: names and emails, nothing else. Then it hands the list
// to Scheduler:
//   1. to the Scheduler page that opened this Canvas tab, if there is one
//      (postMessage, which only that site can receive), or
//   2. by going to Scheduler with the list (a form post, in this same tab,
//      which no pop-up blocker stops), or, if this Canvas forbids both,
//   3. by copying it, to paste into Scheduler.
// Clicked on Scheduler's own page, it only tells the page it's there (the
// page's "does it work?" check). Nothing is changed in Canvas, and nothing
// is loaded from anywhere. canvas_import.py turns this file into the
// bookmark: comments and indentation out, __SITE__ and __KEY__ filled in
// (the key is the professor's own, so Scheduler can tell their lists from
// anything another site sends).
(function () {
  var SITE = "__SITE__";
  var KEY = "__KEY__";
  if (location.origin === SITE) {
    document.dispatchEvent(new CustomEvent("scheduler-button-check", { detail: { v: 2, key: KEY } }));
    return;
  }
  var old = window.__schedulerButton;
  if (old && old.busy && document.body.contains(old.panel)) { old.panel.focus(); return; }
  if (old && old.panel) old.panel.remove();
  var run = { busy: true, stopped: false, panel: null };
  window.__schedulerButton = run;
  var ENVX = window.ENV || {};
  var onCanvas = !!(ENVX.DOMAIN_ROOT_ACCOUNT_ID || ENVX.current_user_id || document.getElementById("application"));

  // A small panel in the top right corner of the Canvas page, built from
  // text only (course and student names never become markup) and styled
  // through the style object, which a Content-Security-Policy allows.
  var panel = document.createElement("div");
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", "Send to Scheduler");
  panel.tabIndex = -1;
  panel.style.cssText = "position:fixed;top:16px;right:16px;z-index:2147483647;width:360px;max-width:calc(100vw - 32px);"
    + "max-height:80vh;overflow:auto;background:#fffdf8;color:#2b2924;border:2px solid #9a5520;border-radius:14px;"
    + "box-shadow:0 18px 50px rgba(0,0,0,.28);font:15px/1.45 -apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,"
    + "Arial,sans-serif;padding:16px 16px 14px;text-align:left";
  var head = document.createElement("div");
  head.style.cssText = "display:flex;align-items:center;justify-content:space-between;margin:0 0 8px";
  var title = document.createElement("strong");
  title.textContent = "Send to Scheduler";
  title.style.cssText = "font-size:16px;color:#7a3f12";
  var close = document.createElement("button");
  close.type = "button";
  close.textContent = "×";
  close.setAttribute("aria-label", "Close, and send nothing");
  close.style.cssText = "border:0;background:none;font-size:22px;line-height:1;cursor:pointer;color:#6b665c;padding:0 4px";
  close.onclick = function () { run.stopped = true; panel.remove(); window.__schedulerButton = null; };
  head.appendChild(title);
  head.appendChild(close);
  var body = document.createElement("div");
  panel.appendChild(head);
  panel.appendChild(body);
  document.body.appendChild(panel);
  run.panel = panel;
  panel.focus();

  function show(text, buttons, note) {
    body.textContent = "";
    var p = document.createElement("p");
    p.textContent = text;
    p.style.cssText = "margin:0 0 10px";
    body.appendChild(p);
    (buttons || []).forEach(function (b) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = b.label;
      button.style.cssText = "display:block;width:100%;margin:0 0 8px;padding:10px 12px;border-radius:10px;cursor:pointer;"
        + "text-align:left;font:inherit;" + (b.main ? "background:#9a5520;color:#fff;border:0;font-weight:700"
          : "background:#fff;color:#2b2924;border:1px solid #d9cfbd");
      button.onclick = b.go;
      body.appendChild(button);
    });
    if (note) {
      var small = document.createElement("p");
      small.textContent = note;
      small.style.cssText = "margin:4px 0 0;font-size:13px;color:#6b665c";
      body.appendChild(small);
    }
  }
  function done(text, buttons, note) {
    run.busy = false;
    show(text, buttons, note);
  }
  var again = [{ label: "Choose another course", go: function () { run.busy = true; pick(); } }];

  // Canvas's API, as this page would call it: same site, the professor's
  // session, in pages (Link rel="next"). Older Canvas starts answers with
  // "while(1);". A dropped connection or a busy Canvas gets one more try.
  function get(url, retried) {
    return fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } }).then(function (r) {
      if (r.status === 401) {
        return r.text().then(function (t) { throw new Error(/not authorized|unauthorized/i.test(t) ? "forbidden" : "signin"); });
      }
      if (r.status === 403) throw new Error("forbidden");
      if (r.status === 404) throw new Error("missing");
      if (r.status === 429 || r.status >= 500) throw new Error("busy");
      if (!r.ok) throw new Error("http");
      var next = (r.headers.get("Link") || "").match(/<([^>]+)>;\s*rel="next"/);
      return r.text().then(function (t) {
        try { return { data: JSON.parse(t.replace(/^while\(1\);/, "")), next: next && next[1] }; }
        catch (err) { throw new Error("http"); }
      });
    }, function () { throw new Error("offline"); }).catch(function (err) {
      if (!retried && (err.message === "busy" || err.message === "offline")) {
        return new Promise(function (wait) { setTimeout(wait, 1500); }).then(function () { return get(url, true); });
      }
      throw err;
    });
  }
  function all(url, sofar) {
    return get(url).then(function (got) {
      var list = (sofar || []).concat(got.data);
      if (run.stopped) return list;
      return got.next && list.length <= 3000 ? all(got.next, list) : list;
    });
  }
  function trouble(err, pickAgain) {
    if (run.stopped) return;
    var why = err && err.message;
    if (why === "signin") done("Sign in to Canvas first (in this tab), then click Send to Scheduler again.");
    else if (why === "forbidden") done("Canvas didn't let this button see that course's class list. Is it a course you teach?",
      pickAgain ? again : null);
    else if (why === "missing" && onCanvas) done("Canvas couldn't find that course. Open your course in Canvas, then click "
      + "Send to Scheduler again.");
    else if (why === "busy" || why === "offline" || onCanvas) done("Canvas didn't answer just now. Wait a minute, then click "
      + "Send to Scheduler again. If it keeps happening, go back to Scheduler and use “Copy and paste instead”.");
    else done("This page isn't Canvas yet. If you're still signing in, finish signing in first. When you see your Canvas "
      + "Dashboard or your course, click Send to Scheduler again.");
  }

  // Which course: the one this page is in; else the one the Scheduler page
  // that opened this tab asks for (its sheet's course, from last time);
  // else the professor picks.
  var here = location.pathname.match(/\/courses\/(\d+)/);
  if (here) {
    show("Getting the class list…");
    get("/api/v1/courses/" + here[1]).then(function (got) { fetchStudents(got.data); }, function (err) { trouble(err, true); });
  } else {
    askOpener(function (id) {
      if (!id) { pick(); return; }
      show("Getting the class list…");
      get("/api/v1/courses/" + id).then(function (got) { fetchStudents(got.data); }, pick);
    });
  }

  function askOpener(then) {
    var answered = false;
    function finish(id) {
      if (answered) return;
      answered = true;
      window.removeEventListener("message", listen);
      then(id);
    }
    function listen(e) {
      if (e.origin === SITE && e.data && e.data.type === "scheduler-course") {
        finish(/^\d{1,15}$/.test(String(e.data.id || "")) ? String(e.data.id) : "");
      }
    }
    if (!window.opener) { finish(""); return; }
    window.addEventListener("message", listen);
    try { window.opener.postMessage({ type: "scheduler-hello" }, SITE); } catch (err) { finish(""); return; }
    setTimeout(function () { finish(""); }, 600);
  }

  function pick() {
    if (run.stopped) return;
    show("Getting your courses…");
    all("/api/v1/courses?per_page=100&include[]=term&include[]=total_students").then(function (courses) {
      if (run.stopped) return;
      var mine = courses.filter(function (c) {
        return c && c.id && c.name && (!c.enrollments || c.enrollments.some(function (e) {
          return /^(teacher|ta|designer)$/i.test(e.type || "");
        }));
      });
      if (!mine.length) {
        done("Canvas doesn't list you as a teacher of any course. Open your course in Canvas (click its card on your "
          + "Dashboard), then click Send to Scheduler again there.");
        return;
      }
      if (mine.length === 1) { fetchStudents(mine[0]); return; }
      var today = Date.now();
      var current = function (c) {
        var t = c.term || {};
        var from = Date.parse(t.start_at || c.start_at || ""), to = Date.parse(t.end_at || c.end_at || "");
        return (isNaN(from) || from <= today) && (isNaN(to) || to >= today) ? 0 : 1;
      };
      mine = mine.map(function (c, i) { return { c: c, i: i }; }).sort(function (a, b) {
        return current(a.c) - current(b.c) || a.i - b.i;
      }).map(function (x) { return x.c; });
      show("Which course's students should go to Scheduler?", mine.map(function (c) {
        return { label: label(c), go: function () { fetchStudents(c); } };
      }), "Only their names and email addresses are sent. Nothing in Canvas changes. Not listed? Open the course in "
        + "Canvas and click Send to Scheduler there.");
      run.busy = false;
    }, trouble);
  }
  function label(c) {
    var term = c.term && c.term.name && !/default term/i.test(c.term.name) ? " · " + c.term.name : "";
    var size = typeof c.total_students === "number" ? " · " + c.total_students + (c.total_students === 1 ? " student" : " students") : "";
    return c.name + term + size + (c.workflow_state === "unpublished" ? " (not published yet)" : "");
  }

  function fetchStudents(course) {
    if (run.stopped) return;
    run.busy = true;
    var name = (course && course.name) || "your course";
    show("Getting the class list for " + name + "…");
    all("/api/v1/courses/" + course.id + "/users?enrollment_type[]=student&include[]=email&per_page=100").then(function (users) {
      if (run.stopped) return;
      var seen = {};
      var students = [];
      users.forEach(function (u) {
        if (!u || seen[u.id]) return;
        seen[u.id] = true;
        var login = u.login_id || "";
        students.push({ name: u.name || u.sortable_name || "", email: u.email || (login.indexOf("@") > 0 ? login : "") });
      });
      if (!students.length) {
        done(name + " has no students in Canvas yet. If it isn't published, or students just enrolled, Canvas may not list "
          + "them for a day or two.", again);
        return;
      }
      if (students.length > 3000) { done(name + " has more than 3,000 students: too many for one sign-up sheet.", again); return; }
      deliver({ type: "scheduler-class-list", v: 2, key: KEY, courseId: String(course.id), course: name,
                host: location.host, students: students });
    }, function (err) { trouble(err, true); });
  }

  function deliver(list) {
    if (run.stopped) return;
    var count = list.students.length + (list.students.length === 1 ? " student" : " students");
    if (!window.opener) { byForm(list, count); return; }
    // 1. The Scheduler page that opened this tab, if it says it got them.
    show("Got " + count + " from " + list.course + ". Taking you back to Scheduler…");
    var thanked = false;
    window.addEventListener("message", function (e) {
      if (e.origin === SITE && e.data && e.data.type === "scheduler-thanks" && !thanked) {
        thanked = true;
        done("Sent " + count + " to Scheduler. This tab closes by itself; if it doesn't, switch to your Scheduler tab "
          + "and press Add.");
      }
    });
    try { window.opener.postMessage(list, SITE); } catch (err) { /* not reachable */ }
    setTimeout(function () { if (!thanked && !run.stopped) byForm(list, count); }, 1500);
  }

  // 2. Scheduler, in this tab, with the list. 3. If this Canvas forbids
  // that too, the list to copy.
  function byForm(list, count) {
    var blocked = false;
    document.addEventListener("securitypolicyviolation", function (e) {
      if (e.violatedDirective && e.violatedDirective.indexOf("form-action") === 0) blocked = true;
    });
    var form = document.createElement("form");
    form.method = "post";
    form.action = SITE + "/canvas-import";
    form.acceptCharset = "utf-8";
    var field = document.createElement("input");
    field.type = "hidden";
    field.name = "list";
    field.value = JSON.stringify(list);
    form.appendChild(field);
    document.body.appendChild(form);
    form.submit();
    form.remove();
    show("Got " + count + " from " + list.course + ". Opening Scheduler…");
    setTimeout(function () {
      if (!blocked) return;
      done("Your Canvas doesn't let this button open Scheduler. Copy the list instead, then paste it into Scheduler.", [{
        label: "Copy the class list (" + count + ")", main: true,
        go: function () {
          var text = lines(list);
          var copied = function () { show("Copied. In Scheduler, paste it into the box under “Type or paste your students”."); };
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(copied, function () { fallbackCopy(text); copied(); });
          } else { fallbackCopy(text); copied(); }
        },
      }]);
    }, 400);
  }
  function lines(list) {
    return list.students.map(function (s) {
      var name = /[,"]/.test(s.name) ? '"' + s.name.replace(/"/g, '""') + '"' : s.name;
      return s.email ? (name ? name + ", " : "") + s.email : name;
    }).join("\n");
  }
  function fallbackCopy(text) {
    var area = document.createElement("textarea");
    area.value = text;
    area.style.cssText = "position:fixed;top:-1000px";
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
})();
