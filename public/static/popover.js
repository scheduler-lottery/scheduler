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
      if (!wasOpen) {
        el.classList.add("open");
        trigger.setAttribute("aria-expanded", "true");
        opener = trigger;
        var focusable = el.querySelector("button, a, [tabindex]");
        if (focusable) focusable.focus({ preventScroll: true });
      }
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

  // While a window is open, the page behind it stays put: it can't scroll.
  // The scrollbar's width is kept as padding, so nothing shifts sideways.
  // Watching the windows' classes catches every way one opens.
  var root = document.documentElement;
  function freeze() {
    var open = !!document.querySelector(".modal-overlay.open");
    if (open === root.classList.contains("page-frozen")) return;
    if (open) root.style.setProperty("--scrollbar-gap", window.innerWidth - root.clientWidth + "px");
    root.classList.toggle("page-frozen", open);
  }
  if ("MutationObserver" in window) {
    var watch = new MutationObserver(freeze);
    document.querySelectorAll(".modal-overlay").forEach(function (el) {
      watch.observe(el, { attributes: true, attributeFilter: ["class"] });
    });
  }
})();
