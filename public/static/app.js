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

  // Flash messages: click to dismiss; good news fades on its own, but
  // problems stay until you've seen them.
  document.querySelectorAll(".flash-stack .flash").forEach(function (el) {
    var dismiss = function () {
      el.classList.add("leaving");
      setTimeout(function () { el.remove(); }, 400);
    };
    el.title = "Click to dismiss";
    el.addEventListener("click", dismiss);
    if (!el.classList.contains("flash-error")) setTimeout(dismiss, 7000);
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

  // Timestamps are stored in UTC; show them in the reader's own time zone.
  document.querySelectorAll("time[data-local]").forEach(function (el) {
    var when = new Date(el.getAttribute("datetime"));
    if (isNaN(when)) return;
    var kind = el.getAttribute("data-local");
    if (kind === "date") {
      el.textContent = when.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
    } else if (kind === "time") {
      el.textContent = when.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
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

  // The Canvas guide: from the course's address, a link straight to its
  // student list (New Analytics), remembered on this computer per sheet.
  var guide = document.querySelector("[data-canvas-guide]");
  var openLink = document.getElementById("canvas-open");
  if (guide && openLink) {
    var courseInput = document.getElementById("canvas-course");
    var status = guide.querySelector("[data-canvas-status]");
    var where = document.querySelector("[data-canvas-where]");
    var courseKey = "canvas-course-" + guide.getAttribute("data-sheet");
    // The New Analytics tool's number at schools we know of. Pasting a New
    // Analytics address teaches this computer the number for that school.
    var KNOWN_TOOLS = { "canvas.northwestern.edu": "48685" };
    var learned = {};
    try { learned = JSON.parse(store("canvas-tools") || "{}") || {}; } catch (err) { learned = {}; }

    var readCourse = function (raw) {
      var m = (raw || "").trim().match(/^(?:https?:\/\/)?([a-z0-9.-]+\.[a-z]{2,})\/courses\/(\d+)(?:\/external_tools\/(\d+))?/i);
      return m ? { host: m[1].toLowerCase(), course: m[2], tool: m[3] } : null;
    };
    var applyCourse = function (save) {
      var found = readCourse(courseInput.value);
      if (!found) {
        var host = store("canvas-host") || "canvas.instructure.com";
        openLink.href = "https://" + host + "/courses";
        openLink.textContent = "Open Canvas";
        where.textContent = "Open your course there, copy its address into the box above, and this button takes you straight to its student list.";
        status.textContent = courseInput.value.trim()
          ? "That doesn't look like a course address. It should have /courses/ and a number in it."
          : "We'll remember it on this computer for this sheet.";
        return;
      }
      if (found.tool) {
        learned[found.host] = found.tool;
        store("canvas-tools", JSON.stringify(learned));
      }
      var tool = found.tool || learned[found.host] || KNOWN_TOOLS[found.host];
      openLink.href = "https://" + found.host + "/courses/" + found.course
        + (tool ? "/external_tools/" + tool + "?launch_type=course_navigation" : "");
      openLink.textContent = "Open my class's student list in Canvas";
      where.textContent = tool
        ? "It opens New Analytics for your course."
        : "It opens your course. Click New Analytics (or Course Analytics) in the course menu on the left.";
      status.textContent = "Got it: course " + found.course + " at " + found.host + ".";
      if (save) {
        store(courseKey, courseInput.value.trim());
        store("canvas-host", found.host);
      }
    };
    courseInput.value = store(courseKey) || "";
    applyCourse(false);
    courseInput.addEventListener("input", function () { applyCourse(true); });
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
