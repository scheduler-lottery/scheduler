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
  // Each light theme's dark twin, for a device set to dark (or to switch at
  // night) when the reader hasn't picked a theme here.
  var TWIN = { paper: "dark", light: "dark", "solarized-light": "solarized-dark", "contrast-light": "contrast-dark" };
  // Each theme's page color, for the browser's own bars around the page.
  var BG = {
    paper: "#faf8f3", light: "#ffffff", "solarized-light": "#eee8d5", "contrast-light": "#ffffff",
    dark: "#0d0f1e", black: "#000000", "solarized-dark": "#002b36", "contrast-dark": "#000000",
  };
  var FONTS = { mixed: 1, oldstyle: 1, sans: 1, readable: 1 };
  // A class's pages carry the look its instructor chose; a reader's own
  // choice, if they've made one, wins (text size is always their own).
  var base = root.getAttribute("data-course-theme");
  if (!THEMES[base]) base = "paper";
  var theme = null;
  var font = root.getAttribute("data-course-font") || "mixed";
  var scale = 100;
  try {
    theme = localStorage.getItem("theme");
    font = localStorage.getItem("font") || font;
    scale = parseInt(localStorage.getItem("text-scale") || "100", 10);
  } catch (e) {
    /* storage blocked (private browsing): the class's or standard look */
  }
  if (!FONTS[font]) font = "mixed";
  if (!(scale >= 50 && scale <= 200)) scale = 100;

  var dark = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  function paint(name) {
    root.setAttribute("data-theme", name);
    root.setAttribute("data-mode", THEMES[name]);
    var tints = document.querySelectorAll('meta[name="theme-color"]');
    for (var i = 0; i < tints.length; i++) tints[i].setAttribute("content", BG[name]);
  }
  // No theme of their own: the class's (or the standard) look, in its dark
  // twin while the device is dark. data-theme-auto marks that; theme.js
  // clears it when the reader picks a theme.
  function follow() {
    if (root.hasAttribute("data-theme-auto")) paint(dark && dark.matches ? TWIN[base] || base : base);
  }
  if (THEMES[theme]) {
    paint(theme);
  } else {
    root.setAttribute("data-theme-auto", "");
    follow();
  }
  // The device switching between light and dark (at sunset, say) switches
  // the page too, while it's open.
  if (dark && dark.addEventListener) dark.addEventListener("change", follow);
  else if (dark && dark.addListener) dark.addListener(follow);

  root.setAttribute("data-font", font);
  root.style.setProperty("--text-scale", String(scale / 100));

  // The free stand-in fonts this font choice can use (the site's own fonts
  // come from its stylesheet), from Google Fonts. Asked for from here rather
  // than by a <link> in the page, so the page doesn't wait on another site
  // before it shows. The Appearance window asks for all of them (theme.js),
  // for its samples.
  var FAMILY = {
    inter: "Inter:wght@400;500;600;700",
    serif: "Instrument+Serif:ital@0;1",
    fraunces: "Fraunces:ital,opsz,wght@0,9..144,200..700;1,9..144,200..700",
    crimson: "Crimson+Pro:ital,wght@0,400;0,700;1,400;1,700",
    atkinson: "Atkinson+Hyperlegible:ital,wght@0,400;0,700;1,400",
  };
  var fontsUrl = function (names) {
    return "https://fonts.googleapis.com/css2?" + names.split(" ").map(function (name) {
      return "family=" + FAMILY[name];
    }).join("&") + "&display=swap";
  };
  var link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = fontsUrl({ sans: "inter", readable: "inter atkinson" }[font] || "inter fraunces serif crimson");
  link.setAttribute("data-font-css", "");
  link.setAttribute("data-all-fonts", fontsUrl("inter serif fraunces crimson atkinson"));
  var arrived = function () { link.setAttribute("data-loaded", ""); };
  link.addEventListener("load", arrived);
  link.addEventListener("error", arrived);
  document.head.appendChild(link);

  // Hide the page for a moment while its fonts come in (app.js shows it
  // after half a second at most), so nothing flashes from a stand-in font
  // to the real one. Not on a slow connection or with data saver on: there
  // the page shows at once, in a stand-in font until the real one is in.
  var net = navigator.connection || {};
  var slow = net.saveData || /(^|-)2g$|^3g$/.test(net.effectiveType || "");
  if (document.fonts && !slow) root.classList.add("fonts-loading");
  // Scripts run here, so what needs them can count on them (messages float
  // over the page only when they can be closed).
  root.classList.add("js");
})();
