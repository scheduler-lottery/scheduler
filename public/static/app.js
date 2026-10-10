// Fonts first: the page (hidden by theme-init.js) shows once the fonts it
// uses have loaded, or after 2.5 seconds whatever happens, so text never
// flashes from a stand-in font to the real one. The fonts are asked for by
// name, so this works the same in a background tab.
(function () {
  var root = document.documentElement;
  if (!root.classList.contains("fonts-loading")) return;
  var shown = false;
  var show = function () {
    if (shown) return;
    shown = true;
    root.classList.remove("fonts-loading");
    document.dispatchEvent(new Event("page-shown"));
  };
  setTimeout(show, 2500);
  var want = function (face) {
    return document.fonts.load(face).then(function (found) { return found.length > 0; }, function () { return false; });
  };
  var choice = root.getAttribute("data-font");
  // Headings: the variable Roslindale, or the single weight that stands in
  // for it until it's uploaded (the sans and easy-to-read choices use their
  // own font for headings too).
  var heading = choice === "sans" || choice === "readable" ? Promise.resolve(true)
    : want("300 16px Roslindale").then(function (ok) { return ok || want("16px 'Roslindale Display Condensed'"); });
  var text = {
    oldstyle: ["16px 'Scheduler Text'", "bold 16px 'Scheduler Text'", "italic 16px 'Scheduler Text'"],
    sans: ["16px Inter", "600 16px Inter"],
    readable: ["16px 'Atkinson Hyperlegible'", "bold 16px 'Atkinson Hyperlegible'"],
  }[choice] || ["16px YaleNew", "bold 16px YaleNew", "italic 16px YaleNew"];
  Promise.all([heading].concat(text.map(want))).then(show, show);
})();

