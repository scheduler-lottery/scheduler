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
        btn.textContent = "Copied ✓";
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

  // The days list on the create/edit form: add, remove, reorder.
  document.querySelectorAll("[data-day-rows]").forEach(function (list) {
    var template = document.querySelector("[data-day-template]");
    var addBtn = document.querySelector("[data-add-day]");

    function renumber() {
      Array.from(list.children).forEach(function (li, i) {
        var input = li.querySelector('input[name="day_label"]');
        if (input) input.setAttribute("aria-label", "Day " + (i + 1));
      });
    }

    function addRow(after) {
      var li = template.content.firstElementChild.cloneNode(true);
      if (after && after.nextSibling) list.insertBefore(li, after.nextSibling);
      else list.appendChild(li);
      renumber();
      li.querySelector('input[name="day_label"]').focus();
      return li;
    }

    if (addBtn && template) addBtn.addEventListener("click", function () { addRow(); });

    list.addEventListener("click", function (e) {
      var li = e.target.closest(".day-row");
      if (!li) return;
      if (e.target.closest("[data-row-remove]")) {
        if (list.children.length > 1) li.remove();
        else li.querySelector('input[name="day_label"]').value = "";
      } else if (e.target.closest("[data-row-up]") && li.previousElementSibling) {
        list.insertBefore(li, li.previousElementSibling);
      } else if (e.target.closest("[data-row-down]") && li.nextElementSibling) {
        list.insertBefore(li.nextElementSibling, li);
      }
      renumber();
    });

    // Enter moves to the next day (adding one at the end) instead of
    // submitting the whole form half-filled.
    list.addEventListener("keydown", function (e) {
      if (e.key !== "Enter" || !e.target.matches('input[name="day_label"]')) return;
      e.preventDefault();
      var li = e.target.closest(".day-row");
      var next = li.nextElementSibling;
      if (next) next.querySelector('input[name="day_label"]').focus();
      else if (template) addRow(li);
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

  // The Canvas guide's "Open Canvas" button points at the school's own Canvas.
  var hostInput = document.getElementById("canvas-host");
  var openLink = document.getElementById("canvas-open");
  if (hostInput && openLink) {
    var cleanHost = function (raw) {
      var host = (raw || "").trim().toLowerCase().replace(/^[a-z]+:\/\//, "").split("/")[0];
      return /^[a-z0-9.-]+\.[a-z]{2,}$/.test(host) ? host : "";
    };
    var apply = function () {
      var host = cleanHost(hostInput.value);
      openLink.href = "https://" + (host || "canvas.instructure.com") + "/courses";
      return host;
    };
    hostInput.value = store("canvas-host") || "";
    apply();
    hostInput.addEventListener("input", function () {
      var host = apply();
      store("canvas-host", host || null);
    });
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
