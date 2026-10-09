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

  function applyTheme(theme) {
    if (!THEMES[theme]) theme = "paper";
    root.setAttribute("data-theme", theme);
    root.setAttribute("data-mode", THEMES[theme]);
    check("appearance-theme", theme);
  }

  function applyFont(font) {
    root.setAttribute("data-font", font);
    check("appearance-font", font);
  }

  function applyScale(percent) {
    percent = Math.max(50, Math.min(200, Math.round(percent / 5) * 5));
    root.style.setProperty("--text-scale", String(percent / 100));
    range.value = percent;
    range.style.setProperty("--fill", ((percent - 50) / 150) * 100 + "%");
    readout.textContent = percent + "%";
    Array.prototype.forEach.call(dialog.querySelectorAll("[data-size-preset]"), function (b) {
      b.setAttribute("aria-pressed", String(+b.getAttribute("data-size-preset") === percent));
    });
    return percent;
  }

  // On an instructor's own sheet: save the class's look (quietly, a moment
  // after the last change).
  var saveTimer = null;
  function saveForClass() {
    if (!courseUrl) return;
    clearTimeout(saveTimer);
    status.textContent = "Saving for your class…";
    saveTimer = setTimeout(function () {
      fetch(courseUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
        body: JSON.stringify({ theme: root.getAttribute("data-theme"), font: root.getAttribute("data-font") }),
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
      applyTheme(e.target.value);
      remember("theme", e.target.value);
      saveForClass();
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
      // Back to the class's look on a class page, or the standard one.
      remember("theme", null);
      remember("font", null);
      remember("text-scale", null);
      applyTheme(root.getAttribute("data-course-theme") || "paper");
      applyFont(root.getAttribute("data-course-font") || "mixed");
      applyScale(100);
      saveForClass();
    }
  });

  // Show what's in effect now (theme-init.js already applied it).
  applyTheme(root.getAttribute("data-theme"));
  applyFont(root.getAttribute("data-font") || "mixed");
  applyScale(Math.round(parseFloat(root.style.getPropertyValue("--text-scale") || "1") * 100));
})();
