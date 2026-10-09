// The Display settings menu: theme, text size (50%-200%), and font. Each
// choice applies at once and is remembered on this device; theme-init.js
// puts it back before the next page paints.
(function () {
  var root = document.documentElement;
  var panel = document.getElementById("display-settings");
  if (!panel) return;

  var THEMES = {
    light: "light", paper: "light", "solarized-light": "light", "contrast-light": "light",
    dark: "dark", black: "dark", "solarized-dark": "dark", "contrast-dark": "dark",
  };
  var range = panel.querySelector("[data-scale-range]");
  var shown = panel.querySelector("[data-scale-value]");

  function remember(key, value, standard) {
    try {
      if (value === standard) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch (err) {
      /* storage blocked: the choice lasts for this page only */
    }
  }

  function mark(selector, attribute, value) {
    Array.prototype.forEach.call(panel.querySelectorAll(selector), function (el) {
      el.setAttribute("aria-checked", el.getAttribute(attribute) === value ? "true" : "false");
    });
  }

  function setTheme(theme) {
    if (!THEMES[theme]) theme = "light";
    root.setAttribute("data-theme", theme);
    root.setAttribute("data-mode", THEMES[theme]);
    remember("theme", theme, "light");
    mark("[data-theme-choice]", "data-theme-choice", theme);
  }

  function setScale(percent) {
    percent = Math.max(50, Math.min(200, Math.round(percent / 5) * 5));
    root.style.setProperty("--text-scale", String(percent / 100));
    remember("text-scale", String(percent), "100");
    range.value = percent;
    shown.textContent = percent + "%";
  }

  function setFont(font) {
    root.setAttribute("data-font", font);
    remember("font", font, "mixed");
    mark("[data-font-choice]", "data-font-choice", font);
  }

  panel.addEventListener("click", function (e) {
    var theme = e.target.closest("[data-theme-choice]");
    var font = e.target.closest("[data-font-choice]");
    var step = e.target.closest("[data-scale-step]");
    if (theme) setTheme(theme.getAttribute("data-theme-choice"));
    if (font) setFont(font.getAttribute("data-font-choice"));
    if (step) setScale(parseInt(range.value, 10) + parseInt(step.getAttribute("data-scale-step"), 10));
    if (e.target.closest("[data-settings-reset]")) {
      setTheme("light");
      setFont("mixed");
      setScale(100);
    }
  });
  range.addEventListener("input", function () { setScale(parseInt(range.value, 10)); });

  // Show what's in effect now (theme-init.js already applied it).
  setTheme(root.getAttribute("data-theme"));
  setFont(root.getAttribute("data-font") || "mixed");
  setScale(Math.round(parseFloat(root.style.getPropertyValue("--text-scale") || "1") * 100));
})();