// Small behaviors used across the site. Everything here is progressive: the
// pages still work (if less smoothly) with JavaScript turned off.
(function () {
  var csrfMeta = document.querySelector('meta[name="csrf-token"]');
  var CSRF = csrfMeta ? csrfMeta.getAttribute("content") : "";

  function store(key, value) {
    try {
      if (value === undefined) return localStorage.getItem(key);
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch (e) {
      return null; // private browsing, blocked storage: just don't remember
    }
    return null;
  }

  // Messages about what just happened: once the page shows, they come up in
  // the upper part of the window, the page behind blurred for a moment.
  // Good news fades after a while; problems stay until they're closed.
  var stack = document.querySelector("[data-flash-stack]");
  if (stack) {
    var notes = Array.prototype.slice.call(stack.querySelectorAll(".flash"));
    var dismiss = function (note) {
      if (!note.parentNode) return;
      note.classList.add("leaving");
      setTimeout(function () {
        note.remove();
        if (!stack.querySelector(".flash")) stack.remove();
      }, 350);
    };
    notes.forEach(function (note) {
      note.querySelector(".flash-close").addEventListener("click", function () { dismiss(note); });
    });
    var arrive = function () {
      // Over everything else on the page, and put back in so screen readers
      // read them out.
      document.body.appendChild(stack);
      notes.forEach(function (note) { note.remove(); });
      setTimeout(function () {
        var veil = document.createElement("div");
        veil.className = "flash-veil";
        veil.setAttribute("aria-hidden", "true");
        document.body.appendChild(veil);
        veil.addEventListener("animationend", function () { veil.remove(); });
        setTimeout(function () { veil.remove(); }, 3000);
        notes.forEach(function (note) {
          stack.appendChild(note);
          if (!note.classList.contains("flash-error")) setTimeout(function () { dismiss(note); }, 8000);
        });
      }, 50);
    };
    if (!document.documentElement.classList.contains("fonts-loading")) {
      arrive();
    } else {
      var arrived = false;
      var once = function () { if (!arrived) { arrived = true; arrive(); } };
      document.addEventListener("page-shown", once);
      setTimeout(once, 3100); // the page shows by itself after 3 seconds
    }
  }

  // Close buttons on the notes pinned to the bottom of the screen.
  document.addEventListener("click", function (e) {
    var closer = e.target.closest && e.target.closest("[data-dismiss-toast]");
    if (closer) closer.closest(".undo-banner").remove();
  });

  // Forms (and single buttons) that need an "are you sure?" first.
  document.addEventListener("submit", function (e) {
    var form = e.target;
    var message = form.getAttribute && form.getAttribute("data-confirm");
    var button = e.submitter;
    var buttonMessage = button && button.getAttribute && button.getAttribute("data-confirm");
    if (buttonMessage) message = buttonMessage;
    if (message && !window.confirm(message)) e.preventDefault();
  });
  document.querySelectorAll("[data-confirm-click]").forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      if (!window.confirm(btn.getAttribute("data-confirm-click"))) e.preventDefault();
    });
  });
  // Something drastic (the owner's "Delete everything") waits until its
  // word is typed exactly; the server checks the same word.
  document.querySelectorAll("form[data-type-to-confirm]").forEach(function (form) {
    var word = form.getAttribute("data-type-to-confirm");
    var typed = form.querySelector('input[name="confirm"]');
    var go = form.querySelector('[type="submit"]');
    if (!typed || !go) return;
    function sync() { go.disabled = typed.value.trim() !== word; }
    typed.addEventListener("input", sync);
    sync();
  });

  // Timestamps are stored in UTC; show them in the reader's own time zone.
  document.querySelectorAll("time[data-local]").forEach(function (el) {
    var when = new Date(el.getAttribute("datetime"));
    if (isNaN(when)) return;
    var kind = el.getAttribute("data-local");
    if (kind === "date") {
      el.textContent = when.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
    } else if (kind === "time") {
      el.textContent = when.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
    } else if (kind === "deadline") {
      el.textContent = when.toLocaleString(undefined, {
        weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
      });
    } else if (kind === "seconds") {
      el.textContent = when.toLocaleString(undefined, {
        month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit",
      });
    } else {
      el.textContent = when.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
    }
    el.title = when.toLocaleString();
  });

  // The instructor's time zone, so dates in downloads match their clock.
  document.querySelectorAll("input[data-timezone]").forEach(function (input) {
    try {
      input.value = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    } catch (e) { /* older browser: the site's default is used */ }
  });

  // "12 / 100" under boxes with a length limit, once they're nearly full.
  document.querySelectorAll("[data-count]").forEach(function (box) {
    var limit = parseInt(box.getAttribute("maxlength"), 10);
    if (!limit) return;
    var counter = document.createElement("span");
    counter.className = "char-count";
    counter.setAttribute("aria-live", "polite");
    box.insertAdjacentElement("afterend", counter);
    var update = function () {
      var used = box.value.length;
      counter.textContent = used > limit * 0.7 ? used + " / " + limit + " characters" : "";
    };
    box.addEventListener("input", update);
    update();
  });

  // "Find a student…" boxes filter the rows of a table as you type.
  document.querySelectorAll("input[data-filter]").forEach(function (input) {
    var rows = document.querySelectorAll(input.getAttribute("data-filter"));
    input.addEventListener("input", function () {
      var q = input.value.trim().toLowerCase();
      rows.forEach(function (row) {
        row.hidden = q && row.textContent.toLowerCase().indexOf(q) === -1;
      });
    });
  });

  // Copy buttons: data-copy="#selector-of-input-or-textarea".
  document.querySelectorAll("[data-copy]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var source = document.querySelector(btn.getAttribute("data-copy"));
      if (!source) return;
      var text = source.value;
      var done = function () {
        var label = btn.textContent;
        btn.textContent = "Copied";
        setTimeout(function () { btn.textContent = label; }, 1800);
        var markUrl = btn.getAttribute("data-mark-shared");
        if (markUrl) {
          fetch(markUrl, { method: "POST", headers: { "X-CSRF-Token": CSRF } }).catch(function () {});
        }
      };
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(done, function () {
          source.select();
          document.execCommand("copy");
          done();
        });
      } else {
        source.select();
        document.execCommand("copy");
        done();
      }
    });
  });

  // Cards that fold away remember it on this computer (per card, per
  // class); one holding something to deal with opens anyway.
  document.querySelectorAll("details[data-fold]").forEach(function (card) {
    var key = "fold-" + card.getAttribute("data-fold");
    var saved = store(key);
    if (saved && !card.hasAttribute("data-fold-alert")) card.open = saved === "open";
    card.addEventListener("toggle", function () { store(key, card.open ? "open" : "closed"); });
  });
  // A link to something folded away (a card, a "Modify this"): unfold it.
  var reveal = function (scroll) {
    var target = location.hash && document.getElementById(decodeURIComponent(location.hash.slice(1)));
    if (!target) return;
    for (var el = target; el; el = el.parentElement) {
      if (el.tagName === "DETAILS") el.open = true;
    }
    if (scroll) target.scrollIntoView({ block: "start" });
  };
  reveal(true);
  window.addEventListener("hashchange", function () { reveal(true); });

  // Emailing the class the link counts as sharing it, as copying it does.
  document.querySelectorAll("[data-compose][data-mark-shared]").forEach(function (link) {
    link.addEventListener("click", function () {
      fetch(link.getAttribute("data-mark-shared"), { method: "POST", headers: { "X-CSRF-Token": CSRF } })
        .catch(function () {});
    });
  });

  // After a delete, the backup that was saved first downloads automatically.
  var autoDownload = document.querySelector("a[data-auto-download]");
  if (autoDownload) {
    setTimeout(function () { window.location.href = autoDownload.href; }, 400);
  }

  // Class-list upload: drop a file (or pick one) and it uploads right away.
  document.querySelectorAll("form[data-dropzone]").forEach(function (form) {
    var input = form.querySelector('input[type="file"]');
    var zone = form.querySelector(".dropzone");
    var status = form.querySelector("[data-dropzone-status]");
    if (!input || !zone) return;

    function upload() {
      if (!input.files || !input.files.length) return;
      zone.classList.add("busy");
      if (status) status.textContent = "Reading “" + input.files[0].name + "”…";
      var pasted = form.querySelector('textarea[name="pasted"]');
      if (pasted) pasted.value = "";
      form.submit();
    }

    input.addEventListener("change", upload);
    ["dragenter", "dragover"].forEach(function (type) {
      zone.addEventListener(type, function (e) {
        e.preventDefault();
        zone.classList.add("dragover");
      });
    });
    ["dragleave", "dragend", "drop"].forEach(function (type) {
      zone.addEventListener(type, function () { zone.classList.remove("dragover"); });
    });
    zone.addEventListener("drop", function (e) {
      e.preventDefault();
      if (!e.dataTransfer || !e.dataTransfer.files.length) return;
      input.files = e.dataTransfer.files;
      upload();
    });
  });

  // The getting-started checklist remembers being folded away, per sheet,
  // on this computer.
  document.querySelectorAll("details[data-guide]").forEach(function (guide) {
    var key = "guide-closed-" + guide.getAttribute("data-guide");
    if (store(key) === "1") guide.open = false;
    guide.addEventListener("toggle", function () {
      store(key, guide.open ? null : "1");
    });
  });

  // The deadline picker: the warning and the "when sign-ups close" choices
  // show once a date is picked; "No deadline" empties it.
  document.querySelectorAll("[data-deadline-field]").forEach(function (field) {
    var date = field.querySelector('input[type="date"]');
    var more = field.querySelectorAll("[data-deadline-more]");
    var sync = function () { more.forEach(function (el) { el.hidden = !date.value; }); };
    date.addEventListener("input", sync);
    date.addEventListener("change", sync);
    var clear = field.querySelector("[data-deadline-clear]");
    if (clear) clear.addEventListener("click", function () { date.value = ""; sync(); });
    sync();
  });

  // While a sheet is being set up, its name in the "Draft" pill follows
  // what's typed.
  var draftTitle = document.querySelector("[data-draft-title]");
  var titleBox = document.getElementById("title");
  if (draftTitle && titleBox) {
    titleBox.addEventListener("input", function () {
      draftTitle.textContent = titleBox.value.trim() || "Untitled class";
    });
  }

  // Setting up a new sheet: one question at a time, with Back and Next and
  // the setup's progress bar ("Step 2 of 8"); the last question creates the
  // sheet. Without JavaScript it's one form.
  document.querySelectorAll("form[data-wizard]").forEach(function (form) {
    var steps = Array.prototype.slice.call(form.querySelectorAll("[data-wizard-step]"));
    var progress = form.querySelector("[data-setup-progress]");
    var error = form.querySelector("[data-wizard-error]");
    var back = form.querySelector("[data-wizard-back]");
    var next = form.querySelector("[data-wizard-next]");
    var done = form.querySelector("[data-wizard-done]");
    var at = 0;
    var show = function (i, focus) {
      at = Math.max(0, Math.min(i, steps.length - 1));
      steps.forEach(function (step, n) { step.hidden = n !== at; });
      // The Draft pill, once there's a title to put in it.
      var draftLine = document.querySelector("[data-draft-line]");
      if (draftLine) draftLine.hidden = steps[at].getAttribute("data-wizard-step") === "name";
      if (progress) {
        var number = parseInt(steps[at].getAttribute("data-progress-number"), 10);
        progress.querySelector("[data-progress-step]").textContent =
          "Step " + number + " of " + progress.getAttribute("data-total");
        progress.querySelector("[data-progress-bar]").value = number - 0.5;
      }
      back.hidden = at === 0;
      next.hidden = at === steps.length - 1;
      done.hidden = at !== steps.length - 1;
      error.hidden = true;
      var question = steps[at].querySelector(".wizard-q");
      if (focus && question) question.focus();
    };
    var problem = function (step) {
      var title = step.querySelector("#title");
      if (title && !title.value.trim()) return "Give the class a name. It's what your students will see.";
      var chips = step.querySelector("[data-day-chips]");
      if (chips && chips.querySelectorAll(".day-chip").length < 2) {
        return "Pick at least two days on the calendar for students to choose between.";
      }
      var seats = step.querySelector("#capacity");
      if (seats && !(parseInt(seats.value, 10) >= 1)) return "Seats per day should be a whole number, like 4.";
      return "";
    };
    next.addEventListener("click", function () {
      var says = problem(steps[at]);
      if (says) { error.textContent = says; error.hidden = false; return; }
      show(at + 1, true);
    });
    back.addEventListener("click", function () { show(at - 1, true); });
    // Enter in a field moves on, rather than creating the sheet half set up.
    form.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && e.target.tagName === "INPUT" && at < steps.length - 1) {
        e.preventDefault();
        next.click();
      }
    });
    var start = steps.map(function (step) { return step.getAttribute("data-wizard-step"); })
      .indexOf(form.getAttribute("data-wizard-start"));
    show(start > 0 ? start : 0, false);
    // The first question's box, ready to type in (once the page shows: a
    // page still hidden while its fonts load can't take focus).
    var ready = function () {
      var box = steps[at].querySelector("input:not([type=hidden]), textarea");
      if (at === 0 && box) box.focus({ preventScroll: true });
    };
    if (document.documentElement.classList.contains("fonts-loading")) {
      document.addEventListener("page-shown", ready);
    } else {
      ready();
    }
  });

  // The class-list questions: one step at a time, with Back. Each answer
  // opens the next step; the drop box shows on the steps that use it.
  document.querySelectorAll("[data-quiz]").forEach(function (quiz) {
    var steps = Array.prototype.slice.call(quiz.querySelectorAll(".quiz-step"));
    var drop = quiz.querySelector("[data-quiz-drop]");
    var key = "quiz-step-" + quiz.getAttribute("data-sheet");
    var trail = [];
    var current = "start";
    var show = function (name, focus) {
      if (!quiz.querySelector('[data-step="' + name + '"]')) name = "start";
      current = name;
      steps.forEach(function (step) { step.hidden = step.getAttribute("data-step") !== name; });
      // On the Canvas step, only with its download steps open.
      if (drop) drop.hidden = !(name === "file" || (name === "canvas" && !!quiz.querySelector("[data-canvas-download][open]")));
      try { sessionStorage.setItem(key, name); } catch (e) { /* fine */ }
      var question = quiz.querySelector('[data-step="' + name + '"] .quiz-q');
      if (focus && question) question.focus({ preventScroll: false });
    };
    var go = function (name) { trail.push(current); show(name, true); };
    // Another part of the page bringing its step into view (the Canvas
    // step, when a class list arrives from Canvas).
    quiz.addEventListener("quiz-go", function (e) { if (current !== e.detail) go(e.detail); });
    quiz.addEventListener("click", function (e) {
      var to = e.target.closest("[data-go]");
      if (to) { go(to.getAttribute("data-go")); return; }
      if (e.target.closest("[data-back]")) show(trail.pop() || "start", true);
    });
    var saved = null;
    try { saved = sessionStorage.getItem(key); } catch (e) { saved = null; }
    show(saved || "start", false);

    // Which school's Canvas: confirm the guess, search by name, or type the
    // address. The choice is saved to the instructor's account.
    var search = quiz.querySelector("[data-school-search]");
    var results = quiz.querySelector("[data-school-results]");
    var status = quiz.querySelector("[data-school-status]");
    if (search && search.hasAttribute("data-start-hidden")) search.hidden = true;
    var choose = function (domain, name) {
      if (status) status.textContent = "Saving…";
      fetch(quiz.getAttribute("data-school-save"), {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF },
        body: JSON.stringify({ domain: domain, name: name }),
      }).then(function (r) { return r.json().then(function (data) { return { ok: r.ok, data: data }; }); })
        .then(function (answer) {
          if (!answer.ok) { if (status) status.textContent = answer.data.error || "That didn't work. Try again."; return; }
          quiz.setAttribute("data-canvas-host", answer.data.domain);
          var named = quiz.querySelector("[data-school-name]");
          if (named) named.textContent = answer.data.name;
          var home = quiz.querySelector("[data-canvas-home]");
          if (home) home.href = "https://" + answer.data.domain + "/courses";
          var course = document.getElementById("canvas-course");
          if (course) course.dispatchEvent(new Event("input"));
          if (status) status.textContent = "";
          go("canvas");
        })
        .catch(function () { if (status) status.textContent = "Couldn't save that. Check your connection and try again."; });
    };
    quiz.addEventListener("click", function (e) {
      var pick = e.target.closest("[data-school-pick]");
      if (pick) { choose(pick.getAttribute("data-domain"), pick.getAttribute("data-name")); return; }
      if (e.target.closest("[data-school-other]") && search) {
        search.hidden = false;
        var box = search.querySelector("[data-school-query]");
        if (box) box.focus();
      }
      if (e.target.closest("[data-school-host-use]")) {
        var typed = quiz.querySelector("[data-school-host]");
        if (typed && typed.value.trim()) choose(typed.value.trim(), "");
      }
    });
    var query = quiz.querySelector("[data-school-query]");
    var timer = null;
    if (query && results) {
      query.addEventListener("input", function () {
        clearTimeout(timer);
        var words = query.value.trim();
        if (words.length < 2) { results.innerHTML = ""; if (status) status.textContent = ""; return; }
        timer = setTimeout(function () {
          if (status) status.textContent = "Searching…";
          fetch(quiz.getAttribute("data-schools-url") + "?q=" + encodeURIComponent(words), { credentials: "same-origin" })
            .then(function (r) { return r.json(); })
            .then(function (data) {
              results.innerHTML = "";
              (data.schools || []).forEach(function (school) {
                var item = document.createElement("li");
                var button = document.createElement("button");
                button.type = "button";
                button.className = "school-result";
                button.setAttribute("data-school-pick", "");
                button.setAttribute("data-domain", school.domain);
                button.setAttribute("data-name", school.name);
                var nameLine = document.createElement("strong");
                nameLine.textContent = school.name;
                var hostLine = document.createElement("span");
                hostLine.textContent = school.domain;
                button.appendChild(nameLine);
                button.appendChild(hostLine);
                item.appendChild(button);
                results.appendChild(item);
              });
              if (status) {
                status.textContent = (data.schools || []).length
                  ? "Pick yours."
                  : "No school by that name. Try another spelling, or use “My school isn't listed” below.";
              }
            })
            .catch(function () { if (status) status.textContent = "Couldn't search just now. Try again, or type the address below."; });
        }, 300);
      });
    }
  });

  // Connect Canvas's course choice: the course's name goes with its number
  // (for the review and the sheet), set from the button pressed.
  document.querySelectorAll(".connect-courses").forEach(function (form) {
    form.addEventListener("click", function (e) {
      var pick = e.target.closest("button[data-name]");
      if (pick) form.querySelector("[data-course-name]").value = pick.getAttribute("data-name");
    });
  });

  // The Canvas step: the class list from Canvas, the simple way.
  // - Scheduler Helper (extension/), once added to the browser: one click
  //   here and it brings the list (in Chrome and Edge, and in Safari once
  //   Safari's is published). Until it's added, the step offers to add it.
  // - Or Canvas's own student file: Canvas opens beside this page, pictures
  //   show where to click, and the file goes in the box.
  // (Connect Canvas, when the school has issued a key, is just a link.)
  // Whichever way, the list shows here first; nothing is added until the
  // professor presses Add.
  var guide = document.querySelector("[data-canvas-guide]");
  if (guide) {
    var quizBox = guide.closest("[data-quiz]");
    var stepBox = guide.closest(".quiz-step") || guide.parentNode;
    var savedCourse = guide.getAttribute("data-canvas-course-id") || "";
    var ua = navigator.userAgent;
    var isEdge = /Edg\//.test(ua);
    var isFirefox = /Firefox\//.test(ua);
    var isSafari = /Safari\//.test(ua) && !/Chrome\/|Chromium\/|Edg\/|Firefox\/|OPR\//.test(ua);
    var isChromium = /Chrome\//.test(ua) && !/OPR\//.test(ua);
    // Course Analytics' tool number at schools we know of, so Canvas can
    // open right on it for a sheet whose course is known.
    var KNOWN_TOOLS = { "canvas.northwestern.edu": "48685" };

    // The school's Canvas: the one saved on their account, else the one
    // their email suggests.
    var host = function () {
      if (guide.hasAttribute("data-host-saved")) return guide.getAttribute("data-host");
      return (quizBox && quizBox.getAttribute("data-canvas-host")) || store("canvas-host") || guide.getAttribute("data-host");
    };
    // Where Canvas opens for the file: this sheet's course's Course Analytics
    // if we know them, else the list of courses.
    var analyticsUrl = function () {
      var tool = KNOWN_TOOLS[host()];
      if (!savedCourse) return "https://" + host() + "/courses";
      return "https://" + host() + "/courses/" + savedCourse
        + (tool ? "/external_tools/" + tool + "?launch_type=course_navigation" : "");
    };

    // Something to show in the guide: its step of the class-list questions
    // comes into view, and any folded section around it opens.
    var reveal = function (el) {
      for (var d = el.closest("details"); d; d = d.parentElement && d.parentElement.closest("details")) d.open = true;
      var step = stepBox.getAttribute && stepBox.getAttribute("data-step");
      if (quizBox && step) quizBox.dispatchEvent(new CustomEvent("quiz-go", { detail: step }));
    };

    // Canvas beside this page, for downloading the file: a window on the
    // right (or a tab), with the picture steps on the left. It's cut off from
    // this page (no window.opener): it only needs to show Canvas.
    var helper = null;
    var watching = null;
    var download = guide.querySelector("[data-canvas-download]");
    var done = guide.querySelector("[data-canvas-close]");
    var guideOn = function (on) {
      document.documentElement.classList.toggle("canvas-beside", on);
      if (done) done.hidden = !on;
    };
    var watch = function () {
      clearInterval(watching);
      watching = setInterval(function () {
        if (!helper || helper.closed) { clearInterval(watching); guideOn(false); }
      }, 1000);
    };
    var openBeside = function (url) {
      if (helper && !helper.closed) {
        try { helper.location.href = url; helper.focus(); return; } catch (err) { /* opened again below */ }
      }
      var width = Math.min(screen.availWidth - 380, Math.max(760, Math.round(screen.availWidth * 0.6)));
      helper = window.open("", "scheduler-canvas", "popup=yes,width=" + width + ",height=" + screen.availHeight
        + ",left=" + ((screen.availLeft || 0) + screen.availWidth - width) + ",top=" + (screen.availTop || 0))
        || window.open("", "scheduler-canvas");
      if (!helper) return;
      try { helper.opener = null; } catch (err) { /* fine */ }
      helper.location.href = url;
      guideOn(true);
      watch();
    };
    if (download) {
      guide.querySelector("[data-canvas-beside]").addEventListener("click", function () { openBeside(analyticsUrl()); });
      done.addEventListener("click", function () {
        guideOn(false);
        if (helper && !helper.closed) helper.close();
      });
    }

    // A class list, here to look over before it's added.
    var arrived = guide.querySelector("[data-canvas-arrived]");
    var showArrived = function (list, origin, how) {
      reveal(arrived);
      var people = list.students.filter(function (p) { return p && typeof p === "object" && (p.name || p.email); })
        .map(function (p) { return { name: String(p.name || "").slice(0, 120), email: String(p.email || "").slice(0, 254) }; });
      var emails = people.filter(function (p) { return p.email; }).length;
      var count = people.length + (people.length === 1 ? " student" : " students");
      var course = String(list.course || "your Canvas course").slice(0, 200);
      arrived.textContent = "";
      var add = function (tag, text, cls, into) {
        var el = document.createElement(tag);
        if (text) el.textContent = text;
        if (cls) el.className = cls;
        (into || arrived).appendChild(el);
        return el;
      };
      add("p", "From Canvas · " + origin.replace(/^https?:\/\//, ""), "eyebrow");
      add("h4", count + " from " + course, "canvas-arrived-title");
      add("p", emails === people.length ? "All with email addresses, so each can sign in with an emailed code."
        : emails ? emails + " with email addresses. The rest sign in with a PIN (usually students who haven't "
          + "accepted Canvas's invitation yet)."
        : "Canvas didn't share their email addresses, so they'll sign in with a PIN.", "mini-hint");
      var names = add("details", "", "arrived-names");
      add("summary", "See the names", "", names);
      var ol = add("ol", "", "arrived-list", names);
      people.forEach(function (p) {
        add("li", (p.name || p.email) + (p.name && p.email ? " · " + p.email : ""), "", ol);
      });
      var form = add("form", "", "arrived-form");
      form.method = "post";
      form.action = guide.getAttribute("data-list-url");
      var field = function (name, value) {
        var input = document.createElement("input");
        input.type = "hidden";
        input.name = name;
        input.value = value;
        form.appendChild(input);
      };
      field("csrf_token", CSRF);
      field("list", JSON.stringify({ type: "scheduler-class-list", v: 2, key: "", courseId: String(list.courseId || ""),
                                     course: course, host: origin.replace(/^https?:\/\//, ""), students: people }));
      field("how", how); // read from Canvas for this page itself
      if (guide.hasAttribute("data-setup")) field("setup", "1");
      var go = document.createElement("button");
      go.type = "submit";
      go.className = "btn btn-primary btn-large";
      go.textContent = "Add " + count;
      form.appendChild(go);
      arrived.hidden = false;
      arrived.scrollIntoView({ block: "center", behavior: "smooth" });
      go.focus({ preventScroll: true });
    };

    // Scheduler Helper. It answers only this site (its manifest says so).
    // Chrome and Edge talk to it over a port (which keeps it awake through a
    // Canvas sign-in); Safari, by single messages.
    var helperBox = guide.querySelector("[data-helper]");
    var helperIds = (guide.getAttribute("data-helper-ids") || "").split(",").filter(Boolean);
    var storeUrl = isSafari ? guide.getAttribute("data-helper-safari") : (isChromium ? guide.getAttribute("data-helper-store") : "");
    var runtime = function () {
      if (window.browser && browser.runtime && browser.runtime.sendMessage) return browser.runtime;
      if (window.chrome && chrome.runtime && chrome.runtime.sendMessage) return chrome.runtime;
      return null;
    };
    var helperId = null;
    var helperHosts = [];
    var upload = guide.querySelector("[data-canvas-download]");
    // With no helper to offer, the file is the way: open, and said plainly.
    var uploadFirst = function () {
      if (!upload) return;
      upload.open = true;
      upload.classList.add("is-first");
      var title = upload.querySelector("[data-upload-title]");
      if (title) title.textContent = title.getAttribute("data-upload-title");
    };
    if (!guide.querySelector("[data-connect]") && (!helperBox || !helperIds.length)) uploadFirst();
    if (helperBox && helperIds.length) {
      var helperSaid = guide.querySelector("[data-helper-said]");
      var helperCourses = guide.querySelector("[data-helper-courses]");
      var helperGo = guide.querySelector("[data-helper-go]");
      var wantsHelper = false; // pressed "Add Scheduler Helper": carry on once it's there
      var helperSay = function (text) { helperSaid.textContent = text; helperSaid.hidden = !text; };
      // The browser's own words in the install steps.
      var browserName = isEdge ? "Edge" : isSafari ? "Safari" : "Chrome";
      guide.querySelectorAll("[data-browser-name]").forEach(function (el) { el.textContent = browserName; });
      guide.querySelectorAll("[data-steps-for]").forEach(function (el) {
        el.hidden = el.getAttribute("data-steps-for") !== browserName.toLowerCase();
      });
      var call = function (id, msg, done) {
        var rt = runtime();
        var finished = false;
        var finish = function (answer) { if (!finished) { finished = true; done(answer || null); } };
        if (!rt) { finish(null); return; }
        try {
          if (window.browser && rt === browser.runtime) {
            Promise.resolve(rt.sendMessage(id, msg)).then(finish, function () { finish(null); });
          } else {
            rt.sendMessage(id, msg, function (answer) {
              var quiet = chrome.runtime.lastError; // (read, so Chrome doesn't log it)
              finish(quiet ? null : answer);
            });
          }
        } catch (err) { finish(null); }
      };
      var findHelper = function (then) {
        var left = helperIds.length;
        helperIds.forEach(function (id) {
          call(id, { type: "hello" }, function (answer) {
            left -= 1;
            if (answer && answer.ok && !helperId) {
              helperId = id;
              helperHosts = answer.hosts || [];
              then(true);
            } else if (!left && !helperId) {
              then(false);
            }
          });
        });
      };
      var showReady = function () {
        helperBox.hidden = false;
        guide.querySelector("[data-helper-install]").hidden = true;
        guide.querySelector("[data-helper-ready]").hidden = false;
      };
      findHelper(function (found) {
        if (found) { showReady(); return; }
        if (storeUrl) {
          helperBox.hidden = false;
          guide.querySelector("[data-helper-install]").hidden = false;
          guide.querySelector("[data-helper-add]").href = storeUrl;
        } else {
          uploadFirst(); // nothing to add in this browser (yet): the file it is
        }
      });
      guide.querySelector("[data-helper-add]").addEventListener("click", function () {
        wantsHelper = true;
        helperSay("Once " + browserName + " says Scheduler Helper was added, come back to this tab.");
      });
      // Back from the store with it added: the page notices, and carries on.
      var lookAgain = function () {
        if (helperId) return;
        findHelper(function (found) {
          if (!found) return;
          showReady();
          if (wantsHelper) { wantsHelper = false; helperGo.click(); }
        });
      };
      window.addEventListener("focus", lookAgain);
      document.addEventListener("visibilitychange", function () { if (!document.hidden) lookAgain(); });

      // One request to the helper, and its answer. A port where there is
      // one (it says when Canvas wants a sign-in); else one message, with a
      // word about signing in if it's taking a while.
      var ask = function (request, onAnswer) {
        var rt = runtime();
        var answered = false;
        var answer = function (msg) {
          if (answered) return;
          answered = true;
          onAnswer(msg || { type: "problem", why: "gone" });
        };
        if (rt && rt.connect && !isSafari) {
          var port = rt.connect(helperId);
          var beat = setInterval(function () { try { port.postMessage({ type: "ping" }); } catch (err) { /* gone */ } }, 20000);
          port.onMessage.addListener(function (msg) {
            if (msg && msg.type === "signin") {
              helperSay("Sign in to Canvas in the tab that just opened. This page carries on by itself after.");
              return;
            }
            clearInterval(beat);
            port.disconnect();
            answer(msg);
          });
          port.onDisconnect.addListener(function () { clearInterval(beat); answer(null); });
          port.postMessage(request);
          return;
        }
        var slow = setTimeout(function () {
          if (!answered) helperSay("Still working. If a Canvas tab opened, sign in there; this page carries on by itself.");
        }, 4000);
        call(helperId, request, function (msg) { clearTimeout(slow); answer(msg); });
      };
      var trouble = function (why) {
        helperGo.disabled = false;
        helperSay(why === "forbidden" ? "Canvas won't share that course's class list with you. Is it a course you teach?"
          : why === "missing" ? "Canvas couldn't find that course. Press Get my class list from Canvas and pick it again."
          : why === "signin" || why === "closed" ? "Canvas sign-in didn't finish. Press Get my class list from Canvas to try again."
          : why === "access" ? "Safari needs your OK first: in Safari's menu bar choose Safari, then Settings, then "
            + "Extensions; click Scheduler Helper, then Edit Websites, and set " + host() + " to Allow. Then press Get my "
            + "class list from Canvas again."
          : why === "host" ? "Scheduler Helper doesn't know your school's Canvas yet. Upload the student file instead (below)."
          : "Canvas didn't answer just now. Wait a minute, then press Get my class list from Canvas again.");
        if (why === "host") uploadFirst();
      };
      var getStudents = function (courseId, label) {
        helperSay("Getting the class list" + (label ? " for " + label : "") + "…");
        ask({ type: "students", host: host(), courseId: courseId }, function (msg) {
          helperGo.disabled = false;
          if (msg.type !== "students") { trouble(msg.why); return; }
          var c = msg.course || {};
          var name = (c.name || label || "your Canvas course") + (c.term ? " (" + c.term + ")" : "");
          if (!msg.students || !msg.students.length) {
            helperSay(name + " has no students in Canvas yet. If it isn't published, or students just enrolled, Canvas may "
              + "not list them for a day or two.");
            return;
          }
          helperSay("");
          showArrived({ course: name, courseId: String(c.id || courseId), students: msg.students }, "https://" + host(),
                      "helper");
        });
      };
      var current = function (c) {
        var from = Date.parse(c.start || ""), to = Date.parse(c.end || ""), today = Date.now();
        return (isNaN(from) || from <= today) && (isNaN(to) || to >= today);
      };
      helperGo.addEventListener("click", function () {
        if (helperHosts.length && helperHosts.indexOf(host()) < 0) { trouble("host"); return; }
        helperGo.disabled = true;
        helperCourses.hidden = true;
        if (savedCourse) { getStudents(savedCourse, ""); return; } // this sheet's course, from last time
        helperSay("Getting your courses from Canvas…");
        ask({ type: "courses", host: host() }, function (msg) {
          helperGo.disabled = false;
          if (msg.type !== "courses") { trouble(msg.why); return; }
          var courses = (msg.courses || []).slice().sort(function (a, b) { return (current(b) ? 1 : 0) - (current(a) ? 1 : 0); });
          if (!courses.length) { helperSay("Canvas doesn't list you as a teacher of any course."); return; }
          if (courses.length === 1) { getStudents(courses[0].id, courses[0].name); return; }
          helperSay("");
          helperCourses.textContent = "";
          var question = document.createElement("p");
          question.textContent = "Which course is this sign-up sheet for?";
          helperCourses.appendChild(question);
          courses.forEach(function (c) {
            var b = document.createElement("button");
            b.type = "button";
            b.className = "quiz-choice";
            b.textContent = c.name + (c.term ? " · " + c.term : "")
              + (typeof c.students === "number" ? " · " + c.students + (c.students === 1 ? " student" : " students") : "")
              + (c.published === false ? " (not published yet)" : "");
            b.addEventListener("click", function () { helperCourses.hidden = true; getStudents(c.id, c.name); });
            helperCourses.appendChild(b);
          });
          helperCourses.hidden = false;
        });
      });
    }

    // The drop box (for the downloaded file) shows on this step only with
    // the file steps open.
    var drop = quizBox && quizBox.querySelector("[data-quiz-drop]");
    var dropShown = function () { if (drop && download && !stepBox.hidden) drop.hidden = !download.open; };
    if (download) download.addEventListener("toggle", dropShown);
    dropShown();

    // Back from Canvas (this tab looked at again) after opening it for the
    // file: point at the last step, the box for the file.
    var canvasSteps = guide.querySelector("[data-canvas-steps]");
    var welcome = guide.querySelector("[data-canvas-welcome]");
    var went = false;
    guide.querySelectorAll("[data-canvas-went]").forEach(function (link) {
      link.addEventListener("click", function () { went = true; });
    });
    var cameBack = function () {
      if (!went || document.visibilityState === "hidden" || !welcome) return;
      went = false;
      welcome.textContent = "Welcome back. Downloaded the student file? Drag it into the box below, or click the box "
        + "and choose it. Not there yet? The pictures above show where to click in Canvas.";
      welcome.hidden = false;
      if (canvasSteps) canvasSteps.querySelectorAll("[data-canvas-step]").forEach(function (li) {
        li.classList.toggle("is-next", li.getAttribute("data-canvas-step") === "drop");
      });
      if (drop && !drop.hidden) drop.scrollIntoView({ block: "center", behavior: "smooth" });
    };
    document.addEventListener("visibilitychange", cameBack);
    window.addEventListener("focus", cameBack);
  }

  // Name fields start read-only so browsers don't autofill a stranger's name
  // into them; unlock on focus.
  document.querySelectorAll("input[data-unlock]").forEach(function (input) {
    var unlock = function () { input.removeAttribute("readonly"); };
    input.addEventListener("focus", unlock);
    input.addEventListener("pointerdown", unlock);
    if (document.activeElement === input) unlock();
  });
})();

// The site's one bit of motion: blocks further down a page rise in as
// they're scrolled to (blocks already in view rise in by CSS alone). Only
// what starts below the window is hidden, so nothing visible blinks, and
// none of it runs for readers who ask for reduced motion.
(function () {
  if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  if (!("IntersectionObserver" in window)) return;
  var rise = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (!entry.isIntersecting) return;
      entry.target.classList.add("is-in");
      rise.unobserve(entry.target);
    });
  }, { rootMargin: "0px 0px -6% 0px", threshold: 0.06 });
  var fold = window.innerHeight * 0.94;
  var blocks = "main > .card, main > section, .mode-frame > .card, .mode-frame > section, [data-reveal]";
  document.querySelectorAll(blocks).forEach(function (el) {
    if (el.getBoundingClientRect().top > fold) {
      el.classList.add("reveal");
      rise.observe(el);
    }
  });
})();

// A soft light follows the pointer across a [data-glow] block: only the
// front page's (see style.css). Mice and trackpads only, and not for
// readers who ask for reduced motion.
(function () {
  var ask = function (query) { return window.matchMedia && window.matchMedia(query).matches; };
  if (ask("(prefers-reduced-motion: reduce)") || !ask("(hover: hover) and (pointer: fine)")) return;
  document.querySelectorAll("[data-glow]").forEach(function (card) {
    card.addEventListener("pointermove", function (e) {
      var box = card.getBoundingClientRect();
      card.style.setProperty("--mx", Math.round(e.clientX - box.left) + "px");
      card.style.setProperty("--my", Math.round(e.clientY - box.top) + "px");
      card.classList.add("is-lit");
    });
    card.addEventListener("pointerleave", function () { card.classList.remove("is-lit"); });
  });
})();
