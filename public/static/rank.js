(function () {
  const initDataEl = document.getElementById("init-data");
  if (!initDataEl) return;
  const initData = JSON.parse(initDataEl.textContent);

  const DAY_LABELS = {};
  initData.days.forEach((d) => { DAY_LABELS[d.key] = d.label; });

  // Start from the saved ranking, then tack on any days that aren't ranked yet.
  let order = initData.ranking.slice();
  initData.days.forEach((d) => {
    if (!order.includes(d.key)) order.push(d.key);
  });

  const excluded = new Set(initData.excluded);
  const comments = Object.assign({}, initData.comments);
  const biddingOpen = !!initData.biddingOpen;
  const COMMENT_LIMIT = initData.commentLimit || 1000;
  const newDays = new Set(initData.newDays || []);
  // The version of the ranking this page started from. The server refuses
  // a save made from an older version (say, a tab left open on another
  // device), rather than quietly overwriting a newer ranking.
  let baseVersion = initData.savedAt || "";
  let hasSubmitted = !!initData.hasSubmitted;
  let stopped = false; // a save failed in a way retrying can't fix

  const listEl = document.getElementById("rank-list");
  const saveBtn = document.getElementById("save-btn");
  const saveStatus = document.getElementById("save-status");
  const warningEl = document.getElementById("rank-warning");
  let dirty = false;

  const SVG_NS = "http://www.w3.org/2000/svg";

  function setStatus(text, cls) {
    if (!saveStatus) return;
    saveStatus.textContent = text;
    saveStatus.className = "save-status" + (cls ? " " + cls : "");
  }

  function markDirty() {
    dirty = true;
    updateWarning();
    if (stopped) return;
    setStatus("Saving in a moment\u2026", "dirty");
    scheduleSave();
  }

  // Marking every day (or all but one) as can't-do still ends with a day,
  // so say so while there's time to add a note.
  function updateWarning() {
    if (!warningEl) return;
    const total = initData.days.length;
    if (excluded.size >= Math.max(1, total - 1)) {
      warningEl.hidden = false;
      warningEl.textContent = excluded.size >= total
        ? "You've marked every day as one you can't do. You'll still be given one of them (your top choice), so please add a note for your instructor explaining why."
        : "You've marked all but one day as can't-do, so you'll get that one (or be squeezed onto it if it's full). If it doesn't work either, add a note for your instructor.";
    } else {
      warningEl.hidden = true;
    }
  }

  // A "?" that reveals an explanation on hover, keyboard focus, or tap.
  // popover.js handles the tap case; CSS handles the rest.
  function makeTip(text, alignRight) {
    const wrap = document.createElement("span");
    wrap.className = "tip" + (alignRight ? " tip-right" : "");

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "tip-btn";
    btn.textContent = "?";
    btn.setAttribute("aria-label", text);
    btn.setAttribute("aria-expanded", "false");

    const bubble = document.createElement("span");
    bubble.className = "tip-bubble";
    bubble.setAttribute("role", "tooltip");
    bubble.textContent = text;

    wrap.appendChild(btn);
    wrap.appendChild(bubble);
    return wrap;
  }

  function makeArrow(up) {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", up ? "M5 15l7-7 7 7" : "M5 9l7 7 7-7");
    path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-width", "2.2");
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("stroke-linejoin", "round");
    svg.appendChild(path);
    return svg;
  }

  function positionLabel(idx, total) {
    if (idx === 0) return "Top choice";
    if (idx === total - 1) return "Last choice";
    return "";
  }

  // Keeps the #n badges, the Top/Last captions, and the disabled state of the
  // end-of-list arrows in sync after any reorder.
  function refreshPositions() {
    const children = Array.from(listEl.children);
    children.forEach((child, idx) => {
      const badge = child.querySelector(".rank-badge");
      if (badge) badge.textContent = "#" + (idx + 1);
      const caption = child.querySelector(".rank-pos-label");
      if (caption) caption.textContent = positionLabel(idx, children.length);
      const up = child.querySelector(".move-up");
      const down = child.querySelector(".move-down");
      if (up) up.disabled = idx === 0;
      if (down) down.disabled = idx === children.length - 1;
      child.setAttribute("aria-label",
        (DAY_LABELS[child.dataset.day] || child.dataset.day) + ", choice " + (idx + 1) +
        " of " + children.length);
    });
  }

  function syncOrderFromDom() {
    order = Array.from(listEl.children).map((x) => x.dataset.day);
  }

  const REDUCED_MOTION =
    window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const FLIP_MS = 180;

  // FLIP: measure every row, let `mutate` reshuffle the DOM, then play each
  // row from where it *was* to where it now is. Without this, rows teleport.
  // Returns the before/after tops so the caller can correct a dragged row's
  // own baseline.
  function animateReorder(mutate) {
    const before = new Map();
    Array.from(listEl.children).forEach((el) => {
      before.set(el, el.getBoundingClientRect().top);
    });

    mutate();

    const after = new Map();
    Array.from(listEl.children).forEach((el) => {
      after.set(el, el.getBoundingClientRect().top);
    });

    if (!REDUCED_MOTION) {
      Array.from(listEl.children).forEach((el) => {
        // The row under the cursor is positioned by the drag handler itself.
        if (el.classList.contains("dragging")) return;
        const start = before.has(el) ? before.get(el) : after.get(el);
        const delta = start - after.get(el);
        if (!delta) return;
        el.style.transition = "none";
        el.style.transform = "translateY(" + delta + "px)";
        void el.offsetHeight; // flush, so the browser sees the start position
        el.style.transition = "transform " + FLIP_MS + "ms cubic-bezier(.2,.8,.3,1)";
        el.style.transform = "";
        window.setTimeout(() => {
          el.style.transition = "";
        }, FLIP_MS + 40);
      });
    }
    return { before, after };
  }

  // Nudge the list, not the page, and only when the row has actually drifted
  // off screen — jumping the viewport on every button press is disorienting.
  function keepInView(el) {
    const rect = el.getBoundingClientRect();
    const margin = 90;
    if (rect.top < margin || rect.bottom > window.innerHeight - margin) {
      el.scrollIntoView({
        block: "nearest",
        behavior: REDUCED_MOTION ? "auto" : "smooth",
      });
    }
  }

  function flash(li) {
    li.classList.remove("just-moved");
    void li.offsetHeight;
    li.classList.add("just-moved");
    window.setTimeout(() => li.classList.remove("just-moved"), 700);
  }

  function moveItem(li, delta) {
    const siblings = Array.from(listEl.children);
    const idx = siblings.indexOf(li);
    const target = idx + delta;
    if (target < 0 || target >= siblings.length) return;

    animateReorder(() => {
      if (delta < 0) listEl.insertBefore(li, siblings[target]);
      else listEl.insertBefore(li, siblings[target].nextSibling);
    });

    refreshPositions();
    syncOrderFromDom();
    markDirty();
    flash(li);
    keepInView(li);

    // Keep focus on the button just pressed so repeated presses work — but
    // preventScroll, because the default focus behaviour is what was yanking
    // the page back to the top of the list.
    const btn = li.querySelector(delta < 0 ? ".move-up" : ".move-down");
    const fallback = li.querySelector(delta < 0 ? ".move-down" : ".move-up");
    const toFocus = btn && !btn.disabled ? btn : fallback;
    if (toFocus) toFocus.focus({ preventScroll: true });
  }

  function render() {
    listEl.innerHTML = "";
    order.forEach((dayKey) => {
      const li = document.createElement("li");
      li.className = "rank-item" + (excluded.has(dayKey) ? " excluded" : "");
      li.dataset.day = dayKey;

      const top = document.createElement("div");
      top.className = "rank-item-top";

      if (biddingOpen) {
        const handle = document.createElement("span");
        handle.className = "drag-handle";
        handle.textContent = "\u283F"; // braille-pattern dots, used as a grip icon
        handle.setAttribute("aria-hidden", "true");
        handle.title = "Drag to reorder";
        top.appendChild(handle);
        attachDragLater.push([li, handle]);
      }

      const pos = document.createElement("div");
      pos.className = "rank-pos";
      const badge = document.createElement("span");
      badge.className = "rank-badge";
      badge.textContent = "#";
      const caption = document.createElement("span");
      caption.className = "rank-pos-label";
      pos.appendChild(badge);
      pos.appendChild(caption);
      top.appendChild(pos);

      const label = document.createElement("span");
      label.className = "day-label";
      label.textContent = DAY_LABELS[dayKey] || dayKey;
      if (newDays.has(dayKey)) {
        const tag = document.createElement("span");
        tag.className = "tag tag-warn";
        tag.textContent = "new";
        label.appendChild(document.createTextNode(" "));
        label.appendChild(tag);
      }
      top.appendChild(label);

      if (biddingOpen) {
        const moves = document.createElement("div");
        moves.className = "rank-move";
        const upBtn = document.createElement("button");
        upBtn.type = "button";
        upBtn.className = "move-up";
        upBtn.setAttribute("aria-label", "Move " + (DAY_LABELS[dayKey] || dayKey) + " up");
        upBtn.title = "Move up";
        upBtn.appendChild(makeArrow(true));
        upBtn.addEventListener("click", () => moveItem(li, -1));

        const downBtn = document.createElement("button");
        downBtn.type = "button";
        downBtn.className = "move-down";
        downBtn.setAttribute("aria-label", "Move " + (DAY_LABELS[dayKey] || dayKey) + " down");
        downBtn.title = "Move down";
        downBtn.appendChild(makeArrow(false));
        downBtn.addEventListener("click", () => moveItem(li, 1));

        moves.appendChild(upBtn);
        moves.appendChild(downBtn);
        top.appendChild(moves);
      }

      li.appendChild(top);

      const commentWrap = document.createElement("div");
      commentWrap.className = "comment-wrap";
      const commentBox = document.createElement("textarea");
      commentBox.className = "comment-box";
      commentBox.placeholder = excluded.has(dayKey)
        ? "Why can't you do this day? (only your instructor sees this)"
        : "Why this day is hard for you — e.g. \"away for a family event\" or \"back-to-back exams\".";
      commentBox.value = comments[dayKey] || "";
      commentBox.maxLength = COMMENT_LIMIT;
      commentBox.disabled = !biddingOpen;
      commentBox.setAttribute("aria-label", "Note about " + (DAY_LABELS[dayKey] || dayKey));

      const commentHint = document.createElement("span");
      commentHint.className = "comment-hint";
      const countHint = function () {
        const used = commentBox.value.length;
        commentHint.textContent = "Only your instructor sees this. Optional." +
          (used > COMMENT_LIMIT * 0.8 ? " " + used + " / " + COMMENT_LIMIT + " characters." : "");
      };
      countHint();
      commentBox.addEventListener("input", countHint);
      commentWrap.appendChild(commentBox);
      commentWrap.appendChild(commentHint);

      if (biddingOpen) {
        const actions = document.createElement("div");
        actions.className = "rank-item-actions";

        // "Can't do this day" — a checkbox styled as a pill so it reads as a
        // real toggle rather than fine print.
        const excludeLabel = document.createElement("label");
        excludeLabel.className = "mini-toggle" + (excluded.has(dayKey) ? " on" : "");
        const excludeCb = document.createElement("input");
        excludeCb.type = "checkbox";
        excludeCb.checked = excluded.has(dayKey);
        excludeCb.addEventListener("change", () => {
          if (excludeCb.checked) excluded.add(dayKey);
          else excluded.delete(dayKey);
          li.classList.toggle("excluded", excludeCb.checked);
          excludeLabel.classList.toggle("on", excludeCb.checked);
          if (excludeCb.checked) {
            commentWrap.hidden = false;
            commentBox.placeholder = "Why can't you do this day? (only your instructor sees this)";
          }
          markDirty();
        });
        excludeLabel.appendChild(excludeCb);
        excludeLabel.appendChild(document.createTextNode("I really can't do this day"));
        actions.appendChild(excludeLabel);
        actions.appendChild(makeTip(
          "For a real conflict, not just a preference. You won't be put on this day unless you mark " +
          "every day. If the days you can do fill up, you're squeezed onto one of them and your instructor " +
          "sees a warning."
        ));

        // Notes start collapsed unless there's already something in them.
        const noteBtn = document.createElement("button");
        noteBtn.type = "button";
        noteBtn.className = "mini-toggle note-toggle";
        const hasNote = !!(comments[dayKey] || "").trim();
        commentWrap.hidden = !hasNote && !excluded.has(dayKey);
        noteBtn.textContent = hasNote ? "✏️ Edit note" : "＋ Add a note";
        if (hasNote) noteBtn.classList.add("has-note");
        noteBtn.setAttribute("aria-expanded", String(!commentWrap.hidden));
        noteBtn.addEventListener("click", () => {
          commentWrap.hidden = !commentWrap.hidden;
          noteBtn.setAttribute("aria-expanded", String(!commentWrap.hidden));
          if (!commentWrap.hidden) commentBox.focus();
        });
        actions.appendChild(noteBtn);
        actions.appendChild(makeTip(
          "Only your instructor sees notes. A note doesn't change how days are given out — it " +
          "tells your instructor why, in case they need to adjust things by hand.",
          true
        ));

        commentBox.addEventListener("input", () => {
          comments[dayKey] = commentBox.value;
          const filled = !!commentBox.value.trim();
          noteBtn.textContent = filled ? "✏️ Edit note" : "＋ Add a note";
          noteBtn.classList.toggle("has-note", filled);
          markDirty();
        });

        li.appendChild(actions);
      } else {
        // Read-only view: only show a note if one was actually written.
        commentWrap.hidden = !(comments[dayKey] || "").trim();
      }

      li.appendChild(commentWrap);
      listEl.appendChild(li);
    });

    attachDragLater.forEach(([li, handle]) => attachDrag(li, handle));
    attachDragLater.length = 0;
    refreshPositions();
  }

  const attachDragLater = [];

  function attachDrag(li, handle) {
    handle.addEventListener("pointerdown", (e) => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      e.preventDefault();

      const pointerStartY = e.clientY;
      const scrollStartY = window.scrollY;
      // Every DOM reinsertion moves this row's resting position. We add the
      // difference back into the transform so the row stays exactly under
      // the cursor instead of leaping by its own height each time.
      let baselineShift = 0;
      let lastClientY = e.clientY;
      let swapLockedUntil = 0;
      let autoScrollFrame = null;

      li.classList.add("dragging");
      document.body.classList.add("is-dragging");
      try { handle.setPointerCapture(e.pointerId); } catch (err) { /* no-op */ }

      function place() {
        const dy =
          lastClientY - pointerStartY + (window.scrollY - scrollStartY) + baselineShift;
        li.style.transform = "translateY(" + dy + "px)";
      }

      // Swap with a neighbour only once its midpoint is crossed, then hold
      // off until the animation settles. Re-evaluating against mid-flight
      // positions is what made it oscillate and run to the end of the list.
      function maybeSwap() {
        if (performance.now() < swapLockedUntil) return;

        const rect = li.getBoundingClientRect();
        const center = rect.top + rect.height / 2;
        const prev = li.previousElementSibling;
        const next = li.nextElementSibling;

        let mutate = null;
        if (prev) {
          const r = prev.getBoundingClientRect();
          if (center < r.top + prev.offsetHeight / 2) {
            mutate = () => listEl.insertBefore(li, prev);
          }
        }
        if (!mutate && next) {
          const r = next.getBoundingClientRect();
          if (center > r.top + next.offsetHeight / 2) {
            mutate = () => listEl.insertBefore(next, li);
          }
        }
        if (!mutate) return;

        const { before, after } = animateReorder(mutate);
        baselineShift += before.get(li) - after.get(li);
        place();
        refreshPositions();
        swapLockedUntil = performance.now() + (REDUCED_MOTION ? 0 : FLIP_MS);
      }

      // Dragging toward the top or bottom of the window scrolls the page
      // gently rather than stranding the row at the edge.
      function autoScrollTick() {
        const margin = 90;
        const maxSpeed = 14;
        let dv = 0;
        if (lastClientY < margin) {
          dv = -maxSpeed * Math.min(1, (margin - lastClientY) / margin);
        } else if (lastClientY > window.innerHeight - margin) {
          dv = maxSpeed * Math.min(1, (lastClientY - (window.innerHeight - margin)) / margin);
        }
        if (dv) {
          window.scrollBy(0, dv);
          place();
          maybeSwap();
        }
        autoScrollFrame = window.requestAnimationFrame(autoScrollTick);
      }
      autoScrollFrame = window.requestAnimationFrame(autoScrollTick);

      function onMove(ev) {
        lastClientY = ev.clientY;
        place();
        maybeSwap();
      }

      function onUp(ev) {
        window.cancelAnimationFrame(autoScrollFrame);
        document.removeEventListener("pointermove", onMove);
        document.removeEventListener("pointerup", onUp);
        document.removeEventListener("pointercancel", onUp);
        document.body.classList.remove("is-dragging");

        // Settle from wherever the finger left it into the row's real slot,
        // instead of snapping.
        const from = li.getBoundingClientRect().top;
        li.style.transform = "";
        li.classList.remove("dragging");
        const to = li.getBoundingClientRect().top;
        if (!REDUCED_MOTION && Math.abs(from - to) > 0.5) {
          li.style.transition = "none";
          li.style.transform = "translateY(" + (from - to) + "px)";
          void li.offsetHeight;
          li.style.transition = "transform " + FLIP_MS + "ms cubic-bezier(.2,.8,.3,1)";
          li.style.transform = "";
          window.setTimeout(() => {
            li.style.transition = "";
          }, FLIP_MS + 40);
        }

        syncOrderFromDom();
        refreshPositions();
        markDirty();
        try { handle.releasePointerCapture(ev.pointerId); } catch (err) { /* no-op */ }
      }

      document.addEventListener("pointermove", onMove);
      document.addEventListener("pointerup", onUp);
      document.addEventListener("pointercancel", onUp);
    });
  }

  render();

  // ------------------------------------------------------------------
  // Saving. Every change saves itself about a second later; "Save now"
  // does it immediately. Leaving the page flushes anything pending.
  // ------------------------------------------------------------------
  const SAVE_DELAY_MS = 1200;
  const RETRY_DELAY_MS = 5000;
  let saveTimer = null;
  let saving = false;
  let saveAgain = false;
  let lastError = null;

  function formatTime(iso) {
    const when = new Date(iso);
    return isNaN(when) ? "" : when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }

  function scheduleSave(delay) {
    if (!biddingOpen) return;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, delay === undefined ? SAVE_DELAY_MS : delay);
  }

  async function save(opts) {
    clearTimeout(saveTimer);
    saveTimer = null;
    if (!biddingOpen || stopped) return;
    if (saving) {
      saveAgain = true;
      return;
    }
    saving = true;
    dirty = false; // anything changed from here on marks it dirty again
    setStatus("Saving\u2026");
    if (saveBtn) saveBtn.disabled = true;
    let retry = false;
    try {
      const res = await fetch(initData.saveUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": initData.csrfToken },
        body: JSON.stringify({
          ranking: order, excluded: Array.from(excluded), comments: comments, base_version: baseVersion,
        }),
        keepalive: !!(opts && opts.keepalive),
      });
      let data = {};
      try { data = await res.json(); } catch (e) { /* not JSON */ }
      if (res.ok && data.ok) {
        lastError = null;
        baseVersion = data.saved_at;
        if (!hasSubmitted) {
          hasSubmitted = true;
          if (saveBtn) saveBtn.textContent = "Save now";
        }
        if (!dirty) {
          setStatus("Saved at " + formatTime(data.saved_at) + " \u2705 You can change it until sign-ups close.", "success");
        }
      } else {
        dirty = true;
        lastError = data.error || "Something went wrong \u2014 press Save to try again.";
        setStatus(lastError, "error");
        if (data.retry === false || res.status === 401 || res.status === 403 || res.status === 409 || res.status === 410) {
          // Retrying can't fix this; say what to do instead.
          stopped = true;
          if (saveBtn) {
            saveBtn.textContent = "Reload the page";
            saveBtn.onclick = function (e) { e.stopImmediatePropagation(); window.location.reload(); };
          }
        } else {
          retry = res.status >= 500;
        }
      }
    } catch (err) {
      dirty = true;
      lastError = "Couldn't reach the server \u2014 check your connection. Trying again shortly\u2026";
      setStatus(lastError, "error");
      retry = true;
    } finally {
      saving = false;
      if (saveBtn) saveBtn.disabled = false;
      if (stopped) {
        saveAgain = false;
      } else if (saveAgain) {
        saveAgain = false;
        save();
      } else if (retry) {
        scheduleSave(RETRY_DELAY_MS);
      }
    }
  }

  if (saveBtn) saveBtn.addEventListener("click", () => { if (!stopped) save(); });

  if (biddingOpen && saveStatus) {
    if (initData.hasSubmitted) {
      const at = initData.savedAt ? " (last saved " + formatTime(initData.savedAt) + ")" : "";
      setStatus("Your ranking is saved" + at + ". Changes save by themselves.", "success");
    } else {
      setStatus("Not saved yet \u2014 if this order is right, press \u201cSave my ranking\u201d.", "warn");
    }
  }
  updateWarning();

  // Leaving or hiding the page: send anything pending right away.
  function flush() {
    if (dirty || saveTimer) save({ keepalive: true });
  }
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") flush();
  });
  window.addEventListener("pagehide", flush);

  window.addEventListener("beforeunload", (e) => {
    // Only nag if a save actually failed; otherwise flush() has it covered.
    if (dirty && lastError) {
      e.preventDefault();
      e.returnValue = "";
    }
  });
})();
