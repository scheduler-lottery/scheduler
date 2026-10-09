// Loaded in <head>, before the page paints, so there's no flash of the wrong
// theme or text size. (A separate file rather than an inline script, so the
// site's Content-Security-Policy can forbid inline scripts entirely.)
// The choices are made in the Display settings menu (theme.js).
(function () {
  var root = document.documentElement;
  var THEMES = {
    light: "light", paper: "light", "solarized-light": "light", "contrast-light": "light",
    dark: "dark", black: "dark", "solarized-dark": "dark", "contrast-dark": "dark",
  };
  var FONTS = { mixed: 1, sans: 1, serif: 1, readable: 1 };
  var theme = "light", font = "mixed", scale = 100;
  try {
    theme = localStorage.getItem("theme") || "light";
    font = localStorage.getItem("font") || "mixed";
    scale = parseInt(localStorage.getItem("text-scale") || "100", 10);
  } catch (e) {
    /* storage blocked (private browsing): the standard look */
  }
  if (!THEMES[theme]) theme = "light";
  if (!FONTS[font]) font = "mixed";
  if (!(scale >= 50 && scale <= 200)) scale = 100;
  root.setAttribute("data-theme", theme);
  root.setAttribute("data-mode", THEMES[theme]);
  root.setAttribute("data-font", font);
  root.style.setProperty("--text-scale", String(scale / 100));
})();
