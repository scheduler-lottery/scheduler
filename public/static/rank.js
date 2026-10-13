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
  const saveRow = saveStatus ? saveStatus.closest(".save-row") : null;
  const warningEl = document.getElementById("rank-warning");
  let dirty = false;

  const SVG_NS = "http://www.w3.org/2000/svg";

  // Phones (and phones on their side) get compact rows: the #, the day, ▲ ▼
  // and the grip on one line, with everything else in a drawer that opens
  // from the day's name. style.css uses the same query for the Save bar and
  // the short intro, so the two always agree.
  const COMPACT_QUERY = "(max-width: 640px), (max-height: 500px) and (pointer: coarse)";
  const compactMQ = window.matchMedia ? window.matchMedia(COMPACT_QUERY) : null;
  let compact = !!(compactMQ && compactMQ.matches);
  let openDay = null; // the day whose drawer is open (one at a time)
  let lastPointerType = ""; // what pressed the last button: "mouse", "touch" or "pen"
  let quietTimer = null; // (the phone's "Saved" pill, faded a moment after it shows)

  // Wide screens get the whole sentence; phones, where the Save bar has to
  // stay small, a short version of it (`short`, when there is one).
  function setStatus(text, cls, short) {
    if (!saveStatus) return;
    saveStatus.className = "save-status" + (cls ? " " + cls : "");
    if (!short || short === text) {
      saveStatus.textContent = text;
    } else {
      saveStatus.textContent = "";
      const full = document.createElement("span");
      full.className = "save-status-full";
      full.textContent = text;
      const brief = document.createElement("span");
      brief.className = "save-status-short";
      brief.textContent = short;
      saveStatus.appendChild(full);
      saveStatus.appendChild(brief);
    }
    // Once something is saved, changes save by themselves, so on a phone the
    // bar shrinks to a small status pill, which fades once it's been read. A
    // problem brings the button back.
    if (saveRow) {
      saveRow.classList.toggle("saved", hasSubmitted && !stopped && cls !== "error");
      clearTimeout(quietTimer);
      saveRow.classList.remove("quiet");
      if (cls === "success") quietTimer = setTimeout(() => saveRow.classList.add("quiet"), 2500);
    }
  }

  function markDirty() {
    dirty = true;
    updateWarning();
    if (stopped) return;
    setStatus("Saving in a moment…", "dirty", "Saving…");
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

  // An open bubble that would run off either side of the screen is nudged
  // back in, at least 8px from each edge (style.css reads --tip-shift).
  function placeTip(wrap) {
    const bubble = wrap.querySelector(".tip-bubble");
    if (!bubble) return;
    wrap.style.setProperty("--tip-shift", "0px");
    const r = bubble.getBoundingClientRect();
    if (!r.width) return; // closed
    const edge = 8;
    const right = document.documentElement.clientWidth - edge;
    let shift = 0;
    if (r.left < edge) shift = edge - r.left;
    else if (r.right > right) shift = Math.max(right - r.right, edge - r.left);
    if (shift) wrap.style.setProperty("--tip-shift", Math.round(shift) + "px");
  }
  ["pointerover", "focusin"].forEach((type) => {
    listEl.addEventListener(type, (e) => {
      const wrap = e.target.closest && e.target.closest(".tip");
      if (wrap) placeTip(wrap);
    });
  });
  // A tap opens a bubble in popover.js, which runs first.
  document.addEventListener("click", () => {
    listEl.querySelectorAll(".tip.tip-open").forEach(placeTip);
  });

  function makeIcon(d, size, filled) {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("aria-hidden", "true");
    if (size) svg.setAttribute("class", size);
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", d);
    if (filled) {
      path.setAttribute("fill", "currentColor");
    } else {
      path.setAttribute("stroke", "currentColor");
      path.setAttribute("stroke-width", "2.2");
      path.setAttribute("stroke-linecap", "round");
      path.setAttribute("stroke-linejoin", "round");
    }
    svg.appendChild(path);
    return svg;
  }

  // ▲ and ▼, solid, as the page's words call them (the chevron is for opening a day).
  function makeArrow(up) {
    return makeIcon(up ? "M12 6.5l6.5 10h-13z" : "M12 17.5l6.5-10h-13z", "", true);
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
      const first = idx === 0;
      const last = idx === children.length - 1;
      child.querySelectorAll(".move-up, .move-top").forEach((b) => { b.disabled = first; });
      child.querySelectorAll(".move-down, .move-bottom").forEach((b) => { b.disabled = last; });
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

  // The part of the window where the list can actually be seen: below the
  // top bar (while it's on screen) and above whatever sits on the bottom
  // edge (the Save bar, and the tab bar on phones). Measured each time,
  // because the bars change with the screen, the keyboard and the text size.
  function visibleBand() {
    let top = 0;
    let bottom = window.innerHeight;
    const topbar = document.querySelector(".topbar");
    if (topbar) top = Math.max(0, Math.min(bottom, topbar.getBoundingClientRect().bottom));
    const tabbar = document.querySelector(".tabbar");
    if (tabbar) {
      const r = tabbar.getBoundingClientRect();
      if (r.height && r.top < bottom && r.bottom > bottom - 2) bottom = r.top;
    }
    if (saveRow) {
      const r = saveRow.getBoundingClientRect();
      // Only while it's stuck to the bottom edge, not where it sits below the list.
      if (r.height && r.top < bottom && r.bottom > bottom - 2) bottom = r.top;
    }
    if (bottom - top < 120) bottom = Math.min(window.innerHeight, top + 120);
    return { top, bottom };
  }

  // Nudge the page only when the row has actually drifted out of the
  // visible band — jumping the viewport on every button press is disorienting.
  function keepInView(el) {
    const rect = el.getBoundingClientRect();
    const band = visibleBand();
    const margin = 8;
    let dy = 0;
    if (rect.top < band.top + margin) dy = rect.top - band.top - margin;
    else if (rect.bottom > band.bottom - margin) {
      dy = Math.min(rect.bottom - band.bottom + margin, rect.top - band.top - margin);
    }
    if (dy) window.scrollBy({ top: dy, behavior: REDUCED_MOTION ? "auto" : "smooth" });
  }

  function flash(li) {
    li.classList.remove("just-moved");
    void li.offsetHeight;
    li.classList.add("just-moved");
    window.setTimeout(() => li.classList.remove("just-moved"), 700);
  }

  // Moves a row to place `target` in the list. On a touch screen (and on a
  // phone) the button just pressed stays under the finger: the page scrolls
  // by however far the row moved, in the same step as the move, so the row
  // stays put and its neighbours slide past it. Otherwise the page keeps
  // still and only a row that went out of sight is brought back.
  function moveTo(li, target, focusSel, fallbackSel) {
    const siblings = Array.from(listEl.children);
    const idx = siblings.indexOf(li);
    if (target < 0 || target >= siblings.length || target === idx) return;
    const holdStill = compact || lastPointerType === "touch" || lastPointerType === "pen";
    const oldTop = li.getBoundingClientRect().top;

    animateReorder(() => {
      if (target < idx) listEl.insertBefore(li, siblings[target]);
      else listEl.insertBefore(li, siblings[target].nextSibling);
      if (holdStill) window.scrollBy(0, li.getBoundingClientRect().top - oldTop);
    });

    refreshPositions();
    syncOrderFromDom();
    markDirty();
    flash(li);
    if (!holdStill) keepInView(li);

    // Keep focus on the button just pressed so repeated presses work — but
    // preventScroll, because the default focus behaviour is what was yanking
    // the page back to the top of the list.
    const btn = li.querySelector(focusSel);
    const fallback = fallbackSel ? li.querySelector(fallbackSel) : null;
    const toFocus = btn && !btn.disabled ? btn : fallback;
    if (toFocus) toFocus.focus({ preventScroll: true });
  }

  function moveItem(li, delta) {
    const idx = Array.from(listEl.children).indexOf(li);
    moveTo(li, idx + delta, delta < 0 ? ".move-up" : ".move-down", delta < 0 ? ".move-down" : ".move-up");
  }

  // ------------------------------------------------------------------
  // The drawer under a day (phones): Move to top or bottom, can't-do and
  // the note. One open at a time.
  // ------------------------------------------------------------------
  function setDrawer(li, open) {
    const toggle = li.querySelector(".day-toggle");
    const drawer = li.querySelector(".rank-drawer");
    if (!toggle || !drawer) return;
    drawer.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
    li.classList.toggle("open", open);
    if (open) openDay = li.dataset.day;
    else if (openDay === li.dataset.day) openDay = null;
  }

  function openDrawer(li) {
    // Closing a drawer above this row would pull the row up from under the
    // finger, so the page scrolls by as much to keep it where it was.
    const before = li.getBoundingClientRect().top;
    Array.from(listEl.querySelectorAll(".rank-item.open")).forEach((other) => {
      if (other !== li) setDrawer(other, false);
    });
    setDrawer(li, true);
    const shift = li.getBoundingClientRect().top - before;
    if (shift) window.scrollBy(0, shift);
    // Then bring the drawer above the bars, without pushing the day itself
    // off the top.
    const band = visibleBand();
    const r = li.getBoundingClientRect();
    const over = r.bottom + 8 - band.bottom;
    const room = r.top - band.top - 8;
    if (over > 0 && room > 0) {
      window.scrollBy({ top: Math.min(over, room), behavior: REDUCED_MOTION ? "auto" : "smooth" });
    }
  }

  function render() {
    listEl.innerHTML = "";
    const drawers = compact && biddingOpen;
    listEl.classList.toggle("rank-compact", drawers);
    order.forEach((dayKey) => {
      const dayName = DAY_LABELS[dayKey] || dayKey;
      const li = document.createElement("li");
      li.className = "rank-item" + (excluded.has(dayKey) ? " excluded" : "");
      li.dataset.day = dayKey;

      const top = document.createElement("div");
      top.className = "rank-item-top";

      if (biddingOpen) {
        const handle = document.createElement("span");
        handle.className = "drag-handle";
        handle.textContent = "⠿"; // braille-pattern dots, used as a grip icon
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

      const label = document.createElement("span");
      label.className = "day-label";
      // In a narrow row a date wraps as "Wed," / "Nov 25", never "Wed, Nov" / "25".
      label.textContent = drawers ? dayName.replace(/ (\d{1,2})(?=,|$)/, " $1") : dayName;
      if (newDays.has(dayKey)) {
        const tag = document.createElement("span");
        tag.className = "tag tag-warn";
        tag.textContent = "new";
        label.appendChild(document.createTextNode(" "));
        label.appendChild(tag);
      }

      // On a phone the badge and the day's name are one button, which opens
      // the drawer; it also shows what's set inside (can't-do, a note).
      let flags = null;
      if (drawers) {
        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "day-toggle";
        toggle.setAttribute("aria-expanded", "false");
        toggle.setAttribute("aria-controls", "rank-drawer-" + dayKey);
        const text = document.createElement("span");
        text.className = "day-toggle-text";
        text.appendChild(label);
        flags = document.createElement("span");
        flags.className = "rank-flags";
        const cant = document.createElement("span");
        cant.className = "rank-flag rank-flag-cant";
        cant.textContent = "Can't do";
        const noted = document.createElement("span");
        noted.className = "rank-flag rank-flag-note";
        noted.appendChild(makeIcon("M5 4.5h14v11H10l-5 4z"));
        const notedWords = document.createElement("span");
        notedWords.className = "visually-hidden";
        notedWords.textContent = "has a note";
        noted.appendChild(notedWords);
        flags.appendChild(cant);
        flags.appendChild(noted);
        text.appendChild(flags);
        toggle.appendChild(pos);
        toggle.appendChild(text);
        toggle.appendChild(makeIcon("M6 9l6 6 6-6", "day-toggle-chevron"));
        toggle.addEventListener("click", () => {
          if (li.classList.contains("open")) setDrawer(li, false);
          else openDrawer(li);
        });
        top.appendChild(toggle);
      } else {
        top.appendChild(pos);
        top.appendChild(label);
      }
      function updateFlags() {
        if (!flags) return;
        const cantFlag = flags.querySelector(".rank-flag-cant");
        const noteFlag = flags.querySelector(".rank-flag-note");
        cantFlag.hidden = !excluded.has(dayKey);
        noteFlag.hidden = !(comments[dayKey] || "").trim();
        flags.hidden = cantFlag.hidden && noteFlag.hidden;
      }
      updateFlags();

      if (biddingOpen) {
        const moves = document.createElement("div");
        moves.className = "rank-move";
        const upBtn = document.createElement("button");
        upBtn.type = "button";
        upBtn.className = "move-up";
        upBtn.setAttribute("aria-label", "Move " + dayName + " up");
        upBtn.title = "Move up";
        upBtn.appendChild(makeArrow(true));
        upBtn.addEventListener("click", () => moveItem(li, -1));

        const downBtn = document.createElement("button");
        downBtn.type = "button";
        downBtn.className = "move-down";
        downBtn.setAttribute("aria-label", "Move " + dayName + " down");
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
      commentBox.setAttribute("aria-label", "Note about " + dayName);

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
            noteBtn.setAttribute("aria-expanded", "true");
            commentBox.placeholder = "Why can't you do this day? (only your instructor sees this)";
          }
          updateFlags();
          markDirty();
        });
        excludeLabel.appendChild(excludeCb);
        excludeLabel.appendChild(document.createTextNode("I really can't do this day"));
        actions.appendChild(excludeLabel);
        if (!drawers) {
          actions.appendChild(makeTip(
            "For a real conflict, not just a preference. You won't be put on this day unless you mark " +
            "every day. If the days you can do fill up, you're squeezed onto one of them and your instructor " +
            "sees a warning."
          ));
        }

        // Notes start collapsed unless there's already something in them.
        const noteBtn = document.createElement("button");
        noteBtn.type = "button";
        noteBtn.className = "mini-toggle note-toggle";
        const hasNote = !!(comments[dayKey] || "").trim();
        commentWrap.hidden = !hasNote && !excluded.has(dayKey);
        noteBtn.textContent = hasNote ? "Edit note" : "＋ Add a note";
        if (hasNote) noteBtn.classList.add("has-note");
        noteBtn.setAttribute("aria-expanded", String(!commentWrap.hidden));
        // (A box opened by ticking "can't do", still empty: a tap opens it for typing, not closed.)
        noteBtn.addEventListener("click", () => {
          const open = commentWrap.hidden || (document.activeElement !== commentBox && !commentBox.value.trim());
          commentWrap.hidden = !open;
          noteBtn.setAttribute("aria-expanded", String(open));
          if (open) commentBox.focus();
        });
        actions.appendChild(noteBtn);
        if (!drawers) {
          actions.appendChild(makeTip(
            "Only your instructor sees notes. A note doesn't change how days are given out — it " +
            "tells your instructor why, in case they need to adjust things by hand.",
            true
          ));
        }

        commentBox.addEventListener("input", () => {
          comments[dayKey] = commentBox.value;
          const filled = !!commentBox.value.trim();
          noteBtn.textContent = filled ? "Edit note" : "＋ Add a note";
          noteBtn.classList.toggle("has-note", filled);
          updateFlags();
          markDirty();
        });

        if (drawers) {
          const drawer = document.createElement("div");
          drawer.className = "rank-drawer";
          drawer.id = "rank-drawer-" + dayKey;
          drawer.hidden = true;

          // One tap to the top or the bottom, instead of ten on ▲ or ▼.
          const jump = document.createElement("div");
          jump.className = "rank-jump";
          [["move-top", "Move to top", true], ["move-bottom", "Move to bottom", false]].forEach((spec) => {
            const b = document.createElement("button");
            b.type = "button";
            b.className = "btn btn-secondary " + spec[0];
            b.setAttribute("aria-label", spec[1] + ": " + dayName);
            b.textContent = spec[1];
            b.addEventListener("click", () => {
              setDrawer(li, false);
              moveTo(li, spec[2] ? 0 : listEl.children.length - 1, ".day-toggle");
            });
            jump.appendChild(b);
          });
          drawer.appendChild(jump);
          drawer.appendChild(actions);

          // The two "?" explanations, as one plain line: no bubbles to aim for.
          const help = document.createElement("p");
          help.className = "rank-drawer-help";
          help.textContent = "“Can't do” is for a real conflict: you won't get that day unless you mark " +
            "every day. Only your instructor sees notes; they don't change who gets which day.";
          drawer.appendChild(commentWrap); // (right under its button)
          drawer.appendChild(help);
          li.appendChild(drawer);
          if (openDay === dayKey) setDrawer(li, true);
        } else {
          li.appendChild(actions);
        }
      } else {
        // Read-only view: only show a note if one was actually written.
        commentWrap.hidden = !(comments[dayKey] || "").trim();
      }

      if (!drawers) li.appendChild(commentWrap);
      listEl.appendChild(li);
    });

    attachDragLater.forEach(([li, handle]) => attachDrag(li, handle));
    attachDragLater.length = 0;
    refreshPositions();
  }

  const attachDragLater = [];

  // Which kind of pointer pressed a button last, so a tap on ▲ can keep the
  // row under the finger while a mouse click leaves the page where it is.
  listEl.addEventListener("pointerdown", (e) => {
    lastPointerType = e.pointerType || "";
    if (!drag) clearStaleDrag();
  }, true);
  listEl.addEventListener("keydown", () => { lastPointerType = ""; }, true);

  // ------------------------------------------------------------------
  // Dragging. The grip starts a drag at once; on a touch screen (and on a
  // phone) a long press anywhere else on a row's top line does too, so a
  // quick swipe still scrolls the page.
  // ------------------------------------------------------------------
  const LONG_PRESS_MS = 350;
  const PRESS_SLOP = 8; // px a finger may wander before it's a swipe, not a press
  let drag = null; // the drag in progress: { li, end }
  let press = null; // a press that becomes a drag if it's held long enough
  let suppressClickUntil = 0;

  // A row left half-dragged (say the browser never told us the finger
  // lifted) would otherwise ignore every tap.
  function clearStaleDrag() {
    listEl.querySelectorAll(".rank-item.dragging").forEach((el) => {
      el.classList.remove("dragging");
      el.style.transform = "";
    });
    document.body.classList.remove("is-dragging");
  }

  function startDrag(li, startY, pointerId, captureEl) {
    const pointerStartY = startY;
    const scrollStartY = window.scrollY;
    // Every DOM reinsertion moves this row's resting position. We add the
    // difference back into the transform so the row stays exactly under
    // the cursor instead of leaping by its own height each time.
    let baselineShift = 0;
    let lastClientY = startY;
    let swapLockedUntil = 0;
    let autoScrollFrame = null;
    let edgeSince = 0;
    let ended = false;

    li.classList.add("dragging");
    document.body.classList.add("is-dragging");
    try { captureEl.setPointerCapture(pointerId); } catch (err) { /* no-op */ }

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

      // Either way it's the neighbour that moves in the DOM, never the
      // dragged row: taking that row out, even for a moment, can cost it
      // the pointer it has captured.
      let mutate = null;
      if (prev) {
        const r = prev.getBoundingClientRect();
        if (center < r.top + prev.offsetHeight / 2) {
          mutate = () => listEl.insertBefore(prev, li.nextSibling);
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

    // Holding the row near the top or bottom of the part of the page that
    // can be seen (between the bars) scrolls the page: gently at first, then
    // faster the longer it's held there, so a long list doesn't take ages.
    function autoScrollTick(now) {
      const band = visibleBand();
      const margin = Math.min(90, (band.bottom - band.top) / 4);
      let depth = 0;
      if (lastClientY < band.top + margin) {
        depth = -Math.min(1, (band.top + margin - lastClientY) / margin);
      } else if (lastClientY > band.bottom - margin) {
        depth = Math.min(1, (lastClientY - (band.bottom - margin)) / margin);
      }
      if (depth) {
        if (!edgeSince) edgeSince = now;
        const speed = 14 + 26 * Math.min(1, (now - edgeSince) / 1000);
        window.scrollBy(0, depth * speed);
        place();
        maybeSwap();
      } else {
        edgeSince = 0;
      }
      autoScrollFrame = window.requestAnimationFrame(autoScrollTick);
    }
    autoScrollFrame = window.requestAnimationFrame(autoScrollTick);

    function onMove(ev) {
      if (ev.pointerId !== pointerId) return;
      lastClientY = ev.clientY;
      place();
      maybeSwap();
    }

    function onUp() {
      if (ended) return;
      ended = true;
      drag = null;
      window.cancelAnimationFrame(autoScrollFrame);
      document.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerup", onUp);
      document.removeEventListener("pointercancel", onUp);
      captureEl.removeEventListener("lostpointercapture", onUp);
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
      try { captureEl.releasePointerCapture(pointerId); } catch (err) { /* no-op */ }
    }

    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onUp);
    document.addEventListener("pointercancel", onUp);
    captureEl.addEventListener("lostpointercapture", onUp);
    drag = { li, end: onUp };
  }

  function attachDrag(li, handle) {
    handle.addEventListener("pointerdown", (e) => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      if (drag) return;
      e.preventDefault();
      cancelPress();
      startDrag(li, e.clientY, e.pointerId, handle);
    });

    // The long press: on the row's top line, but not on its buttons (▲ ▼
    // and the grip) — and with a mouse on a wide screen only the grip
    // drags, as it always has.
    const area = li.querySelector(".rank-item-top");
    area.addEventListener("pointerdown", (e) => {
      if (drag || press || e.button > 0) return;
      if (e.pointerType === "mouse" && !compact) return;
      if (e.target.closest(".drag-handle, .rank-move, .tip, a, input, textarea")) return;
      press = {
        li, area, id: e.pointerId, x: e.clientX, y: e.clientY, lastY: e.clientY,
        timer: window.setTimeout(() => {
          const p = press;
          press = null;
          if (!p || drag) return;
          p.li.classList.remove("pressing");
          suppressClickUntil = Infinity; // until this finger lifts: it's a drag, not a tap
          startDrag(p.li, p.lastY, p.id, p.area);
          try { if (navigator.vibrate) navigator.vibrate(10); } catch (err) { /* no-op */ }
        }, LONG_PRESS_MS),
      };
      li.classList.add("pressing");
    });
  }

  function cancelPress() {
    if (!press) return;
    window.clearTimeout(press.timer);
    press.li.classList.remove("pressing");
    press = null;
  }

  document.addEventListener("pointermove", (e) => {
    if (!press || e.pointerId !== press.id) return;
    press.lastY = e.clientY;
    if (Math.abs(e.clientX - press.x) > PRESS_SLOP || Math.abs(e.clientY - press.y) > PRESS_SLOP) cancelPress();
  });
  ["pointerup", "pointercancel"].forEach((type) => {
    document.addEventListener(type, (e) => {
      if (press && e.pointerId === press.id) cancelPress();
      // The tap that ends a long-press drag isn't a tap on the day's name.
      if (suppressClickUntil === Infinity) suppressClickUntil = performance.now() + 400;
    });
  });
  listEl.addEventListener("click", (e) => {
    if (performance.now() < suppressClickUntil) {
      e.preventDefault();
      e.stopPropagation();
    }
  }, true);
  // While a row is being dragged, the finger moves the row, not the page.
  listEl.addEventListener("touchmove", (e) => {
    if (drag && e.cancelable) e.preventDefault();
  }, { passive: false });
  listEl.addEventListener("contextmenu", (e) => {
    if (drag || press) e.preventDefault();
  });

  // Anything that pulls the page out from under a drag — folding or
  // unfolding the phone, turning it, switching apps — drops the row where
  // it is, rather than leaving it stuck half-dragged.
  function dropDrag() {
    cancelPress();
    if (drag) drag.end();
  }
  window.addEventListener("orientationchange", dropDrag);
  window.addEventListener("blur", dropDrag);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") dropDrag();
  });

  // ------------------------------------------------------------------
  // Folding, unfolding or turning the phone. The rows change height, so
  // the row at the top of the screen is put back where it was, instead of
  // the page landing a few days off. Crossing into or out of phone width
  // also swaps the rows' layout (compact or full).
  // ------------------------------------------------------------------
  let anchor = null; // { day, offset }: the top row on screen and its distance from the window's top
  let anchorFrame = 0;
  function noteAnchor() {
    anchorFrame = 0;
    anchor = null;
    const rows = listEl.children;
    if (!rows.length) return;
    const top = visibleBand().top;
    // Not into the list yet: nothing to keep.
    if (rows[0].getBoundingClientRect().top >= top) return;
    for (let i = 0; i < rows.length; i++) {
      const r = rows[i].getBoundingClientRect();
      if (r.bottom > top) {
        anchor = { day: rows[i].dataset.day, offset: r.top };
        return;
      }
    }
  }
  window.addEventListener("scroll", () => {
    if (!anchorFrame) anchorFrame = window.requestAnimationFrame(noteAnchor);
  }, { passive: true });

  function rowFor(day) {
    return Array.from(listEl.children).find((li) => li.dataset.day === day) || null;
  }

  let modePending = false;
  function applyMode() {
    const want = !!(compactMQ && compactMQ.matches);
    if (want === compact) return;
    const active = document.activeElement;
    const inList = active && active !== document.body && listEl.contains(active);
    // Mid-note, keep the keyboard up: switch once they're done typing.
    if (inList && active.tagName === "TEXTAREA") {
      modePending = true;
      return;
    }
    modePending = false;
    dropDrag();
    let focusDay = null;
    let focusSel = null;
    if (inList) {
      focusDay = active.closest(".rank-item").dataset.day;
      focusSel = [".move-up", ".move-down", ".day-toggle"].find((sel) => active.matches(sel)) || null;
    }
    compact = want;
    render();
    if (focusDay) {
      const row = rowFor(focusDay);
      const target = row && (row.querySelector(focusSel || ".day-toggle") || row.querySelector("button"));
      if (target) target.focus({ preventScroll: true });
    }
  }
  listEl.addEventListener("focusout", () => {
    if (modePending) window.setTimeout(applyMode, 0);
  });

  let lastWidth = window.innerWidth;
  window.addEventListener("resize", () => {
    // Only a change of width (a fold, a turn, a resized window). The phone's
    // own toolbars and keyboard change the height alone, all the time.
    if (window.innerWidth === lastWidth) return;
    lastWidth = window.innerWidth;
    dropDrag();
    const keep = anchor;
    applyMode();
    const row = keep && rowFor(keep.day);
    if (row) window.scrollBy(0, row.getBoundingClientRect().top - keep.offset);
  });
  if (compactMQ) {
    if (compactMQ.addEventListener) compactMQ.addEventListener("change", applyMode);
    else if (compactMQ.addListener) compactMQ.addListener(applyMode);
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

  // "Saved 7:44 PM · saves by itself", for the phone's pill.
  function savedPill(iso) {
    const at = iso ? formatTime(iso) : "";
    return "Saved" + (at ? " " + at : "") + " · saves by itself";
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
    setStatus("Saving…");
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
          setStatus("Saved at " + formatTime(data.saved_at) + ". You can change it until sign-ups close.", "success",
            savedPill(data.saved_at));
        }
      } else {
        dirty = true;
        lastError = data.error || "Something went wrong — press Save to try again.";
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
      lastError = "Couldn't reach the server — check your connection. Trying again shortly…";
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
      setStatus("Your ranking is saved" + at + ". Changes save by themselves.", "success", savedPill(initData.savedAt));
    } else {
      setStatus("Not saved yet — if this order is right, press “Save my ranking”.", "warn", "Not saved yet");
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
