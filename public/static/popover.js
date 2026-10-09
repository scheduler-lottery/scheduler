(function () {
  function closeDropdowns() {
    document.querySelectorAll(".popover-dropdown.open").forEach(function (el) {
      el.classList.remove("open");
      var trigger = document.querySelector('[data-popover-target="' + el.id + '"]');
      if (trigger) trigger.setAttribute("aria-expanded", "false");
    });
  }

  function closeModals() {
    document.querySelectorAll(".modal-overlay.open").forEach(function (el) {
      el.classList.remove("open");
    });
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
      var wasOpen = el.classList.contains("open");
      closeDropdowns();
      closeModals();
      if (!wasOpen) {
        el.classList.add("open");
        trigger.setAttribute("aria-expanded", "true");
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
    if (e.target.classList && e.target.classList.contains("modal-overlay")) {
      e.target.classList.remove("open");
    }
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      closeDropdowns();
      closeModals();
      closeTips();
    }
  });
})();
