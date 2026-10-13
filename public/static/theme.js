// The Appearance window: theme, text size (50%-200%), and font. Choices
// apply at once and are remembered on this device; theme-init.js puts them
// back before the next page paints. On an instructor's own sheet, the theme
// and font are also saved as the class's look, which every student in that
// class then sees (unless they've picked their own).
(function () {
  var root = document.documentElement;
  var dialog = document.getElementById("appearance");
  if (!dialog) return;

  var THEMES = {
    light: "light", paper: "light", "solarized-light": "light", "contrast-light": "light",
    dark: "dark", black: "dark", "solarized-dark": "dark", "contrast-dark": "dark",
  };
  // Each light theme's dark twin (as in theme-init.js), for "Automatic".
  var TWIN = { paper: "dark", light: "dark", "solarized-light": "solarized-dark", "contrast-light": "contrast-dark" };
  var range = dialog.querySelector("[data-size-range]");
  var readout = dialog.querySelector("[data-size-readout]");
  var status = dialog.querySelector("[data-appearance-status]");
  var courseUrl = dialog.getAttribute("data-course-look");
  var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";

  function remember(key, value) {
    try {
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch (err) {
      /* storage blocked: the choice lasts for this page only */
    }
  }

  function check(name, value) {
    var input = dialog.querySelector('input[name="' + name + '"][value="' + value + '"]');
    if (input) input.checked = true;
  }

  // The class's look (or the standard one): what "Automatic" follows.
  function baseTheme() {
    var base = root.getAttribute("data-course-theme");
    return THEMES[base] ? base : "paper";
  }

  // "auto" follows the device: the base theme, or its dark twin while the
  // device is set to dark (theme-init.js keeps following it after this).
  function applyTheme(theme) {
    var auto = theme === "auto";
    if (auto) {
      var dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
      theme = dark ? TWIN[baseTheme()] || baseTheme() : baseTheme();
      root.setAttribute("data-theme-auto", "");
    } else {
      root.removeAttribute("data-theme-auto");
    }
    if (!THEMES[theme]) theme = "paper";
    root.setAttribute("data-theme", theme);
    root.setAttribute("data-mode", THEMES[theme]);
    check("appearance-theme", auto ? "auto" : theme);
    // The browser's own bars, in the new page color.
    var bg = getComputedStyle(root).getPropertyValue("--bg").trim();
    if (bg) {
      Array.prototype.forEach.call(document.querySelectorAll('meta[name="theme-color"]'), function (meta) {
        meta.setAttribute("content", bg);
      });
    }
  }

  function applyFont(font) {
    root.setAttribute("data-font", font);
    check("appearance-font", font);
  }

  function applyScale(percent) {
    percent = Math.max(50, Math.min(200, Math.round(percent / 5) * 5));
    root.style.setProperty("--text-scale", String(percent / 100));
    root.classList.toggle("text-large", percent >= 150);
    range.value = percent;
    range.style.setProperty("--fill", ((percent - 50) / 150) * 100 + "%");
    readout.textContent = percent + "%";
    Array.prototype.forEach.call(dialog.querySelectorAll("[data-size-preset]"), function (b) {
      b.setAttribute("aria-pressed", String(+b.getAttribute("data-size-preset") === percent));
    });
    return percent;
  }

  // On an instructor's own sheet: save the class's look (quietly, a moment
  // after the last change). Following the device saves the class's own
  // theme, not the dark twin showing on this device.
  var saveTimer = null;
  function saveForClass() {
    if (!courseUrl) return;
    clearTimeout(saveTimer);
    status.textContent = "Saving for your class…";
    saveTimer = setTimeout(function () {
      var theme = root.hasAttribute("data-theme-auto") ? baseTheme() : root.getAttribute("data-theme");
      fetch(courseUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
        body: JSON.stringify({ theme: theme, font: root.getAttribute("data-font") }),
        credentials: "same-origin",
      }).then(function (r) {
        status.textContent = r.ok ? "Saved: your class sees this look." : "Couldn't save it for your class. Try again.";
      }).catch(function () {
        status.textContent = "Couldn't save it for your class. Check your connection.";
      });
    }, 350);
  }

  dialog.addEventListener("change", function (e) {
    if (e.target.name === "appearance-theme") {
      var auto = e.target.value === "auto";
      applyTheme(e.target.value);
      remember("theme", auto ? null : e.target.value);
      saveForClass(); // (Automatic too: the class is back on the standard look, which follows each device)
    } else if (e.target.name === "appearance-font") {
      applyFont(e.target.value);
      remember("font", e.target.value);
      saveForClass();
    }
  });

  range.addEventListener("input", function () {
    var percent = applyScale(parseInt(range.value, 10));
    remember("text-scale", percent === 100 ? null : String(percent));
  });

  dialog.addEventListener("click", function (e) {
    var step = e.target.closest("[data-size-step]");
    var preset = e.target.closest("[data-size-preset]");
    if (step || preset) {
      var percent = applyScale(preset ? +preset.getAttribute("data-size-preset")
                                      : parseInt(range.value, 10) + +step.getAttribute("data-size-step"));
      remember("text-scale", percent === 100 ? null : String(percent));
    }
    if (e.target.closest("[data-appearance-reset]")) {
      // Back to the class's look on a class page, or the standard one,
      // light or dark to match the device.
      remember("theme", null);
      remember("font", null);
      remember("text-scale", null);
      applyTheme("auto");
      applyFont(root.getAttribute("data-course-font") || "mixed");
      applyScale(100);
      saveForClass();
    }
  });

  // The samples of every font, once the window is first opened (the page
  // itself asks only for the fonts its own choice uses: theme-init.js).
  document.addEventListener("click", function (e) {
    if (!e.target.closest || !e.target.closest('[data-popover-target="appearance"]')) return;
    var css = document.querySelector("link[data-font-css]");
    if (!css || document.querySelector("link[data-font-css-all]")) return;
    var all = document.createElement("link");
    all.rel = "stylesheet";
    all.href = css.getAttribute("data-all-fonts");
    all.setAttribute("data-font-css-all", "");
    document.head.appendChild(all);
  }, true);

  // On a phone, Text size comes first: it's what most people open this
  // window for, and on a small screen the themes would push it out of
  // sight. (Moved in the page itself, so screen readers and the Tab key
  // meet the parts in the order they're shown.)
  var themes = dialog.querySelector("[data-theme-group]");
  var sizes = dialog.querySelector("[data-size-group]");
  var phone = window.matchMedia && window.matchMedia("(max-width: 640px)");
  function arrange() {
    if (!themes || !sizes) return;
    if (phone && phone.matches) themes.parentNode.insertBefore(sizes, themes);
    else if (themes.nextElementSibling !== sizes) themes.parentNode.insertBefore(sizes, themes.nextElementSibling);
  }
  arrange();
  if (phone && phone.addEventListener) phone.addEventListener("change", arrange);
  else if (phone && phone.addListener) phone.addListener(arrange);

  // Show what's in effect now (theme-init.js already applied it).
  applyTheme(root.hasAttribute("data-theme-auto") ? "auto" : root.getAttribute("data-theme"));
  applyFont(root.getAttribute("data-font") || "mixed");
  applyScale(Math.round(parseFloat(root.style.getPropertyValue("--text-scale") || "1") * 100));
})();
