// Loaded in <head>, before the page paints, so there's no flash of the wrong
// theme. (A separate file rather than an inline script, so the site's
// Content-Security-Policy can forbid inline scripts entirely.)
(function () {
  try {
    var t = localStorage.getItem("theme");
    document.documentElement.setAttribute("data-theme", t === "dark" ? "dark" : "light");
  } catch (e) {
    document.documentElement.setAttribute("data-theme", "light");
  }
})();
