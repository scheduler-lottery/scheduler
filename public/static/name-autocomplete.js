(function () {
  const input = document.getElementById("name-input");
  const list = document.getElementById("name-suggestions");
  if (!input || !list) return;
  const suggestUrl = input.getAttribute("data-suggest-url");
  if (!suggestUrl) return;

  let items = [];
  let activeIndex = -1;
  let debounceTimer = null;
  let controller = null;

  // Phones and tablets: the on-screen keyboard covers the bottom of the
  // window, which is where the names would open. So once the student taps
  // the box (again when the keyboard comes up, and when names first show),
  // the page moves the box up near the top of what's still visible, if it
  // can scroll that far. If two names still wouldn't fit under the box,
  // the list opens above it instead, only as tall as the room there.
  const touch = !!(window.matchMedia && window.matchMedia("(pointer: coarse)").matches);
  const view = window.visualViewport;
  const ROW = 45; // about one name's row
  let tapped = false; // the box was tapped (not just focused as the page opened)
  let lifted = false; // moved up once since then: not again while typing
  let settle = null;

  // What the keyboard leaves visible, in the window's own terms; and where
  // that starts below a top bar that stays put while the page scrolls.
  function visibleBand() {
    const top = view ? view.offsetTop : 0;
    const band = { top, bottom: top + (view ? view.height : window.innerHeight) };
    const bar = document.querySelector(".topbar");
    const pos = bar ? getComputedStyle(bar).position : "";
    if (pos === "sticky" || pos === "fixed") band.top = Math.max(band.top, bar.getBoundingClientRect().bottom);
    return band;
  }

  function lift() {
    if (!touch || lifted || document.activeElement !== input) return;
    const band = visibleBand();
    const box = input.getBoundingClientRect();
    // Room under the box already for three names (or as many as came)? Leave the page be.
    const wanted = Math.min(3, Math.max(items.length, 1)) * ROW + 12;
    if (band.bottom - box.bottom >= wanted && box.top >= band.top) return;
    lifted = true;
    window.scrollBy(0, box.top - band.top - 8);
  }

  function place() {
    if (!touch) return;
    if (list.hidden) {
      list.classList.remove("opens-up");
      return;
    }
    const band = visibleBand();
    const box = input.getBoundingClientRect();
    const below = band.bottom - box.bottom - 12;
    const above = box.top - band.top - 12;
    const up = below < 2 * ROW && above > below;
    list.classList.toggle("opens-up", up);
    list.style.setProperty("--suggest-room", Math.max(Math.floor(up ? above : below), 2 * ROW) + "px");
  }

  // After the keyboard (or the page) has finished moving.
  function settleSoon(andLift) {
    clearTimeout(settle);
    settle = setTimeout(() => {
      if (andLift && tapped) lift();
      place();
    }, 120);
  }

  function closeList() {
    list.hidden = true;
    list.classList.remove("opens-up");
    list.innerHTML = "";
    items = [];
    activeIndex = -1;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
  }

  function highlight(idx) {
    Array.from(list.children).forEach((li, i) => {
      li.classList.toggle("active", i === idx);
    });
    activeIndex = idx;
    if (idx >= 0) {
      input.setAttribute("aria-activedescendant", "suggestion-" + idx);
      list.children[idx].scrollIntoView({ block: "nearest" });
    } else {
      input.removeAttribute("aria-activedescendant");
    }
  }

  function selectName(name) {
    input.value = name;
    closeList();
  }

  function renderList(names) {
    items = names;
    activeIndex = -1;
    list.innerHTML = "";
    if (!names.length) {
      closeList();
      return;
    }
    names.forEach((name, idx) => {
      const li = document.createElement("li");
      li.className = "suggestion-item";
      li.textContent = name;
      li.id = "suggestion-" + idx;
      li.setAttribute("role", "option");
      // mousedown (not click) fires before the input's blur handler closes the list
      li.addEventListener("mousedown", (e) => {
        e.preventDefault();
        selectName(name);
      });
      list.appendChild(li);
    });
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    if (touch) {
      tapped = true;
      lift();
      place();
    }
  }

  input.addEventListener("input", () => {
    const q = input.value.trim();
    clearTimeout(debounceTimer);
    // The server only answers from the third letter on.
    if (q.replace(/\s/g, "").length < 3) {
      closeList();
      return;
    }
    debounceTimer = setTimeout(async () => {
      if (controller) controller.abort();
      controller = new AbortController();
      try {
        const res = await fetch(suggestUrl + "?q=" + encodeURIComponent(q), {
          signal: controller.signal,
        });
        if (!res.ok) throw new Error("bad response");
        const names = await res.json();
        // Don't show a suggestion that's identical to what's already typed
        const filtered = names.filter((n) => n.toLowerCase() !== q.toLowerCase());
        renderList(filtered);
      } catch (err) {
        if (err.name !== "AbortError") closeList();
      }
    }, 150);
  });

  input.addEventListener("keydown", (e) => {
    if (list.hidden) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      highlight(Math.min(activeIndex + 1, items.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      highlight(Math.max(activeIndex - 1, 0));
    } else if (e.key === "Enter") {
      if (activeIndex >= 0) {
        e.preventDefault();
        selectName(items[activeIndex]);
      }
    } else if (e.key === "Escape") {
      closeList();
    }
  });

  input.addEventListener("blur", () => {
    // small delay so a mousedown-triggered selection can still register
    setTimeout(closeList, 100);
    tapped = false;
    lifted = false;
  });

  if (touch) {
    input.addEventListener("pointerdown", () => {
      tapped = true;
    });
    input.addEventListener("focus", () => {
      if (tapped) settleSoon(true);
    });
    // The keyboard coming up (or going), the page scrolled with it up.
    if (view) {
      view.addEventListener("resize", () => {
        if (document.activeElement === input) settleSoon(true);
      });
      view.addEventListener("scroll", () => settleSoon(false));
    }
    window.addEventListener("scroll", () => {
      if (!list.hidden) settleSoon(false);
    }, { passive: true });
  }
})();
