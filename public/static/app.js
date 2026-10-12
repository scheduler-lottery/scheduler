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
  // A problem with a file, back at the box it was chosen in: said under
  // that box, where the professor is looking, not over the page.
  var hashed = location.hash && document.getElementById(decodeURIComponent(location.hash.slice(1)));
  var boxSaid = hashed && hashed.matches && hashed.matches("input[type=file]") && hashed.form
    && hashed.form.querySelector("[data-dropzone-status]");
  if (stack && boxSaid) {
    stack.querySelectorAll(".flash-error").forEach(function (note) {
      boxSaid.textContent = note.querySelector(".flash-text").textContent;
      boxSaid.classList.add("is-error");
      note.remove();
    });
    if (!stack.querySelector(".flash")) { stack.remove(); stack = null; }
  }
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
      var flashShown = false;
      var once = function () { if (!flashShown) { flashShown = true; arrive(); } };
      document.addEventListener("page-shown", once);
      setTimeout(once, 3100); // the page shows by itself after 3 seconds
    }
  }

  // Once the page shows (a page still hidden while its fonts load can't
  // take focus): run `then`.
  var whenShown = function (then) {
    if (document.documentElement.classList.contains("fonts-loading")) document.addEventListener("page-shown", then);
    else then();
  };
  // The box a page asks to be typed in, and a report of what just happened
  // (a class list just added: data-focus-on-load), get the focus, so
  // keyboards start there and screen readers read it out.
  whenShown(function () {
    var first = document.querySelector("[data-focus-on-load]") || document.querySelector("[autofocus]");
    if (first && (!document.activeElement || document.activeElement === document.body)) {
      first.focus({ preventScroll: first.hasAttribute("autofocus") });
    }
  });

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

  // A new class list's review: students taken off by hand stay off, unless
  // "Put … back" is ticked; then the buttons say they put them back too.
  document.querySelectorAll("[data-put-back]").forEach(function (box) {
    var say = function () {
      box.form.querySelectorAll("[data-put-back-say]").forEach(function (span) { span.hidden = !box.checked; });
    };
    box.addEventListener("change", say);
    say(); // (ticked already, back on this page)
  });

  // The helper's page in a browser that can't add it (Safari, Firefox, a
  // phone): how to use it in Chrome instead of a store button.
  (function () {
    var ua = navigator.userAgent;
    var phone = /Android|iPhone|iPad|iPod|Mobile/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
    if (/Chrome\//.test(ua) && !/OPR\//.test(ua) && !phone) return;
    document.querySelectorAll("[data-chromium-only]").forEach(function (el) { el.hidden = true; });
    document.querySelectorAll("[data-not-chromium]").forEach(function (el) { el.hidden = false; });
  })();

  // After a class list Scheduler Helper brought: remove it from Chrome, if
  // the professor wants. The helper asks Chrome to remove it; 1.0.0 can't,
  // so the steps in Chrome's own menu, and the page notices once it's gone.
  // "Keep Scheduler Helper" is remembered on this computer.
  document.querySelectorAll("[data-helper-remove]").forEach(function (box) {
    var rt = window.chrome && chrome.runtime && chrome.runtime.sendMessage ? chrome.runtime : null;
    var ids = (box.getAttribute("data-helper-ids") || "").split(",").filter(Boolean);
    if (!rt || !ids.length || store("helper-keep")) return;
    var ua = navigator.userAgent;
    var browserName = /Edg\//.test(ua) ? "Edge" : /OPR\//.test(ua) ? "Opera" : "Chrome";
    box.querySelectorAll("[data-browser-name]").forEach(function (el) { el.textContent = browserName; });
    var removeWord = box.querySelector("[data-remove-word]");
    if (removeWord) removeWord.textContent = browserName === "Edge" ? "Remove from Microsoft Edge" : "Remove from " + browserName;
    var question = box.querySelector("[data-helper-remove-ask]");
    var buttons = box.querySelector(".helper-remove-buttons");
    var go = box.querySelector("[data-helper-remove-go]");
    var steps = box.querySelector("[data-helper-remove-steps]");
    var said = box.querySelector("[data-helper-remove-said]");
    var send = function (id, msg, then) {
      var over = false;
      var finish = function (answer) { if (!over) { over = true; then(answer || null); } };
      try {
        rt.sendMessage(id, msg, function (answer) { var quiet = chrome.runtime.lastError; finish(quiet ? null : answer); });
      } catch (err) { finish(null); }
      setTimeout(function () { finish(null); }, 3000);
    };
    var found = null;
    var can = [];
    ids.forEach(function (id) {
      send(id, { type: "hello" }, function (answer) {
        if (!answer || !answer.ok || found) return;
        found = id;
        can = answer.can || [];
        if (can.indexOf("remove") < 0) go.textContent = "Show me how to remove it";
        box.hidden = false;
      });
    });
    var settle = function (text) { // (said, with the focus there, as the buttons it came from go)
      said.textContent = text;
      said.tabIndex = -1;
      said.focus({ preventScroll: true });
    };
    var gone = function () {
      question.hidden = true;
      buttons.hidden = true;
      steps.hidden = true;
      settle("Done: Scheduler Helper is removed from " + browserName + ". If you need it again, this page offers to add "
        + "it back.");
    };
    // Removed in Chrome's own menu: noticed by itself (for ten minutes).
    var watching = null;
    var watch = function () {
      if (watching) return;
      var until = Date.now() + 10 * 60 * 1000;
      watching = setInterval(function () {
        send(found, { type: "hello" }, function (answer) {
          if (answer && answer.ok) { if (Date.now() > until) clearInterval(watching); return; }
          clearInterval(watching);
          gone();
        });
      }, 2000);
    };
    var byHand = function (first) {
      buttons.hidden = true;
      steps.hidden = false;
      settle(first + "In " + browserName + "'s own menu:");
      watch();
    };
    box.querySelector("[data-helper-remove-keep]").addEventListener("click", function () {
      store("helper-keep", "1");
      question.hidden = true;
      buttons.hidden = true;
      settle("OK: Scheduler Helper stays in " + browserName + ".");
    });
    go.addEventListener("click", function () {
      if (!found) return;
      if (can.indexOf("remove") < 0) { byHand(""); return; }
      said.textContent = "Removing Scheduler Helper…";
      send(found, { type: "remove" }, function () {
        setTimeout(function () { // (gone, it no longer answers)
          send(found, { type: "hello" }, function (still) {
            if (still && still.ok) { byHand(browserName + " didn't remove it. "); return; }
            gone();
          });
        }, 1500);
      });
    });
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
    var label = btn.textContent;
    var back = null;
    btn.addEventListener("click", function () {
      var source = document.querySelector(btn.getAttribute("data-copy"));
      if (!source) return;
      var text = source.value;
      var done = function () {
        btn.textContent = "Copied";
        clearTimeout(back);
        back = setTimeout(function () { btn.textContent = label; }, 1800);
        var said = btn.parentNode.querySelector("[data-copy-said]"); // (what's next, where it stays)
        if (said) said.textContent = said.getAttribute("data-copy-said");
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
  // A link to a file box (back from a wrong file): its question shows, and
  // the box is ready to click, with its step marked.
  var reveal = function (scroll) {
    var target = location.hash && document.getElementById(decodeURIComponent(location.hash.slice(1)));
    if (!target) return;
    for (var el = target; el; el = el.parentElement) {
      if (el.tagName === "DETAILS") el.open = true;
    }
    var step = target.closest(".quiz-step[data-step]");
    var quiz = step && step.closest("[data-quiz]");
    if (quiz) {
      var name = step.getAttribute("data-step");
      try { sessionStorage.setItem("quiz-step-" + quiz.getAttribute("data-sheet"), name); } catch (e) { /* fine */ }
      quiz.dispatchEvent(new CustomEvent("quiz-go", { detail: name }));
    }
    var isFile = target.matches("input[type=file]");
    var shown = isFile ? (target.closest("[data-canvas-step]") || target.form || target) : target;
    if (shown.matches("[data-canvas-step]")) shown.classList.add("is-next");
    // (Once the page is laid out, the questions show just the one step,
    // and the browser has made its own jump to the link's target.)
    var place = function () {
      if (scroll) shown.scrollIntoView({ block: isFile ? "center" : "start" });
      if (isFile) whenShown(function () { target.focus({ preventScroll: true }); });
    };
    if (document.readyState === "complete") setTimeout(place, 0);
    else window.addEventListener("load", function () { setTimeout(place, 0); });
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

  // Class-list upload: pick a file (or drop one) and it uploads right away.
  // A dropped folder (Safari unzips Canvas's download into one), or both
  // files from it dropped at once, gives up its student file, not the
  // grades file beside it; a file dropped anywhere else on the page goes to
  // the box in view, rather than replacing the page.
  var pickList = function (items) { // files, or a folder's entries: anything with a .name
    var lists = items.filter(function (x) { return /\.(csv|tsv|txt|xlsx|zip)$/i.test(x.name); });
    return lists.filter(function (x) { return /student/i.test(x.name); })[0] || lists[0] || null;
  };
  var dropFolder = function (entry, done) {
    var reader = entry.createReader();
    reader.readEntries(function (entries) {
      var pick = pickList(entries.filter(function (x) { return x.isFile; }));
      if (!pick) { done(null); return; }
      pick.file(function (file) { done(file); }, function () { done(null); });
    }, function () { done(null); });
  };
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
    // Canvas's file: the window to choose it opens right in Downloads, with
    // only Canvas's kinds of files to pick (Chrome and Edge; elsewhere, the
    // usual window, and the step says to click Downloads in it).
    var plainWindow = false;
    if (form.hasAttribute("data-from-downloads") && typeof window.showOpenFilePicker === "function") {
      input.addEventListener("click", function (e) {
        if (plainWindow) return;
        e.preventDefault();
        window.showOpenFilePicker({
          startIn: "downloads",
          excludeAcceptAllOption: true,
          types: [{ description: "Canvas's file", accept: {
            "application/zip": [".zip"], "text/csv": [".csv", ".tsv", ".txt"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"] } }],
        }).then(function (handles) { return handles[0].getFile(); }).then(function (file) {
          var one = new DataTransfer();
          one.items.add(file);
          input.files = one.files;
          upload();
        }).catch(function (err) {
          if (err && err.name === "AbortError") return; // closed without choosing: nothing to do
          plainWindow = true; // (that window couldn't open here: the usual one, next click)
          if (status) status.textContent = "Click the box again, then Downloads on the left of the window that opens.";
          zone.scrollIntoView({ block: "center" });
        });
      });
    }
    form.takeDrop = function (dt) {
      var item = dt.items && dt.items[0];
      var entry = item && item.webkitGetAsEntry && item.webkitGetAsEntry();
      if (entry && entry.isDirectory) {
        dropFolder(entry, function (file) {
          if (!file) {
            if (status) status.textContent = "That's a folder. Open it, and choose the file with “student” in its name.";
            return;
          }
          var box = new DataTransfer();
          box.items.add(file);
          input.files = box.files;
          upload();
        });
        return;
      }
      if (!dt.files.length) return;
      var one = new DataTransfer(); // (the box takes one file)
      one.items.add(pickList(Array.prototype.slice.call(dt.files)) || dt.files[0]);
      input.files = one.files;
      upload();
    };

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
      if (e.dataTransfer) form.takeDrop(e.dataTransfer);
    });
  });
  var carryingFiles = function (e) {
    return e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types || [], "Files") >= 0;
  };
  ["dragover", "drop"].forEach(function (type) {
    document.addEventListener(type, function (e) {
      if (!carryingFiles(e) || e.defaultPrevented) return;
      e.preventDefault(); // (otherwise the browser leaves this page to show the file)
      if (type !== "drop") return;
      var form = Array.prototype.filter.call(document.querySelectorAll("form[data-dropzone]"), function (f) {
        return f.offsetParent !== null;
      })[0];
      if (form && form.takeDrop) form.takeDrop(e.dataTransfer);
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
    // A professor who has brought a list from Canvas before starts at the
    // Canvas step (with a way to the others), not at "Do you use Canvas?".
    var first = quiz.getAttribute("data-first") || "start";
    var current = first;
    var show = function (name, focus) {
      if (!quiz.querySelector('[data-step="' + name + '"]')) name = "start";
      current = name;
      steps.forEach(function (step) { step.hidden = step.getAttribute("data-step") !== name; });
      if (drop) drop.hidden = name !== "file"; // (the Canvas step has its own box)
      quiz.querySelectorAll("[data-first-only]").forEach(function (el) { el.hidden = !(name === first && first !== "start"); });
      var leave = quiz.parentNode.querySelector("[data-quiz-leave]");
      if (leave) leave.hidden = !(name === "start" || (name === first && !trail.length)); // later, the question's own Back is the one
      try { sessionStorage.setItem(key, name); } catch (e) { /* fine */ }
      var question = quiz.querySelector('[data-step="' + name + '"] .quiz-q');
      if (focus && question) {
        // The question in view, and as much of its step under it as fits (the
        // Canvas step's Add button and its steps, say).
        question.focus({ preventScroll: true });
        var shownStep = question.closest(".quiz-step");
        if (shownStep && shownStep.getBoundingClientRect().bottom > window.innerHeight) shownStep.scrollIntoView({ block: "start" });
        else if (question.getBoundingClientRect().top < 0) question.scrollIntoView({ block: "start" });
      }
    };
    var shownAt = 0; // when a click last brought up a step
    var go = function (name) { trail.push(current); show(name, true); };
    // Another part of the page bringing its step into view (the Canvas
    // step, when a class list arrives from Canvas).
    quiz.addEventListener("quiz-go", function (e) { if (current !== e.detail) go(e.detail); });
    // A double-click's second click lands on whatever its first click just
    // brought up ("Change", "Type or paste…"): ignore it.
    quiz.addEventListener("click", function (e) {
      if (e.detail > 1 && Date.now() - shownAt < 1500) { e.preventDefault(); e.stopPropagation(); }
    }, true);
    quiz.addEventListener("click", function (e) {
      var to = e.target.closest("[data-go]");
      if (to) { go(to.getAttribute("data-go")); shownAt = Date.now(); return; }
      if (e.target.closest("[data-back]")) { show(trail.pop() || "start", true); shownAt = Date.now(); }
    });
    var saved = null;
    try { saved = sessionStorage.getItem(key); } catch (e) { saved = null; }
    show(saved || first, false);

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
          quiz.dispatchEvent(new CustomEvent("canvas-school")); // (the Canvas step's offer may change)
          quiz.querySelectorAll("[data-school-name]").forEach(function (el) { el.textContent = answer.data.name; });
          quiz.querySelectorAll("[data-school-domain]").forEach(function (el) {
            el.textContent = answer.data.name === answer.data.domain ? "" : "(" + answer.data.domain + ")";
          });
          // (Next time, "Change" asks the short question again; the search
          // keeps what was found, for "a different school".)
          if (search && search.hasAttribute("data-start-hidden")) search.hidden = true;
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
                status.textContent = !(data.schools || []).length
                  ? "No school by that name. Try another spelling, or use “My school isn't listed” below."
                  : data.more ? "Pick yours. Not there? Type more of its name, like “University of Chicago”."
                  : "Pick yours.";
              }
            })
            .catch(function () { if (status) status.textContent = "Couldn't search just now. Try again, or type the address below."; });
        }, 300);
      });
    }
  });

  // Connect Canvas's course choice: the course's name goes with its number
  // (for the review and the sheet), set from the button pressed.
  // One course only: a second click (or a double-click) would send the
  // same one-time Canvas permission again, after it's been given back.
  document.querySelectorAll(".connect-courses").forEach(function (form) {
    form.addEventListener("click", function (e) {
      var pick = e.target.closest("button[data-name]");
      if (pick) form.querySelector("[data-course-name]").value = pick.getAttribute("data-name");
    });
    form.addEventListener("submit", function (e) {
      if (form.hasAttribute("data-sent")) { e.preventDefault(); return; }
      form.setAttribute("data-sent", "");
      form.setAttribute("aria-busy", "true");
    });
  });

  // The Canvas step: the class list from Canvas, the simple way.
  // - Scheduler Helper (extension/, in the Chrome Web Store), once added to
  //   Chrome: one click here and it brings the list. Until it's added, the
  //   step offers to add it: in Chrome and Edge, on a computer, for a school
  //   whose Canvas it reads. In another browser (Safari, Firefox), it says
  //   how to open this page in Chrome instead.
  // - Otherwise (and always, folded under it) Canvas's own student file:
  //   Canvas opens beside this page, pictures show where to click, and the
  //   file goes in the box at the end.
  // (Connect Canvas, when the school has issued a key, is just a link.)
  // Whichever way, the list shows here first; nothing changes until the
  // professor presses the button under it.
  var guide = document.querySelector("[data-canvas-guide]");
  if (guide) (function () { // (its own scope: its names never clash with the rest of this file's)
    var quizBox = guide.closest("[data-quiz]");
    var stepBox = guide.closest(".quiz-step") || guide.parentNode;
    var savedCourse = guide.getAttribute("data-canvas-course-id") || "";
    var forgotCourse = ""; // this sheet's course, which Canvas no longer has: the next list's course replaces it
    var hasList = guide.hasAttribute("data-has-list");
    var ua = navigator.userAgent;
    var isEdge = /Edg\//.test(ua);
    var isSafari = /Safari\//.test(ua) && !/Chrome\/|Chromium\/|CriOS\/|Edg\/|Firefox\/|FxiOS\/|OPR\//.test(ua);
    var isOpera = /OPR\//.test(ua);
    // Chrome, Edge, and the like (Opera, with the helper added from Chrome's store, too).
    var isChromium = /Chrome\//.test(ua) && (!isOpera || !!(window.chrome && chrome.runtime && chrome.runtime.sendMessage));
    var isWindows = /Windows NT/.test(ua);
    // Phones and tablets can't add browser helpers, and Canvas's file is
    // easier on a computer.
    var isPhone = /Android|iPhone|iPad|iPod|Mobile/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
    var macSafari = isSafari && !isPhone;
    // Chrome and Edge open the file window right in Downloads (the box does
    // that: above).
    var picker = !isPhone && typeof window.showOpenFilePicker === "function";
    // Course Analytics' tool number at schools we know of, so Canvas can
    // open right on it for a sheet whose course is known.
    var KNOWN_TOOLS = { "canvas.northwestern.edu": "48685" };
    store("canvas-host", null); // (from an older version of this page)

    // The school's Canvas: as chosen on this page (or saved), else the one
    // their email suggests.
    var host = function () {
      return (quizBox && quizBox.getAttribute("data-canvas-host")) || guide.getAttribute("data-host");
    };
    // Where Canvas opens for the file: this sheet's course's Course Analytics
    // at a school whose tool we know, else the course's page, else (no
    // course yet) the Dashboard, as the pictures show.
    var canvasBase = function () { // (http only for a stand-in Canvas on this computer)
      return (/^(127\.0\.0\.1|localhost)(:\d+)?$/.test(host()) ? "http://" : "https://") + host();
    };
    var analyticsUrl = function () {
      var tool = KNOWN_TOOLS[host()];
      if (!savedCourse) return canvasBase() + "/";
      return canvasBase() + "/courses/" + savedCourse
        + (tool ? "/external_tools/" + tool + "?launch_type=course_navigation" : "");
    };
    // Words for this kind of computer: Safari on a Mac unzips Canvas's
    // download into a folder (Safari on a phone doesn't); Windows hides the
    // .zip at the end of a name; phones tap, and have no window with
    // Downloads on its left; Chrome's window opens in Downloads.
    var shown = function (selector, on) { stepBox.querySelectorAll(selector).forEach(function (el) { el.hidden = !on; }); };
    if (macSafari) { shown("[data-safari-only]", true); shown("[data-not-safari]", false); }
    if (isWindows) { shown("[data-windows-only]", true); shown("[data-not-windows]", false); }
    if (isPhone) { shown("[data-phone-only]", true); shown("[data-desktop-only]", false); }
    if (picker) { shown("[data-picker-downloads]", true); shown("[data-picker-plain]", false); }
    var pageName = document.title.split(" — ")[0];
    guide.querySelectorAll("[data-page-title]").forEach(function (el) { el.textContent = pageName; });

    // Something to show in the guide: its step of the class-list questions
    // comes into view, and any folded section around it opens.
    var showHere = function (el) {
      for (var d = el.closest("details"); d; d = d.parentElement && d.parentElement.closest("details")) d.open = true;
      var step = stepBox.getAttribute && stepBox.getAttribute("data-step");
      if (quizBox && step) quizBox.dispatchEvent(new CustomEvent("quiz-go", { detail: step }));
    };

    // Canvas, for downloading the file: a new tab each press, with the
    // browser's usual top (where its downloads show), cut off from this
    // page (noopener). (Once cut off, a page can't bring the old tab
    // forward, and a button that does nothing is worse than a second tab.)
    var download = guide.querySelector("[data-canvas-download]");
    var opener = guide.querySelector("[data-canvas-open]");
    var dropBox = guide.querySelector("form[data-dropzone]");
    var fileInput = dropBox && dropBox.querySelector("input[type=file]");
    var steps = guide.querySelector("[data-canvas-steps]");
    var starts = guide.querySelector("[data-canvas-starts]");
    // Where Canvas will open, said, with that step marked: with a course
    // known, past the Dashboard (and at a school whose tool we know, right
    // on Course Analytics).
    var showStart = function () {
      var at = !savedCourse ? "signin" : KNOWN_TOOLS[host()] ? "list" : "analytics";
      if (steps) steps.querySelectorAll("[data-canvas-step]").forEach(function (li) {
        li.classList.toggle("is-next", at !== "signin" && li.getAttribute("data-canvas-step") === at);
      });
      if (!starts) return;
      var course = guide.getAttribute("data-canvas-course") || "this class";
      starts.hidden = at === "signin";
      starts.textContent = at === "list" ? "Canvas opens right on Course Analytics for " + course + ", so start at step 3."
        : at === "analytics" ? "Canvas opens right on " + course + "'s page, so start at step 2." : "";
    };
    showStart();
    opener.addEventListener("click", function () {
      showStart();
      window.open(analyticsUrl(), "_blank", "noopener");
    });
    // A file already saved (yesterday, say): straight to choosing it.
    var chooseNow = guide.querySelector("[data-canvas-choose]");
    if (chooseNow && fileInput) chooseNow.addEventListener("click", function () { fileInput.click(); });
    // What happened to the helper, said where the professor now looks: at
    // Canvas's file.
    var fileSaid = guide.querySelector("[data-file-said]");
    var fileSay = function (text) { if (fileSaid) fileSaid.textContent = text; };

    // A class list, here to look over before anything changes.
    var arrivedBox = guide.querySelector("[data-canvas-arrived]");
    var ready = guide.querySelector("[data-helper-ready]");
    var help = stepBox.querySelector && stepBox.querySelector(".quiz-help");
    var upload = download;
    var connectBox = guide.querySelector("[data-connect]");
    var helperBox = guide.querySelector("[data-helper]");
    var helperId = null;
    // While a list waits here, it's the one thing to do.
    var tidy = function (on) {
      if (ready) ready.hidden = on || !helperId;
      if (helperBox && helperId) helperBox.hidden = on;
      if (help) help.hidden = on;
      if (upload && !upload.classList.contains("is-first")) upload.hidden = on;
    };
    var showArrived = function (list, origin, how) {
      showHere(arrivedBox);
      var people = list.students.filter(function (p) { return p && typeof p === "object" && (p.name || p.email); })
        .map(function (p) { return { name: String(p.name || "").slice(0, 120), email: String(p.email || "").slice(0, 254) }; });
      var emails = people.filter(function (p) { return p.email; }).length;
      var count = people.length + (people.length === 1 ? " student" : " students");
      var course = String(list.course || "your Canvas course").slice(0, 200);
      arrivedBox.textContent = "";
      var add = function (tag, text, cls, into) {
        var el = document.createElement(tag);
        if (text) el.textContent = text;
        if (cls) el.className = cls;
        (into || arrivedBox).appendChild(el);
        return el;
      };
      add("p", "From Canvas · " + origin.replace(/^https?:\/\//, ""), "eyebrow");
      var heading = add(guide.getAttribute("data-heading") || "h4",
                        (hasList ? count + " in " + course + " in Canvas now" : count + " from " + course),
                        "canvas-arrived-title");
      heading.id = "arrived-title-" + guide.getAttribute("data-sheet");
      var hint = null;
      if (!hasList) {
        hint = add("p", emails === people.length ? "All with email addresses, so each can sign in with an emailed code."
          : emails ? emails + " with email addresses. The rest sign in with a PIN (usually students who haven't "
            + "accepted Canvas's invitation yet)."
          : "Canvas didn't share their email addresses, so they'll sign in with a PIN.", "mini-hint");
        hint.id = "arrived-hint-" + guide.getAttribute("data-sheet");
        var names = add("details", "", "arrived-names");
        add("summary", people.length === 1 ? "See the name" : "See all " + people.length + " names", "", names);
        var ol = add("ol", "", "arrived-list", names);
        people.forEach(function (p) {
          add("li", (p.name || p.email) + (p.name && p.email ? " · " + p.email : ""), "", ol);
        });
      }
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
      field("list", JSON.stringify({ type: "scheduler-class-list", v: 2, courseId: String(list.courseId || ""),
                                     course: course, host: origin.replace(/^https?:\/\//, ""), students: people }));
      field("how", how);
      if (forgotCourse) field("forget_course", forgotCourse);
      if (guide.hasAttribute("data-setup")) field("setup", "1");
      var go = document.createElement("button");
      go.type = "submit";
      go.className = "btn btn-primary btn-large";
      go.textContent = hasList ? "Compare with my list" : "Add " + count;
      go.setAttribute("aria-describedby", heading.id + (hint ? " " + hint.id : ""));
      form.appendChild(go);
      if (how === "helper" && !hasList) {
        var other = add("button", "Wrong course? Pick a different one", "btn-link arrived-other");
        other.type = "button";
        other.addEventListener("click", function () {
          arrivedBox.hidden = true;
          savedCourse = ""; // ask which, this time
          askWhich = true;
          tidy(false);
          helperGo.click();
        });
      }
      tidy(true);
      arrivedBox.hidden = false;
      if (hasList) {
        // Updating: nothing changes until the next page, where who's new and
        // who's gone are shown; so straight there, no extra press. (The
        // button stays, in case the page doesn't move on.)
        add("p", "Comparing it with your list…", "mini-hint");
        form.submit();
        return;
      }
      arrivedBox.scrollIntoView({ block: "center", behavior: "smooth" });
      go.focus({ preventScroll: true });
    };

    // "Stuck?" speaks to the way on screen: Connect Canvas or Scheduler
    // Helper. (Its lines about Canvas's file always show: the file is
    // always there.)
    var showHelp = function () {
      if (!help) return;
      var route = connectBox && !connectBox.hidden ? "connect" : chromeBox && !chromeBox.hidden ? "chrome"
        : helperBox && !helperBox.hidden && !(upload && upload.classList.contains("is-first")) ? "helper" : "file";
      help.querySelectorAll('[data-for="helper"], [data-for="connect"], [data-for="chrome"]').forEach(function (p) {
        p.hidden = p.getAttribute("data-for") !== route;
      });
    };
    // With no helper to offer, the file is the way: open, and said plainly;
    // and back again, when a school it reads is chosen after all.
    var uploadTitle = upload && upload.querySelector("[data-upload-title]");
    var uploadWas = uploadTitle ? uploadTitle.textContent : "";
    var uploadFirst = function () {
      if (helperBox) helperBox.hidden = true;
      fileSay("");
      if (upload && !upload.classList.contains("is-first")) {
        upload.open = true;
        upload.classList.add("is-first");
        if (uploadTitle) uploadTitle.textContent = uploadTitle.getAttribute("data-upload-title");
        opener.classList.replace("btn-secondary", "btn-primary"); // the one thing to do first
      }
      showHelp();
    };
    var uploadSecond = function () {
      fileSay("");
      if (upload && upload.classList.contains("is-first")) {
        upload.classList.remove("is-first");
        upload.open = false;
        if (uploadTitle) uploadTitle.textContent = uploadWas;
        opener.classList.replace("btn-primary", "btn-secondary");
      }
      showHelp();
    };

    // Scheduler Helper. It answers only this site (its manifest says so).
    // The page talks to it over a port, which keeps it awake through a Canvas
    // sign-in.
    var helperIds = (guide.getAttribute("data-helper-ids") || "").split(",").filter(Boolean);
    var coveredHosts = (guide.getAttribute("data-helper-hosts") || "").split(",").filter(Boolean);
    var covered = function () { return coveredHosts.indexOf(host()) >= 0; };
    var storeUrl = isChromium ? guide.getAttribute("data-helper-store") : "";
    var runtime = function () { // (Chrome gives a page this once a helper that answers it is added)
      return window.chrome && chrome.runtime && chrome.runtime.sendMessage ? chrome.runtime : null;
    };
    var helperHosts = [];
    var helperCan = []; // what the helper says it can do besides reading Canvas (1.0.0 says nothing)
    var canOffer = function () { return !!helperBox && helperIds.length && !isPhone && covered(); };
    var offerHelper = null; // (decide, below, where a helper can be offered)
    var askWhich = guide.hasAttribute("data-ask-which"); // the course list, even when one matches (after "start over")
    // Another browser (Safari, Firefox): the helper is Chrome's, so how to
    // open this page in Chrome, with Canvas's file, folded, as the other way.
    var chromeBox = guide.querySelector("[data-helper-chrome]");
    var pageLink = guide.querySelector("[data-page-link]");
    if (pageLink) { // (opened in Chrome, it lands right on this step: at=canvas)
      var link = new URL(location.href);
      link.hash = "";
      link.searchParams.set("at", "canvas");
      pageLink.value = link.toString();
    }
    var offerChrome = function () {
      if (!chromeBox) { uploadFirst(); return; }
      helperBox.hidden = false;
      guide.querySelector("[data-helper-install]").hidden = true;
      if (ready) ready.hidden = true;
      chromeBox.hidden = false;
      uploadSecond();
    };
    if (connectBox && !connectBox.hidden) showHelp();
    else if (canOffer() && !isChromium) offerChrome();
    // (With no helper to offer, Canvas's file shows at once; a helper that's
    // there anyway, being tried out, takes its place when it answers.)
    else if (!canOffer() || !storeUrl) uploadFirst();
    // The school changed on this page: what's offered may change with it.
    // Connect Canvas reads only the school whose key this site has.
    if (quizBox) quizBox.addEventListener("canvas-school", function () {
      if (connectBox) {
        connectBox.hidden = host() !== guide.getAttribute("data-connect-host");
        if (!connectBox.hidden) { uploadSecond(); return; }
      }
      if (chromeBox) chromeBox.hidden = true;
      if (canOffer() && !isChromium) offerChrome(); else if (offerHelper && canOffer()) offerHelper(); else uploadFirst();
      showStart();
    });
    var helperGo = guide.querySelector("[data-helper-go]");
    if (helperBox && helperIds.length && !isPhone && isChromium) {
      var helperSaid = guide.querySelector("[data-helper-said]");
      var helperCourses = guide.querySelector("[data-helper-courses]");
      var settingsGo = guide.querySelector("[data-helper-settings]");
      var goLabel = helperGo.textContent;
      var busy = false;
      // Busy, the button still works (and looks it): a second press brings
      // Canvas's sign-in back, or says the list is still coming.
      var setBusy = function (on) {
        busy = on;
        helperGo.classList.toggle("is-busy", on);
        if (!on) helperGo.textContent = goLabel;
      };
      var wantsHelper = false; // pressed "Add Scheduler Helper": watch for it, and say what's next once it's there
      var resumeAfterAdd = false; // pressed "Update from Canvas" with no helper: once it's added, the update carries on
      var accessWait = false; // told how to let it read Canvas: back on this tab, it tries again by itself
      var helperSay = function (text) { helperSaid.textContent = text; }; // (always in the page, so it's read out)
      // The browser's own words in the install steps.
      var browserName = isEdge ? "Edge" : isOpera ? "Opera" : "Chrome";
      guide.querySelectorAll("[data-browser-name]").forEach(function (el) { el.textContent = browserName; });
      guide.querySelectorAll("[data-steps-for]").forEach(function (el) {
        el.hidden = el.getAttribute("data-steps-for") !== (isEdge ? "edge" : "chrome");
      });
      // What this helper can do besides reading Canvas ("front", "show-signin",
      // "remove", "settings"): the store's 1.0.0 can do none of them.
      var can = function (what) { return helperCan.indexOf(what) >= 0; };
      // This tab, marked in its title while it's behind another, when the
      // helper can't bring it forward (1.0.0): it shows where to click.
      var pageTitle = document.title;
      var markTab = function (mark) { if (document.hidden) document.title = mark + pageTitle; };
      document.addEventListener("visibilitychange", function () { if (!document.hidden) document.title = pageTitle; });
      // One message to the helper, and its answer (null if none comes in
      // `ms`: 4 seconds by default).
      var call = function (id, msg, finished, ms) {
        var rt = runtime();
        var over = false;
        var finish = function (answer) { if (!over) { over = true; finished(answer || null); } };
        if (!rt) { finish(null); return finish; }
        try {
          rt.sendMessage(id, msg, function (answer) {
            var quiet = chrome.runtime.lastError; // (read, so Chrome doesn't log it)
            finish(quiet ? null : answer);
          });
        } catch (err) { finish(null); }
        setTimeout(function () { finish(null); }, ms || 4000);
        return finish;
      };
      // Is the helper here? Said once, whichever id answers (or none).
      var findHelper = function (then) {
        var left = helperIds.length;
        var told = false;
        var tell = function (found) { if (!told) { told = true; then(found); } };
        helperIds.forEach(function (id) {
          call(id, { type: "hello" }, function (answer) {
            left -= 1;
            if (answer && answer.ok && !helperId) {
              helperId = id;
              helperHosts = answer.hosts || [];
              helperCan = answer.can || [];
              tell(true);
            } else if (!left) {
              tell(!!helperId);
            }
          });
        });
      };
      // The helper can't read this school's Canvas: one it doesn't know (the
      // file, then), or Chrome keeping it away (how to let it in).
      var cannotRead = function (why) {
        helperSay("");
        if (why === "access") {
          accessWait = true;
          helperSay(browserName + " is keeping Scheduler Helper away from Canvas. To let it in: "
            + (can("settings") ? "click Open Scheduler Helper's settings, below. There, "
              : "click the puzzle piece at the top right of " + browserName + ", then the three dots next to Scheduler "
                + "Helper, then Manage extension. There, ")
            + "under Site access, turn on " + host() + ". Then come back to this tab: it carries on by itself. Or "
            + "use Canvas's file of your students instead, below.");
          if (settingsGo && can("settings")) settingsGo.hidden = false;
          if (!helperBox.contains(document.activeElement)) helperGo.focus();
          return;
        }
        uploadFirst();
        fileSay("Scheduler Helper can't read your school's Canvas yet, so use Canvas's file of your students instead. "
          + "It brings the same names and emails. Start with Open Canvas, just below.");
        opener.focus({ preventScroll: true });
        if (fileSaid) fileSaid.scrollIntoView({ block: "center", behavior: "smooth" });
      };
      if (settingsGo) settingsGo.addEventListener("click", function () {
        call(helperId, { type: "settings" }, function () { /* (its page opens next to this one) */ });
      });
      var showReady = function () {
        if (!covered()) return; // (it can't read this school's Canvas)
        if (helperHosts.length && helperHosts.indexOf(host()) < 0) { cannotRead("host"); return; }
        var hadFocus = helperBox.contains(document.activeElement);
        helperBox.hidden = false;
        guide.querySelector("[data-helper-install]").hidden = true;
        ready.hidden = false;
        uploadSecond();
        if (hadFocus) helperGo.focus();
      };
      var decide = function () {
        if (helperId) { if (canOffer()) showReady(); return; }
        findHelper(function (found) {
          if (found) { if (canOffer()) showReady(); else uploadFirst(); return; }
          if (isOpera) { offerChrome(); return; } // (Opera's own store doesn't have it)
          if (storeUrl && canOffer()) {
            helperBox.hidden = false;
            guide.querySelector("[data-helper-install]").hidden = false;
            guide.querySelector("[data-helper-add]").href = storeUrl;
            uploadSecond();
          } else {
            uploadFirst(); // nothing to add in this browser (yet): the file it is
          }
        });
      };
      offerHelper = decide;
      if (canOffer()) decide();
      // Added from the store: the page notices (while the store is still in
      // front, too), asks the helper to bring this page back to the front
      // (1.0.0 can't: the tab's title says to click it), and points at the
      // button. It reads nothing until that's pressed, unless "Update from
      // Canvas" was what was pressed.
      var looking = false;
      var lookAgain = function () {
        if (helperId || looking || !canOffer()) return;
        looking = true;
        setTimeout(function () { looking = false; }, 6000); // (never stuck looking)
        findHelper(function (found) {
          looking = false;
          if (!found) return;
          showReady();
          if (!wantsHelper) return;
          wantsHelper = false;
          call(helperId, { type: "front" }, function (r) {
            if (!(r && r.ok)) markTab("Added. Click here · ");
          });
          if (resumeAfterAdd) {
            resumeAfterAdd = false;
            helperSay("Scheduler Helper is added. Getting your class list from Canvas…");
            helperGo.click();
            return;
          }
          helperSay("Scheduler Helper is added. Now press Get my class list from Canvas.");
          helperGo.scrollIntoView({ block: "center" });
          helperGo.focus({ preventScroll: true });
        });
      };
      var watchStore = null;
      var stopWatch = function () {
        if (watchStore && watchStore.terminate) watchStore.terminate(); else clearInterval(watchStore);
        watchStore = null;
      };
      var watchFor = function () { // (for half an hour: the helper added, or turned back on)
        wantsHelper = true;
        stopWatch();
        var until = Date.now() + 30 * 60 * 1000;
        var beat = function () {
          if (!wantsHelper || helperId || Date.now() > until) { stopWatch(); return; }
          lookAgain();
        };
        // A worker keeps its beat while the store is in front; this page's own
        // timers slow to once a minute after five minutes hidden.
        try { watchStore = new Worker("/static/tick.js"); watchStore.onmessage = beat; }
        catch (err) { watchStore = setInterval(beat, 1500); }
      };
      guide.querySelector("[data-helper-add]").addEventListener("click", function () {
        helperSay("When " + browserName + " says Scheduler Helper was added, this page comes back by itself. (If it "
          + "doesn't, click “" + pageName + "” at the very top of the window.)");
        watchFor();
      });
      var backHere = function () {
        lookAgain();
        if (accessWait && helperId && !busy) { accessWait = false; helperGo.click(); } // (let in, perhaps: try again)
      };
      window.addEventListener("focus", backHere);
      document.addEventListener("visibilitychange", function () { if (!document.hidden) backHere(); });

      // One request to the helper, over a port, and its answer. The helper
      // says when Canvas wants a sign-in, and keeps saying it's waiting.
      var signinUp = false; // Canvas's sign-in page is what's being waited on
      var nudge = null; // (a press while Canvas is still answering: say so)
      var again = null; // (a press while Canvas's sign-in waits, with a helper that can't bring it back: ask again)
      var cutOff = null; // a request the helper dropped (it restarted): asked again, once, when it's back
      var resending = false;
      var signinWords = function () {
        return can("show-signin")
          ? "Canvas wants you to sign in. Its sign-in page is open in another tab, in front of this one: sign in there "
            + "as usual, and you'll come straight back here."
          : "Canvas just came up in front of this page. Sign in there as usual, and you'll come straight back here. "
            + "(If it shows a Canvas page but no place to sign in, click the reload arrow ↻ at the top left of the "
            + "window, then sign in.)";
      };
      var ask = function (request, onAnswer) {
        var rt = runtime();
        var answered = false;
        var ports = [];
        var started = Date.now();
        var beat = null, slow = null, later = null, quiet = null;
        var offline = function () { answer({ type: "problem", why: "offline" }); };
        var done = function () {
          clearTimeout(slow); clearTimeout(later); clearTimeout(quiet); clearInterval(beat);
          window.removeEventListener("offline", offline);
          nudge = null;
          again = null;
        };
        var answer = function (msg) {
          if (answered) return;
          answered = true;
          signinUp = false;
          done();
          ports.forEach(function (p) { try { p.disconnect(); } catch (err) { /* gone */ } });
          cutOff = !msg && !resending ? { request: request, onAnswer: onAnswer } : null;
          onAnswer(msg || { type: "problem", why: "gone" });
        };
        // Canvas slow to answer (a big class, a busy day): a word that it's
        // still coming, unless its sign-in page is what's being waited on.
        var still = function () {
          if (!answered && !signinUp) helperSay("Still working: Canvas is slow to answer right now. Keep this page "
            + "open; your list will show up here by itself. You don't need to press anything.");
        };
        // Three minutes without a word from the helper: Canvas isn't
        // answering, so say so, and the button works again. While Canvas's
        // sign-in page is up, eleven (the helper waits ten for a sign-in;
        // 1.0.0 says nothing meanwhile, later ones say "wait").
        var hush = function () {
          clearTimeout(quiet);
          quiet = setTimeout(function () { answer({ type: "problem", why: "slow" }); }, (signinUp ? 11 : 3) * 60 * 1000);
        };
        var drop = function (port) {
          var i = ports.indexOf(port);
          if (i >= 0) ports.splice(i, 1);
          try { port.disconnect(); } catch (err) { /* gone */ }
        };
        var open = function () {
          var port = null;
          try { port = rt && rt.connect ? rt.connect(helperId) : null; } catch (err) { port = null; }
          if (!port) return false;
          ports.push(port);
          port.onMessage.addListener(function (msg) {
            if (answered || !msg) return;
            if (msg.type === "wait") { // still signing in (or, signedIn, done: the list is on its way)
              if (msg.signedIn && signinUp) {
                signinUp = false;
                helperGo.textContent = goLabel;
                helperSay("Signed in. Getting your class list from Canvas…");
              }
              hush();
              return;
            }
            if (msg.type === "signin") {
              if (navigator.onLine === false) { offline(); return; }
              if (signinUp) return; // (asked again: the press said so)
              signinUp = true;
              hush();
              helperGo.textContent = "Show me Canvas's sign-in page";
              helperSay(signinWords());
              return;
            }
            // A problem on one ask while another still waits: that one may yet bring the list.
            if (msg.type === "problem" && ports.length > 1) { drop(port); return; }
            answer(msg);
          });
          port.onDisconnect.addListener(function () {
            void chrome.runtime.lastError;
            drop(port);
            if (!ports.length) answer(null);
          });
          port.postMessage(request);
          return true;
        };
        if (!open()) { answer(null); return; } // (no helper here any more)
        window.addEventListener("offline", offline);
        beat = setInterval(function () {
          ports.forEach(function (p) { try { p.postMessage({ type: "ping" }); } catch (err) { /* gone */ } });
        }, 20000);
        slow = setTimeout(still, 8000);
        // 1.0.0, held up by a Canvas tab that's asking the professor something: where to look.
        later = setTimeout(function () {
          if (!answered && !signinUp && !can("show-signin")) helperSay("Still working. If you have Canvas open in "
            + "another tab, look at it: if it's asking you something (like “Leave site?”), answer it, and your list "
            + "then comes here by itself.");
        }, 30000);
        nudge = function () { if (Date.now() - started >= 8000) still(); }; // (not a double-click's second click)
        again = function () { return ports.length < 2 && open(); };
        hush();
      };
      var trouble = function (why) {
        setBusy(false);
        if (why === "gone") helperId = null; // (before tidy: the button it can't answer hides)
        tidy(false);
        helperCourses.hidden = true;
        if (why === "gone") {
          helperSaid.tabIndex = -1;
          helperSaid.focus({ preventScroll: true }); // (where the news is, as the button it was on goes)
          helperGone();
          return;
        }
        if (why === "host" || why === "access") { cannotRead(why); return; }
        if (why === "missing" || why === "forbidden") askWhich = true; // (the next press: the list of courses)
        if ((why === "signin" || why === "closed") && !can("front")) markTab("Canvas sign-in closed · ");
        helperSay(why === "forbidden" ? "Canvas won't share that course's class list with you. Is it a course you teach?"
          : why === "missing" ? "Canvas couldn't find that course. Press Get my class list from Canvas to choose it again."
          : why === "offline" ? "This computer can't reach Canvas right now. Check that the internet is working, then "
            + "press Get my class list from Canvas again."
          : why === "down" ? "Canvas isn't answering right now (it may be down for maintenance). Try again in a few "
            + "minutes, or use Canvas's file of your students instead, below."
          : why === "signin" || why === "closed" ? "Canvas sign-in didn't finish. Press Get my class list from Canvas to try again."
          : !can("show-signin") ? "Canvas didn't answer just now. If you have Canvas open in another tab and it's asking "
            + "you something (like “Leave site?”), answer it. Then press Get my class list from Canvas again."
          : "Canvas didn't answer just now. Wait a minute, then press Get my class list from Canvas again.");
        if (!helperBox.contains(document.activeElement)) helperGo.focus();
      };
      // The helper stopped answering (removed, turned off, or updating): is it
      // back after a moment? Then what it was asked, once more. If not, how
      // to get it back, or Canvas's file instead.
      var helperGone = function () {
        helperSay("Checking on Scheduler Helper…");
        var tries = 0;
        var check = function () {
          findHelper(function (found) {
            if (found) {
              showReady();
              if (cutOff) {
                var redo = cutOff;
                cutOff = null;
                resending = true;
                setBusy(true);
                helperSay("Scheduler Helper restarted for a moment; carrying on…");
                ask(redo.request, redo.onAnswer);
                return;
              }
              helperSay("Scheduler Helper stopped for a moment. Press Get my class list from Canvas again.");
              helperGo.focus({ preventScroll: true });
              return;
            }
            if (++tries < 3) { setTimeout(check, 1000); return; } // (an update takes a moment)
            cutOff = null;
            watchFor(); // turned back on or added again: it says so, and points at the button
            if (storeUrl && canOffer()) {
              helperBox.hidden = false;
              guide.querySelector("[data-helper-install]").hidden = false;
              guide.querySelector("[data-helper-add]").href = storeUrl;
              uploadSecond();
              helperSay("Scheduler Helper is no longer in " + browserName + " (it was removed or turned off), so it "
                + "can't bring your class list. Add it again with the button above (three clicks), or use Canvas's "
                + "file of your students instead.");
              guide.querySelector("[data-helper-add]").focus({ preventScroll: true });
              helperSaid.scrollIntoView({ block: "nearest", behavior: "smooth" });
            } else {
              uploadFirst();
              fileSay("Scheduler Helper is no longer in " + browserName + ", so use Canvas's file of your students "
                + "instead. It brings the same names and emails. Start with Open Canvas, just below.");
              opener.focus({ preventScroll: true });
            }
          });
        };
        check();
      };
      var current = function (c) {
        var from = Date.parse(c.start || ""), to = Date.parse(c.end || ""), today = Date.now();
        return (isNaN(from) || from <= today) && (isNaN(to) || to >= today);
      };
      var pickNote = ""; // said above the courses, the once
      var autoPicked = ""; // the course picked from the sheet's name (if Canvas then won't give its list: ask)
      // The course this sheet is for, when its name says: the same name, or
      // the one current course with the same course number ("LAW 540" and
      // "2026FA_LAW_540_SEC20"), unless the letters just before that number
      // name another subject ("LAW 615" isn't "BUSCOM_615"). In the sheet's
      // name, letters are a subject when written as one (LAW, BUSCOM) or found
      // as one in the professor's courses; otherwise ("Evidence 540") they're
      // just a word. Years aren't course numbers.
      var numbers = function (text, known) {
        var out = [];
        String(text).replace(/([A-Za-z]*)[\s_-]*(\d{3,})/g, function (all, letters, n) {
          if (/^(19|20)\d\d$/.test(n)) return all;
          var subject = letters.toLowerCase();
          if (known && !/^[A-Z]{2,8}$/.test(letters) && known.indexOf(subject) < 0) subject = "";
          out.push({ subject: subject, n: n });
          return all;
        });
        return out;
      };
      var sheetTitle = function () { return (guide.getAttribute("data-sheet-title") || "").trim().toLowerCase(); };
      var titleNumbers = function (courses) {
        var known = [];
        courses.forEach(function (c) { numbers(c.name).forEach(function (x) { if (x.subject) known.push(x.subject); }); });
        return numbers((guide.getAttribute("data-sheet-title") || "").trim(), known);
      };
      var sameNumber = function (c, ours) {
        return numbers(c.name).some(function (x) {
          return ours.some(function (y) {
            return y.n === x.n && (!y.subject || !x.subject || y.subject.indexOf(x.subject) === 0 || x.subject.indexOf(y.subject) === 0);
          });
        });
      };
      // The sheet's name is for another course ("LAW 615 Privacy"; this one, BUSCOM_615 or LAW_700).
      var otherCourse = function (c, courses) {
        var ours = titleNumbers(courses);
        return ours.length > 0 && String(c.name).trim().toLowerCase() !== sheetTitle() && !sameNumber(c, ours);
      };
      var matching = function (courses) {
        var title = sheetTitle();
        var same = courses.filter(function (c) { return String(c.name).trim().toLowerCase() === title; });
        if (same.length === 1) return same[0];
        var ours = titleNumbers(courses);
        if (!ours.length) return null;
        var hits = courses.filter(function (c) { return current(c) && sameNumber(c, ours); });
        return hits.length === 1 ? hits[0] : null;
      };
      // Courses on the professor's other sheets, marked in the list.
      var used = {};
      try { used = JSON.parse(guide.getAttribute("data-used-courses") || "{}") || {}; } catch (err) { used = {}; }
      var getCourses = function () {
        setBusy(true);
        helperSay("Getting your courses from Canvas…");
        ask({ type: "courses", host: host() }, function (msg) {
          setBusy(false);
          var note = pickNote;
          pickNote = "";
          var choose = askWhich; // "Wrong course?", a start over, or this sheet's course gone from Canvas
          askWhich = false;
          if (msg.type !== "courses") { trouble(msg.why); return; }
          var courses = (msg.courses || []).slice().sort(function (a, b) { return (current(b) ? 1 : 0) - (current(a) ? 1 : 0); });
          if (!courses.length) {
            helperSay((note ? note + " " : "") + "Canvas didn't list any course you teach. In Canvas: if a bar at the "
              + "bottom says Student View, click Leave Student View; if Canvas is signed in as someone else, click "
              + "Account, then Logout, and sign in as yourself. Then press Get my class list from Canvas again. Still "
              + "nothing? Your school's Canvas help can add you to your course. Until then, you can type or paste your "
              + "students instead:");
            // (the way to do that: the class-list question's own step, or, on the class page, its Add students box)
            helperCourses.textContent = "";
            var typeIn = document.createElement(quizBox ? "button" : "a");
            typeIn.className = "btn btn-secondary";
            typeIn.textContent = "Type or paste your students";
            if (quizBox) { typeIn.type = "button"; typeIn.setAttribute("data-go", "paste"); } else typeIn.href = "#add-students";
            helperCourses.appendChild(typeIn);
            helperCourses.hidden = false;
            return;
          }
          if (courses.length === 1 && !choose && !otherCourse(courses[0], courses)) { getStudents(courses[0].id, courses[0].name); return; }
          var match = choose ? null : matching(courses);
          if (match) { autoPicked = String(match.id); getStudents(match.id, match.name); return; } // (and "Wrong course?" under the list)
          helperSay("");
          helperCourses.textContent = "";
          if (note) {
            var said = document.createElement("p");
            said.className = "mini-hint";
            said.textContent = note;
            helperCourses.appendChild(said);
          }
          var title = guide.getAttribute("data-sheet-title");
          var question = document.createElement("p");
          question.id = "helper-courses-q-" + guide.getAttribute("data-sheet");
          question.className = "helper-courses-q";
          question.tabIndex = -1;
          question.textContent = courses.length === 1 ? "Canvas lists just one course you teach. Is “" + title + "” for it?"
            : "Which Canvas course is “" + title + "” for?";
          helperCourses.appendChild(question);
          courses.forEach(function (c) {
            var b = document.createElement("button");
            b.type = "button";
            b.className = "quiz-choice";
            b.textContent = c.name + (c.term ? " · " + c.term : "")
              + (typeof c.students === "number" ? " · " + c.students + (c.students === 1 ? " student" : " students") : "")
              + (c.published === false ? " (not published yet)" : "")
              + (used[String(c.id)] ? " · already on your sheet “" + used[String(c.id)] + "”" : "")
              + (String(c.id) === guide.getAttribute("data-canvas-course-id") ? " · this sheet's course now" : "");
            b.addEventListener("click", function () {
              helperCourses.hidden = true;
              ready.hidden = false;
              helperGo.focus({ preventScroll: true }); // (where the list's news is said, while it comes)
              getStudents(c.id, c.name);
            });
            helperCourses.appendChild(b);
          });
          if (courses.length === 1) {
            var none = document.createElement("p");
            none.className = "mini-hint";
            none.textContent = "If not, Canvas doesn't list you as a teacher of the right course yet. Your school's "
              + "Canvas help can add you. Until then, you can use Canvas's file, or add your students by email.";
            helperCourses.appendChild(none);
          } else {
            var notHere = document.createElement("p");
            notHere.className = "mini-hint";
            notHere.textContent = "Not listed? Only courses where you're a teacher or TA show.";
            helperCourses.appendChild(notHere);
          }
          ready.hidden = true; // the choice is the one thing to do now
          helperCourses.hidden = false;
          question.focus();
        });
      };
      var getStudents = function (courseId, label) {
        setBusy(true);
        helperSay("Getting the class list" + (label ? " for " + label : "") + "…");
        ask({ type: "students", host: host(), courseId: courseId }, function (msg) {
          setBusy(false);
          var picked = autoPicked;
          autoPicked = "";
          if (msg.type !== "students") {
            var gone = msg.why === "missing" || msg.why === "forbidden";
            // The course this sheet's list came from, gone from Canvas (or no
            // longer theirs): which course it is now, from their list.
            if (gone && savedCourse && String(courseId) === savedCourse) {
              forgotCourse = savedCourse;
              savedCourse = "";
              showStart();
              pickNote = "Canvas no longer has the course this sheet's list came from (it may have been deleted or "
                + "merged, or you're no longer on it).";
              askWhich = true;
              getCourses();
              return;
            }
            // The course picked from the sheet's name, and Canvas won't give its list: ask which.
            if (gone && picked && String(courseId) === picked) {
              pickNote = "Canvas wouldn't give the class list for " + (label || "that course") + ".";
              askWhich = true;
              getCourses();
              return;
            }
            trouble(msg.why);
            return;
          }
          var c = msg.course || {};
          var name = (c.name || label || "your Canvas course") + (c.term ? " (" + c.term + ")" : "");
          if (!msg.students || !msg.students.length) {
            tidy(false);
            helperSay(name + " has no students in Canvas yet. If it isn't published, or students just enrolled, Canvas may "
              + "not list them for a day or two.");
            return;
          }
          helperSay("");
          showArrived({ course: name, courseId: String(c.id || courseId), students: msg.students }, canvasBase(), "helper");
        });
      };
      helperGo.addEventListener("click", function (e) {
        var second = e && e.detail > 1; // (a double-click's second click: nothing more to do)
        if (busy && signinUp) { // Canvas's sign-in waiting, behind: in front again
          if (second) return;
          var elsewhere = "Canvas's sign-in page is open in another tab, at the top of this window. Click it, sign in "
            + "as usual, and you'll come straight back here.";
          if (can("show-signin")) {
            call(helperId, { type: "show-signin", host: host() }, function (r) {
              if (r && r.ok) {
                helperSay("Canvas's sign-in page is in front again. Sign in there as usual, and you'll come straight back here.");
              } else if (r) { // (no sign-in waiting any more: signed in, and the list is on its way)
                signinUp = false;
                helperGo.textContent = goLabel;
                helperSay("You're signed in to Canvas. Your class list is on its way, and shows up here by itself.");
              } else {
                helperSay(elsewhere);
              }
            });
          } else if (again && again()) { // (1.0.0: asked again, it brings a Canvas tab in front, or reads Canvas if signed in)
            helperSay("Canvas is in front again. Sign in there as usual, and you'll come straight back here. (If it shows "
              + "a Canvas page but no place to sign in, click the reload arrow ↻ at the top left of the window.)");
          } else {
            helperSay(elsewhere);
          }
          return;
        }
        if (busy) { if (nudge && !second) nudge(); return; }
        resending = false;
        accessWait = false;
        if (settingsGo) settingsGo.hidden = true;
        if (helperHosts.length && helperHosts.indexOf(host()) < 0) { trouble("host"); return; }
        helperCourses.hidden = true;
        arrivedBox.hidden = true;
        if (savedCourse && !askWhich) { getStudents(savedCourse, ""); return; } // this sheet's course, from last time
        if (askWhich) savedCourse = "";
        getCourses();
      });
      // Back from "start over" or "Wrong course?" on the review page: the
      // button, ready, says what's next (nothing is read until it's pressed).
      if (askWhich) {
        var pointAt = function () {
          if (!helperId || ready.hidden) return;
          showHere(helperGo);
          if (guide.hasAttribute("data-ask-now")) { helperGo.click(); return; } // (asked to pick another course: the courses)
          helperSay("Press Get my class list from Canvas, then pick the right course.");
          helperGo.scrollIntoView({ block: "center" });
          helperGo.focus({ preventScroll: true });
        };
        var tries = 0;
        var waitForHelper = setInterval(function () {
          if (helperId || ++tries > 20) { clearInterval(waitForHelper); pointAt(); }
        }, 250);
      }
    }
    // "Update from Canvas", up top on the class page: the guide, open; and
    // with the helper ready, its one press.
    document.querySelectorAll("[data-canvas-update-go]").forEach(function (link) {
      link.addEventListener("click", function (e) {
        e.preventDefault();
        showHere(guide);
        // (the box's own title, "Class changed in Canvas?", shows above the guide)
        (guide.closest("details") || guide).scrollIntoView({ block: "start", behavior: "smooth" });
        var add = guide.querySelector("[data-helper-add]");
        if (helperGo && helperId && ready && !ready.hidden) helperGo.click();
        else if (helperGo && !helperGo.closest("[hidden]")) helperGo.focus({ preventScroll: true });
        else if (add && !add.closest("[hidden]")) {
          add.focus({ preventScroll: true });
          resumeAfterAdd = true; // (once it's added, the update carries on)
        }
      });
    });

    // Opened from a link to this step (at=canvas: copied in another browser
    // to open here in Chrome): the step, in view. Nothing is read until the
    // button is pressed.
    if (/[?&]at=canvas(&|$)/.test(location.search) && guide.closest("details")) {
      showHere(guide);
      guide.closest("details").scrollIntoView({ block: "start" });
    }

    // Back from Canvas (this page hidden, then looked at again) after
    // opening it for the file: a bar pinned to the bottom of the window,
    // with one press to choose Canvas's file. The page doesn't move: the
    // professor may only have come back to read the next step.
    var welcome = guide.querySelector("[data-canvas-welcome]");
    var bar = guide.querySelector("[data-came-back]");
    var barSaid = guide.querySelector("[data-came-back-said]");
    var went = false;
    var wasHidden = false;
    var boxInView = false;
    if (dropBox && "IntersectionObserver" in window) {
      new IntersectionObserver(function (entries) {
        boxInView = entries[0].isIntersecting;
        if (bar && boxInView) bar.hidden = true;
      }).observe(dropBox);
    }
    guide.querySelectorAll("[data-canvas-went]").forEach(function (link) {
      link.addEventListener("click", function () { went = true; wasHidden = false; });
    });
    var cameBack = function () {
      if (document.visibilityState === "hidden") { wasHidden = true; return; }
      if (!went || !wasHidden) return;
      wasHidden = false; // (went stays: every return gets the bar, until a file is chosen)
      if (steps) steps.querySelectorAll("[data-canvas-step]").forEach(function (li) {
        li.classList.toggle("is-next", li.getAttribute("data-canvas-step") === "drop");
      });
      if (!bar || !fileInput || boxInView) {
        if (welcome) welcome.textContent = "Welcome back. Downloaded Canvas's file? Step 4 is just below.";
        return;
      }
      bar.hidden = false;
      if (barSaid) barSaid.textContent = "Back from Canvas. Has it saved its file?";
    };
    if (bar && fileInput) {
      bar.querySelector("[data-came-back-choose]").addEventListener("click", function () { fileInput.click(); });
      bar.querySelector("[data-came-back-close]").addEventListener("click", function () { bar.hidden = true; });
    }
    document.addEventListener("visibilitychange", cameBack);
    window.addEventListener("focus", cameBack);
  })();

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
