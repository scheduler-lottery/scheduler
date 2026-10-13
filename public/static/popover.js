(function () {
  var opener = null; // what opened the window that's open, to return focus to

  function close(el) {
    el.classList.remove("open");
    document.querySelectorAll('[data-popover-target="' + el.id + '"]').forEach(function (trigger) {
      if (trigger.hasAttribute("aria-expanded")) trigger.setAttribute("aria-expanded", "false");
    });
  }

  function closeDropdowns() {
    document.querySelectorAll(".popover-dropdown.open").forEach(close);
  }

  function closeModals() {
    var open = document.querySelectorAll(".modal-overlay.open");
    open.forEach(close);
    // Back to the button that opened it, unless that was inside a window
    // that's now closed too (Appearance, opened from Settings).
    if (open.length && opener && opener.offsetParent !== null) opener.focus({ preventScroll: true });
  }

  // Open a window, its first button (the close ×) taking the focus.
  function show(el, trigger) {
    el.classList.add("open");
    if (trigger) trigger.setAttribute("aria-expanded", "true");
    opener = trigger;
    var focusable = el.querySelector("button, a, [tabindex]");
    if (focusable) focusable.focus({ preventScroll: true });
  }

  function closeTips() {
    document.querySelectorAll(".tip.tip-open").forEach(function (el) {
      el.classList.remove("tip-open");
    });
  }

  // Tooltips show on hover/focus via CSS; on touch there's no hover, so a tap
  // on the "?" toggles .tip-open instead.
  document.addEventListener("click", function (e) {
    var tipBtn = e.target.closest(".tip-btn");
    var wasOpen = tipBtn && tipBtn.parentElement.classList.contains("tip-open");
    closeTips();
    if (tipBtn && !wasOpen) {
      tipBtn.parentElement.classList.add("tip-open");
      tipBtn.setAttribute("aria-expanded", "true");
      e.stopPropagation();
      return;
    }
    if (tipBtn) tipBtn.setAttribute("aria-expanded", "false");
  });

  document.addEventListener("click", function (e) {
    var trigger = e.target.closest("[data-popover-target]");
    if (trigger) {
      var id = trigger.getAttribute("data-popover-target");
      var el = document.getElementById(id);
      if (!el) return;
      // A link that opens a window (with a page as its no-script fallback)
      // opens the window instead.
      if (trigger.tagName === "A") e.preventDefault();
      var wasOpen = el.classList.contains("open");
      opener = null;
      closeDropdowns();
      closeModals();
      if (!wasOpen) show(el, trigger);
      e.stopPropagation();
      return;
    }

    if (e.target.closest("[data-popover-close]")) {
      closeDropdowns();
      closeModals();
      return;
    }

    if (!e.target.closest(".popover-dropdown")) closeDropdowns();
    if (e.target.classList && e.target.classList.contains("modal-overlay")) closeModals();
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      closeDropdowns();
      closeModals();
      closeTips();
    }
  });

  // Back closes a window. Each window that opens adds a step to the
  // browser's history (this same page, marked with the window's id), so
  // Android's Back button or gesture and the iPhone's swipe from the left
  // edge close the window instead of leaving the page. Closing it any other
  // way (×, Done, a tap outside it, Escape) takes that step back out, so
  // the page's own Back and Forward stay as they were. Anything else the
  // page keeps in its history entry is carried along.
  var root = document.documentElement;
  var stepHere = false; // the window's step was added by this page (not left from before a reload)
  function marked() {
    return history.state && typeof history.state === "object" ? history.state.modal || null : null;
  }
  function stateWith(id) {
    var state = {};
    var now = history.state;
    if (now && typeof now === "object") Object.keys(now).forEach(function (key) { state[key] = now[key]; });
    if (id) state.modal = id;
    else delete state.modal;
    return state;
  }
  function syncHistory() {
    var open = document.querySelector(".modal-overlay.open[id]");
    var id = open ? open.id : null;
    try {
      if (id && marked() !== id) {
        // One window opened from another (Appearance, from Settings) takes
        // over its step rather than adding a second.
        if (marked()) {
          history.replaceState(stateWith(id), "");
        } else {
          history.pushState(stateWith(id), "");
          stepHere = true;
        }
      } else if (!id && marked()) {
        // A step left from before a reload can't be stepped back out of
        // without loading the page again: it's just unmarked.
        if (stepHere) history.back();
        else history.replaceState(stateWith(null), "");
        stepHere = false;
      }
    } catch (err) {
      /* no history to use here (a sandboxed frame): Back leaves the page, as before */
    }
  }
  window.addEventListener("popstate", function (e) {
    var want = e.state && typeof e.state === "object" ? e.state.modal : null;
    var open = document.querySelector(".modal-overlay.open");
    if (open && open.id !== want) closeModals();
    // Forward again, to the step a window was open at: open it again.
    var el = want && document.getElementById(want);
    if (el && el.classList.contains("modal-overlay") && !el.classList.contains("open")) {
      stepHere = true;
      show(el, null);
    }
  });

  // While a window is open, the page behind it stays put: it can't scroll.
  // The scrollbar's width is kept as padding, so nothing shifts sideways.
  // Watching the windows' classes catches every way one opens.
  function freeze() {
    var open = !!document.querySelector(".modal-overlay.open");
    if (open === root.classList.contains("page-frozen")) return;
    if (open) root.style.setProperty("--scrollbar-gap", window.innerWidth - root.clientWidth + "px");
    root.classList.toggle("page-frozen", open);
  }
  if ("MutationObserver" in window) {
    var watch = new MutationObserver(function () {
      freeze();
      syncHistory();
    });
    document.querySelectorAll(".modal-overlay").forEach(function (el) {
      watch.observe(el, { attributes: true, attributeFilter: ["class"] });
    });
  }

  // Back here from another page (a link followed from inside a window) at
  // the step where the window was open: it's open again, as it was left. A
  // reload starts the page afresh, with no window open.
  (function () {
    var id = marked();
    if (!id) return;
    var nav = window.performance && performance.getEntriesByType && performance.getEntriesByType("navigation")[0];
    var el = document.getElementById(id);
    if (nav && nav.type === "back_forward" && el && el.classList.contains("modal-overlay")) show(el, null);
    else history.replaceState(stateWith(null), "");
  })();

  // On a phone each window is a sheet that comes up from the bottom of the
  // screen (style.css). A swipe down on its top (the grab handle, title and
  // close button) closes it, as sheets do elsewhere on phones: the sheet
  // follows the finger, and goes if let go far enough down (or flicked).
  // Only while its content is scrolled to the top, so a swipe meant to
  // scroll back up isn't taken for one. Listening also makes iOS show the
  // :active (pressed) look of buttons when they're tapped.
  var phone = window.matchMedia ? window.matchMedia("(max-width: 640px)") : null;
  var drag = null;
  document.addEventListener("touchstart", function (e) {
    drag = null;
    var sheet = e.target.closest && e.target.closest(".modal-overlay.open > .modal-dialog");
    if (!sheet || !phone || !phone.matches || e.touches.length !== 1 || sheet.scrollTop > 0) return;
    var title = sheet.querySelector(".modal-close + .modal-title");
    var top = sheet.getBoundingClientRect().top;
    var head = Math.max(top + 72, title ? title.getBoundingClientRect().bottom : 0);
    if (e.touches[0].clientY > head) return;
    drag = { sheet: sheet, y: e.touches[0].clientY, at: Date.now(), dy: 0, moved: false };
  }, { passive: true });
  document.addEventListener("touchmove", function (e) {
    if (!drag || e.touches.length !== 1) return;
    var dy = e.touches[0].clientY - drag.y;
    if (!drag.moved && dy < 0) { drag = null; return; } // upward: a scroll
    if (!drag.moved && dy < 6) return;
    drag.moved = true;
    drag.dy = Math.max(0, dy);
    drag.sheet.classList.add("is-dragged");
    drag.sheet.style.setProperty("--sheet-drag", drag.dy + "px");
  }, { passive: true });
  function letGo() {
    var d = drag;
    drag = null;
    if (!d || !d.moved) return;
    var sheet = d.sheet;
    var flicked = d.dy > 40 && d.dy / Math.max(1, Date.now() - d.at) > 0.6;
    sheet.classList.remove("is-dragged");
    if (flicked || d.dy > Math.min(140, sheet.offsetHeight / 3)) {
      sheet.classList.add("is-leaving"); // slides the rest of the way down
      setTimeout(function () {
        closeModals();
        sheet.classList.remove("is-leaving");
        sheet.style.removeProperty("--sheet-drag");
      }, 180);
    } else {
      sheet.style.removeProperty("--sheet-drag"); // springs back up
    }
  }
  document.addEventListener("touchend", letGo);
  document.addEventListener("touchcancel", letGo);
})();
