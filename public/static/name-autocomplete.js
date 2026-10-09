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

  function closeList() {
    list.hidden = true;
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
  });
})();
