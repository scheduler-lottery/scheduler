(function () {
  var root = document.documentElement;
  var btn = document.getElementById("theme-toggle");
  if (!btn) return;

  function apply(theme) {
    root.setAttribute("data-theme", theme);
    btn.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
    btn.setAttribute(
      "aria-label",
      theme === "dark" ? "Switch to light theme" : "Switch to dark theme"
    );
  }

  // The inline snippet in <head> already set data-theme before paint;
  // this just wires the button up to match and to persist changes.
  apply(root.getAttribute("data-theme") === "dark" ? "dark" : "light");

  btn.addEventListener("click", function () {
    var current = root.getAttribute("data-theme") === "dark" ? "dark" : "light";
    var next = current === "dark" ? "light" : "dark";
    try {
      localStorage.setItem("theme", next);
    } catch (err) {
      /* no-op: localStorage may be unavailable (private mode, etc.) */
    }
    apply(next);
  });
})();