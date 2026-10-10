// Loaded in <head>, before the page paints, so there's no flash of the wrong
// theme or text size. (A separate file rather than an inline script, so the
// site's Content-Security-Policy can forbid inline scripts entirely.)
// The choices are made in the Appearance window (theme.js).
(function () {
  var root = document.documentElement;
  var THEMES = {
    light: "light", paper: "light", "solarized-light": "light", "contrast-light": "light",
    dark: "dark", black: "dark", "solarized-dark": "dark", "contrast-dark": "dark",
  };
  var FONTS = { mixed: 1, oldstyle: 1, sans: 1, readable: 1 };
  // A class's pages carry the look its instructor chose; a reader's own
  // choice, if they've made one, wins (text size is always their own).
  var theme = root.getAttribute("data-course-theme") || "paper";
  var font = root.getAttribute("data-course-font") || "mixed";
  var scale = 100;
  try {
    theme = localStorage.getItem("theme") || theme;
    font = localStorage.getItem("font") || font;
    scale = parseInt(localStorage.getItem("text-scale") || "100", 10);
  } catch (e) {
    /* storage blocked (private browsing): the class's or standard look */
  }
  if (!THEMES[theme]) theme = "paper";
  if (!FONTS[font]) font = "mixed";
  if (!(scale >= 50 && scale <= 200)) scale = 100;
  root.setAttribute("data-theme", theme);
  root.setAttribute("data-mode", THEMES[theme]);
  root.setAttribute("data-font", font);
  root.style.setProperty("--text-scale", String(scale / 100));
  // Hide the page until its fonts are in (app.js shows it), so nothing
  // flashes from a stand-in font to the real one.
  if (document.fonts) root.classList.add("fonts-loading");
  // Scripts run here, so what needs them can count on them (messages float
  // over the page only when they can be closed).
  root.classList.add("js");
})();
