// "Send to Scheduler": the button a professor keeps in their bookmarks bar.
// Clicked on any Canvas page, it runs in that page, signed in as them, and
// asks Canvas's own API (same site, their session) for a course's students:
// names and emails, nothing else. Then it hands the list to Scheduler:
//   1. to the Scheduler page that opened this Canvas window, if there is one
//      (postMessage, which only that site can receive), or
//   2. by going to Scheduler with the list (a form post, in this same tab,
//      which no pop-up blocker stops), or, if this Canvas forbids both,
//   3. by copying it, to paste into Scheduler.
// Nothing is changed in Canvas. Teach.py turns this file into the bookmark
// (comments and line breaks out, __SITE__ filled in).
(function () {
  var SITE = "__SITE__";
  if (window.__schedulerButton) { window.__schedulerButton.focus(); return; }

  // A small panel in the corner of the Canvas page. Built from text only
  // (course names never become markup), and styled through the style
  // object, which a Content-Security-Policy allows.
  var panel = document.createElement("div");
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", "Scheduler");
  panel.tabIndex = -1;
  panel.style.cssText = "position:fixed;top:16px;right:16px;z-index:2147483647;width:340px;max-height:80vh;overflow:auto;"
    + "background:#fffdf8;color:#2b2924;border:2px solid #9a5520;border-radius:14px;box-shadow:0 18px 50px rgba(0,0,0,.28);"
    + "font:15px/1.45 -apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;padding:16px 16px 14px;text-align:left";
  var head = document.createElement("div");
  head.style.cssText = "display:flex;align-items:center;justify-content:space-between;margin:0 0 8px";
  var title = document.createElement("strong");
  title.textContent = "Scheduler";
  title.style.cssText = "font-size:16px;color:#7a3f12";
  var close = document.createElement("button");
  close.type = "button";
  close.textContent = "×";
  close.setAttribute("aria-label", "Close");
  close.style.cssText = "border:0;background:none;font-size:22px;line-height:1;cursor:pointer;color:#6b665c;padding:0 4px";
  close.onclick = function () { panel.remove(); window.__schedulerButton = null; };
  head.appendChild(title);
  head.appendChild(close);
  var body = document.createElement("div");
  panel.appendChild(head);
  panel.appendChild(body);
  document.body.appendChild(panel);
  window.__schedulerButton = panel;
  panel.focus();

  function show(text, buttons) {
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
  }

  // Canvas's API, as this page would call it: same site, the professor's
  // session. Answers start with "while(1);" and come in pages.
  function get(url) {
    return fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } }).then(function (r) {
      if (r.status === 401) throw new Error("signin");
      if (r.status === 403) throw new Error("forbidden");
      if (!r.ok) throw new Error("http");
      var next = (r.headers.get("Link") || "").match(/<([^>]+)>;\s*rel="next"/);
      return r.text().then(function (t) {
        return { data: JSON.parse(t.replace(/^while\(1\);/, "")), next: next && next[1] };
      });
    });
  }
  function all(url, sofar) {
    return get(url).then(function (got) {
      var list = (sofar || []).concat(got.data);
      return got.next && list.length < 5000 ? all(got.next, list) : list;
    });
  }
  function trouble(err) {
    var why = err && err.message;
    show(why === "signin" ? "Sign in to Canvas first, then click Send to Scheduler again."
      : why === "forbidden" ? "Canvas didn't let this button read that course's class list. Is it a course you teach?"
      : "This button works on Canvas pages. Open your school's Canvas, then click Send to Scheduler again.");
  }

  var here = location.pathname.match(/\/courses\/(\d+)/);
  if (here) {
    show("Getting the class list…");
    get("/api/v1/courses/" + here[1]).then(function (got) { fetchStudents(here[1], got.data.name); }, trouble);
  } else {
    show("Getting your courses…");
    all("/api/v1/courses?enrollment_type=teacher&per_page=50&include[]=term").then(function (courses) {
      var mine = courses.filter(function (c) { return c && c.id && c.name; });
      if (!mine.length) { show("You aren't a teacher in any Canvas course right now."); return; }
      if (mine.length === 1) { fetchStudents(mine[0].id, mine[0].name); return; }
      show("Which class's list should go to Scheduler?", mine.map(function (c) {
        return { label: c.name + (c.term && c.term.name ? " (" + c.term.name + ")" : ""),
                 go: function () { fetchStudents(c.id, c.name); } };
      }));
    }, trouble);
  }

  function fetchStudents(id, course) {
    show("Getting the class list for " + course + "…");
    all("/api/v1/courses/" + id + "/users?enrollment_type[]=student&include[]=email&per_page=100").then(function (users) {
      var seen = {};
      var students = [];
      users.forEach(function (u) {
        if (!u || seen[u.id]) return;
        seen[u.id] = true;
        var login = u.login_id || "";
        students.push({ id: u.id, name: u.name || u.sortable_name || "",
                        email: u.email || (login.indexOf("@") > 0 ? login : "") });
      });
      if (!students.length) { show(course + " has no students in Canvas yet."); return; }
      fillEmails(students).then(function () {
        deliver({ type: "scheduler-class-list", v: 1, host: location.host, course: course,
                  students: students.map(function (s) { return { name: s.name, email: s.email }; }) });
      });
    }, trouble);
  }

  // Some schools' Canvas leaves emails out of the class list; each
  // student's profile has their main email, when the teacher may see it.
  // Asked a few at a time; if Canvas says no, the rest go without (they
  // sign in to Scheduler with a PIN instead).
  function fillEmails(students) {
    var missing = students.filter(function (s) { return !s.email; });
    if (!missing.length) return Promise.resolve();
    show("Getting their email addresses…");
    var refused = false;
    var next = 0;
    function worker() {
      if (refused || next >= missing.length) return Promise.resolve();
      var s = missing[next++];
      return get("/api/v1/users/" + s.id + "/profile").then(function (got) {
        if (got.data && got.data.primary_email) s.email = got.data.primary_email;
      }, function (err) {
        if (err && (err.message === "forbidden" || err.message === "signin")) refused = true;
      }).then(worker);
    }
    var workers = [];
    for (var i = 0; i < 6; i++) workers.push(worker());
    return Promise.all(workers);
  }

  function lines(list) {
    return list.students.map(function (s) { return s.email ? s.name + ", " + s.email : s.name; }).join("\n");
  }

  function deliver(list) {
    var count = list.students.length + (list.students.length === 1 ? " student" : " students");
    show("Got " + count + " from " + list.course + ". Sending them to Scheduler…");
    // 1. The Scheduler page that opened this window, if it says it got them.
    var thanked = false;
    window.addEventListener("message", function (e) {
      if (e.origin === SITE && e.data && e.data.type === "scheduler-thanks") {
        thanked = true;
        show("Sent " + count + " to Scheduler. Taking you back there… (If this tab stays open, switch to the Scheduler tab, "
          + "check the list and press Add.)");
      }
    });
    if (window.opener) {
      try { window.opener.postMessage(list, SITE); } catch (err) { /* not reachable */ }
    }
    setTimeout(function () { if (!thanked) byForm(list, count); }, window.opener ? 1500 : 0);
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
    show("Opening Scheduler with " + count + "…");
    setTimeout(function () {
      if (!blocked) return;
      show("Your Canvas doesn't let this button open Scheduler. Copy the list instead, then paste it into Scheduler.", [{
        label: "Copy the class list (" + count + ")", main: true,
        go: function () {
          var text = lines(list);
          var done = function () { show("Copied. In Scheduler, paste it into the box under “Type or paste your students”."); };
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text); done(); });
          } else { fallbackCopy(text); done(); }
        },
      }]);
    }, 400);
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
