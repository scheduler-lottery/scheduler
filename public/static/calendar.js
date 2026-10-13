// The day picker on the create/edit form: a calendar in a pop-up. Click or
// tap a date to add or remove it. To add (or remove) every day in between,
// press on one date and drag to another with a mouse; on a touch screen,
// press and hold a date, then slide to another (a quick swipe scrolls, as it
// does anywhere else). The picked days show on the form as a list, carried
// by hidden inputs — and the server checks each is a real date.
(function () {
  var field = document.querySelector("[data-day-field]");
  var dialog = document.getElementById("day-calendar");
  if (!field || !dialog) return;

  var list = field.querySelector("[data-day-chips]");
  var openButton = field.querySelector("[data-day-open]");
  var months = dialog.querySelector("[data-calendar]");
  var summary = dialog.querySelector("[data-cal-summary]");
  var doneButton = dialog.querySelector("[data-cal-done]");
  var MAX = parseInt(field.getAttribute("data-max"), 10) || 30;
  var DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  var MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
                     "October", "November", "December"];

  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function isoOf(date) { return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate()); }
  function dateOf(iso) {
    var p = iso.split("-");
    return new Date(+p[0], +p[1] - 1, +p[2]);
  }
  function labelOf(iso) {
    var d = dateOf(iso);
    return DAY_NAMES[d.getDay()] + ", " + MONTH_NAMES[d.getMonth()].slice(0, 3) + " " + d.getDate();
  }
  var TODAY = isoOf(new Date());

  // picked: date -> its day key ("" for a new day). Days saved before days
  // were picked on a calendar have no date; they're kept as they are.
  var picked = {};
  var undated = [];
  Array.prototype.forEach.call(list.querySelectorAll("li"), function (li) {
    var date = li.querySelector('input[name="day_date"]').value;
    var key = li.querySelector('input[name="day_key"]').value;
    var label = li.querySelector('input[name="day_label"]').value;
    if (date) picked[date] = key;
    else undated.push({ key: key, label: label });
  });
  var knownKeys = Object.assign({}, picked);

  function pickedDates() { return Object.keys(picked).sort(); }
  function count() { return pickedDates().length + undated.length; }
  // A day that has passed can't be added; one that was already on the
  // sheet can still be taken off (and put back).
  function closed(iso) { return iso < TODAY && !(iso in picked) && !(iso in knownKeys); }

  var first = pickedDates()[0];
  var view = first ? dateOf(first) : new Date();
  view = new Date(view.getFullYear(), view.getMonth(), 1);

  function hidden(name, value) {
    var input = document.createElement("input");
    input.type = "hidden";
    input.name = name;
    input.value = value;
    return input;
  }

  // The list on the form.
  function renderList() {
    list.innerHTML = "";
    var rows = pickedDates().map(function (iso) { return { key: picked[iso], date: iso, label: labelOf(iso) }; })
      .concat(undated.map(function (d) { return { key: d.key, date: "", label: d.label }; }));
    rows.forEach(function (row) {
      var li = document.createElement("li");
      li.className = "day-chip";
      li.appendChild(hidden("day_key", row.key));
      li.appendChild(hidden("day_date", row.date));
      li.appendChild(hidden("day_label", row.label));
      var text = document.createElement("span");
      text.textContent = row.label;
      li.appendChild(text);
      var remove = document.createElement("button");
      remove.type = "button";
      remove.className = "chip-remove";
      remove.setAttribute("aria-label", "Remove " + row.label);
      remove.textContent = "×";
      remove.addEventListener("click", function () {
        if (row.date) delete picked[row.date];
        else undated = undated.filter(function (d) { return d.key !== row.key; });
        renderList();
        renderCalendar();
      });
      li.appendChild(remove);
      list.appendChild(li);
    });
    if (!rows.length) {
      var empty = document.createElement("li");
      empty.className = "day-chip-empty";
      empty.textContent = "No days yet.";
      list.appendChild(empty);
    }
    if (openButton) openButton.textContent = rows.length ? "Change the days" : "Pick days on a calendar";
    // (The new-sheet form keeps answers in progress: app.js listens.)
    list.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // The calendar in the pop-up: two months side by side (one at a time on
  // a phone, style.css).
  function renderCalendar() {
    months.innerHTML = "";
    for (var m = 0; m < 2; m++) {
      var monthStart = new Date(view.getFullYear(), view.getMonth() + m, 1);
      var box = document.createElement("div");
      box.className = "cal-month";
      var title = document.createElement("h3");
      title.className = "cal-title";
      title.textContent = MONTH_NAMES[monthStart.getMonth()] + " " + monthStart.getFullYear();
      box.appendChild(title);
      var grid = document.createElement("div");
      grid.className = "cal-grid";
      grid.setAttribute("role", "group");
      grid.setAttribute("aria-label", title.textContent);
      DAY_NAMES.forEach(function (name) {
        var head = document.createElement("span");
        head.className = "cal-head";
        head.textContent = name.slice(0, 2);
        head.setAttribute("aria-hidden", "true");
        grid.appendChild(head);
      });
      for (var blank = 0; blank < monthStart.getDay(); blank++) {
        grid.appendChild(document.createElement("span"));
      }
      var days = new Date(monthStart.getFullYear(), monthStart.getMonth() + 1, 0).getDate();
      for (var d = 1; d <= days; d++) {
        var iso = isoOf(new Date(monthStart.getFullYear(), monthStart.getMonth(), d));
        var cell = document.createElement("button");
        cell.type = "button";
        cell.className = "cal-day" + (iso < TODAY ? " past" : "") + (iso === TODAY ? " today" : "");
        cell.setAttribute("data-date", iso);
        cell.setAttribute("aria-pressed", iso in picked ? "true" : "false");
        cell.setAttribute("aria-label", labelOf(iso) + (iso < TODAY ? " (already passed)" : ""));
        cell.disabled = closed(iso);
        cell.textContent = d;
        grid.appendChild(cell);
      }
      box.appendChild(grid);
      months.appendChild(box);
    }
    renderSummary();
  }

  // What's picked, under the calendar, and on Done ("Done (5 days)").
  function renderSummary() {
    var n = count();
    summary.textContent = n
      ? n + (n === 1 ? " day" : " days") + " picked: " + pickedDates().map(labelOf).concat(
          undated.map(function (d) { return d.label; })).join(" · ")
      : "No days picked yet.";
    if (n >= MAX) summary.textContent += " (That's the most a sheet can have.)";
    if (doneButton) doneButton.textContent = n ? "Done (" + n + (n === 1 ? " day)" : " days)") : "Done";
  }

  function refreshCells() {
    Array.prototype.forEach.call(months.querySelectorAll(".cal-day"), function (cell) {
      var iso = cell.getAttribute("data-date");
      cell.setAttribute("aria-pressed", iso in picked ? "true" : "false");
      cell.disabled = closed(iso);
    });
  }

  // After a pick: the list on the form, the calendar's days and its summary
  // (the calendar itself stays, so whatever has focus keeps it).
  function picksChanged() {
    renderList();
    refreshCells();
    renderSummary();
  }

  function toggle(iso) {
    if (iso in picked) delete picked[iso];
    else if (!closed(iso) && count() < MAX) picked[iso] = knownKeys[iso] || "";
    picksChanged();
  }

  function datesBetween(a, b) {
    var start = dateOf(a < b ? a : b);
    var end = a < b ? b : a;
    var out = [];
    for (var day = start; isoOf(day) <= end; day = new Date(day.getFullYear(), day.getMonth(), day.getDate() + 1)) {
      out.push(isoOf(day));
    }
    return out;
  }

  // Pressing on a day starts a drag: everything from there to wherever the
  // pointer goes is added (or, starting on a picked day, removed). A mouse
  // starts it at once; a finger only after holding still on a day for a
  // moment, so a swipe across the dates scrolls instead.
  var drag = null;
  var dragged = false;
  var press = null; // a finger on a day, before it's a tap or a hold
  var lastPointer = 0; // when a mouse or finger last picked here (its click is already done)
  var HOLD = 350; // ms
  var SLOP = 8; // px a finger can wander and still be tapping

  function apply(target) {
    var next = Object.assign({}, drag.before);
    datesBetween(drag.anchor, target).forEach(function (iso) {
      if (drag.adding) {
        if (!(iso in next) && !(iso < TODAY && !(iso in knownKeys)) && Object.keys(next).length + undated.length < MAX) {
          next[iso] = knownKeys[iso] || "";
        }
      } else {
        delete next[iso];
      }
    });
    picked = next;
    refreshCells();
  }

  function startDrag(iso) {
    drag = { anchor: iso, adding: !(iso in picked), before: Object.assign({}, picked), last: iso };
    dragged = false;
    apply(iso);
  }

  function cancelPress() {
    if (press) clearTimeout(press.timer);
    press = null;
  }

  months.addEventListener("pointerdown", function (e) {
    var cell = e.target.closest(".cal-day");
    if (!cell || cell.disabled || e.button > 0 || drag) return;
    var iso = cell.getAttribute("data-date");
    if (e.pointerType === "mouse") {
      e.preventDefault();
      startDrag(iso);
      if (months.setPointerCapture) months.setPointerCapture(e.pointerId);
      return;
    }
    // A finger (or a pen): a tap if it lifts soon without moving; a range
    // if it holds still; a scroll (the browser's) if it moves first.
    cancelPress();
    press = { id: e.pointerId, x: e.clientX, y: e.clientY, iso: iso };
    press.timer = setTimeout(function () {
      if (!press) return;
      var held = press.iso;
      press = null;
      startDrag(held);
      if (navigator.vibrate) { try { navigator.vibrate(10); } catch (err) { /* fine */ } }
    }, HOLD);
  });

  months.addEventListener("pointermove", function (e) {
    if (press && press.id === e.pointerId) {
      if (Math.abs(e.clientX - press.x) > SLOP || Math.abs(e.clientY - press.y) > SLOP) cancelPress();
      return;
    }
    if (!drag) return;
    var under = document.elementFromPoint(e.clientX, e.clientY);
    var cell = under && under.closest(".cal-day");
    if (!cell || !months.contains(cell) || cell.getAttribute("data-date") === drag.last) return;
    drag.last = cell.getAttribute("data-date");
    dragged = true;
    apply(drag.last);
  });

  // While a finger is picking a range, it doesn't scroll the pop-up.
  months.addEventListener("touchmove", function (e) {
    if (drag && e.cancelable) e.preventDefault();
  }, { passive: false });
  // (Nor does holding a day bring up the phone's menu for it.)
  months.addEventListener("contextmenu", function (e) {
    if (drag || press) e.preventDefault();
  });

  function endDrag() {
    if (!drag) return;
    drag = null;
    lastPointer = Date.now();
    picksChanged();
  }
  months.addEventListener("pointerup", function (e) {
    if (press && press.id === e.pointerId) {
      var tapped = press.iso;
      cancelPress();
      lastPointer = Date.now();
      toggle(tapped);
      return;
    }
    endDrag();
  });
  months.addEventListener("pointercancel", function (e) {
    if (press && press.id === e.pointerId) cancelPress(); // the browser took it: a scroll
    endDrag();
  });
  months.addEventListener("lostpointercapture", function (e) {
    if (e.target === months) endDrag();
  });

  // Keyboard (and screen readers): Enter or Space on a date toggles it. A
  // mouse or finger has already done its work by the time its click comes.
  months.addEventListener("click", function (e) {
    var cell = e.target.closest(".cal-day");
    if (!cell || Date.now() - lastPointer < 800) return;
    var iso = cell.getAttribute("data-date");
    toggle(iso);
    var again = months.querySelector('.cal-day[data-date="' + iso + '"]');
    if (again) again.focus();
  });

  // A drag that ends outside the calendar isn't a click on the backdrop
  // (which would close the pop-up).
  dialog.addEventListener("click", function (e) {
    if (dragged && e.target === dialog) {
      e.stopPropagation();
    }
    dragged = false;
  }, true);

  dialog.querySelector("[data-cal-prev]").addEventListener("click", function () {
    view = new Date(view.getFullYear(), view.getMonth() - 1, 1);
    renderCalendar();
  });
  dialog.querySelector("[data-cal-next]").addEventListener("click", function () {
    view = new Date(view.getFullYear(), view.getMonth() + 1, 1);
    renderCalendar();
  });
  dialog.querySelector("[data-cal-clear]").addEventListener("click", function () {
    picked = {};
    picksChanged();
  });

  renderList();
  renderCalendar();
})();

// The deadline picker: the same calendar, one month at a time, for one
// date, and beside it the times as a list to scroll and pick from (under
// it on a phone), with quick picks above. It fills the form's plain date
// box and time menu, which stay as they are when JavaScript is off.
(function () {
  var field = document.querySelector("[data-deadline-field]");
  var dialog = document.getElementById("deadline-calendar");
  if (!field || !dialog) return;

  var dateInput = field.querySelector('input[name="close_date"]');
  var timeSelect = field.querySelector('select[name="close_time"]');
  var chip = field.querySelector("[data-deadline-chip]");
  var openButton = field.querySelector("[data-deadline-open]");
  var months = dialog.querySelector("[data-deadline-calendar]");
  var summary = dialog.querySelector("[data-dcal-summary]");
  var zone = field.getAttribute("data-zone") || "";
  var DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  var MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
                     "October", "November", "December"];

  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function isoOf(date) { return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate()); }
  function dateOf(iso) {
    var p = iso.split("-");
    return new Date(+p[0], +p[1] - 1, +p[2]);
  }
  function labelOf(iso) {
    var d = dateOf(iso);
    return DAY_NAMES[d.getDay()] + ", " + MONTH_NAMES[d.getMonth()].slice(0, 3) + " " + d.getDate();
  }
  var TODAY = isoOf(new Date());

  // The time: the time menu holds it ("17:00"); the list shows it.
  var timeList = dialog.querySelector("[data-time-list]");
  var dayHead = dialog.querySelector("[data-dcal-day]");
  function timeLabel() {
    var option = timeSelect.options[timeSelect.selectedIndex];
    return option ? option.textContent.trim() : "";
  }
  function showChosenTime() {
    var chosen = timeList.querySelector('input[value="' + timeSelect.value + '"]');
    if (!chosen) return;
    chosen.checked = true;
    // In the middle of the list, so the times around it show too.
    var slot = chosen.closest(".time-slot");
    timeList.scrollTop = slot.offsetTop - timeList.offsetTop - (timeList.clientHeight - slot.offsetHeight) / 2;
  }

  var view = dateInput.value ? dateOf(dateInput.value) : new Date();
  view = new Date(view.getFullYear(), view.getMonth(), 1);

  function changed() {
    dateInput.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // Quick picks, counted back from the first presentation day still ahead:
  // a week before it, the evening before, and the end of the day before.
  // Only those still to come show, and only once there are days.
  var presetBox = dialog.querySelector("[data-dcal-presets]");
  var presetList = dialog.querySelector("[data-dcal-preset-list]");
  function shifted(iso, days) {
    var d = dateOf(iso);
    return isoOf(new Date(d.getFullYear(), d.getMonth(), d.getDate() + days));
  }
  function renderPresets() {
    if (!presetBox) return;
    var first = Array.prototype.map.call(document.querySelectorAll('[data-day-chips] input[name="day_date"]'),
      function (input) { return input.value; }).filter(function (iso) { return iso >= TODAY; }).sort()[0];
    var now = new Date();
    var clock = pad(now.getHours()) + ":" + pad(now.getMinutes());
    var picks = !first ? [] : [
      { date: shifted(first, -7), time: "17:00", says: "a week before the first day" },
      { date: shifted(first, -1), time: "17:00", says: "the evening before the first day" },
      { date: shifted(first, -1), time: "23:59", says: "the end of the day before" },
    ].filter(function (pick) {
      return (pick.date > TODAY || (pick.date === TODAY && pick.time > clock))
        && timeSelect.querySelector('option[value="' + pick.time + '"]');
    });
    presetList.innerHTML = "";
    picks.forEach(function (pick, i) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "dcal-preset";
      button.setAttribute("aria-pressed", pick.date === dateInput.value && pick.time === timeSelect.value ? "true" : "false");
      var when = document.createElement("strong");
      when.textContent = labelOf(pick.date) + ", " + timeSelect.querySelector('option[value="' + pick.time + '"]').textContent.trim();
      var says = document.createElement("small");
      says.textContent = pick.says;
      button.appendChild(when);
      button.appendChild(says);
      button.addEventListener("click", function () {
        dateInput.value = pick.date;
        timeSelect.value = pick.time;
        timeSelect.dispatchEvent(new Event("change", { bubbles: true }));
        view = new Date(dateOf(pick.date).getFullYear(), dateOf(pick.date).getMonth(), 1);
        changed();
        render();
        showChosenTime();
        if (presetList.children[i]) presetList.children[i].focus();
      });
      presetList.appendChild(button);
    });
    presetBox.hidden = !picks.length;
  }

  function renderChip() {
    chip.innerHTML = "";
    var li = document.createElement("li");
    if (dateInput.value) {
      li.className = "day-chip";
      var text = document.createElement("span");
      text.textContent = labelOf(dateInput.value) + " · " + timeLabel() + (zone ? " " + zone : "");
      li.appendChild(text);
      var remove = document.createElement("button");
      remove.type = "button";
      remove.className = "chip-remove";
      remove.setAttribute("aria-label", "No deadline");
      remove.textContent = "×";
      remove.addEventListener("click", function () {
        dateInput.value = "";
        changed();
        render();
      });
      li.appendChild(remove);
    } else {
      li.className = "day-chip-empty";
      li.textContent = "No deadline: you'll close sign-ups yourself.";
    }
    chip.appendChild(li);
    openButton.textContent = dateInput.value ? "Change the date and time" : "Pick a date and time";
  }

  function renderCalendar() {
    months.innerHTML = "";
    for (var m = 0; m < 1; m++) {
      var monthStart = new Date(view.getFullYear(), view.getMonth() + m, 1);
      var box = document.createElement("div");
      box.className = "cal-month";
      var title = document.createElement("h3");
      title.className = "cal-title";
      title.textContent = MONTH_NAMES[monthStart.getMonth()] + " " + monthStart.getFullYear();
      box.appendChild(title);
      var grid = document.createElement("div");
      grid.className = "cal-grid";
      grid.setAttribute("role", "group");
      grid.setAttribute("aria-label", title.textContent);
      DAY_NAMES.forEach(function (name) {
        var head = document.createElement("span");
        head.className = "cal-head";
        head.textContent = name.slice(0, 2);
        head.setAttribute("aria-hidden", "true");
        grid.appendChild(head);
      });
      for (var blank = 0; blank < monthStart.getDay(); blank++) grid.appendChild(document.createElement("span"));
      var days = new Date(monthStart.getFullYear(), monthStart.getMonth() + 1, 0).getDate();
      for (var d = 1; d <= days; d++) {
        var iso = isoOf(new Date(monthStart.getFullYear(), monthStart.getMonth(), d));
        var cell = document.createElement("button");
        cell.type = "button";
        cell.className = "cal-day" + (iso < TODAY ? " past" : "") + (iso === TODAY ? " today" : "");
        cell.setAttribute("data-date", iso);
        cell.setAttribute("aria-pressed", iso === dateInput.value ? "true" : "false");
        cell.setAttribute("aria-label", labelOf(iso) + (iso < TODAY ? " (already passed)" : ""));
        if (iso < TODAY) cell.disabled = true;
        cell.textContent = d;
        grid.appendChild(cell);
      }
      box.appendChild(grid);
      months.appendChild(box);
    }
  }

  function renderTime() {
    dayHead.textContent = dateInput.value ? "Time on " + labelOf(dateInput.value) : "Time";
    summary.textContent = dateInput.value
      ? "Sign-ups close " + labelOf(dateInput.value) + " at " + timeLabel() + (zone ? " " + zone : "") + "."
      : "Pick a date. The time is " + timeLabel() + (zone ? " " + zone : "") + ".";
  }

  function render() {
    renderChip();
    renderCalendar();
    renderTime();
    renderPresets();
  }

  months.addEventListener("click", function (e) {
    var cell = e.target.closest(".cal-day");
    if (!cell || cell.disabled) return;
    var iso = cell.getAttribute("data-date");
    dateInput.value = iso === dateInput.value ? "" : iso;
    changed();
    render();
    var again = months.querySelector('.cal-day[data-date="' + iso + '"]');
    if (again) again.focus();
  });

  timeList.addEventListener("change", function (e) {
    if (e.target.name !== "deadline-time") return;
    timeSelect.value = e.target.value;
    timeSelect.dispatchEvent(new Event("change", { bubbles: true }));
    renderChip();
    renderTime();
    renderPresets();
  });
  // Opening the picker: quick picks from the days as they are now, and the
  // chosen time in view (once the window shows).
  openButton.addEventListener("click", function () {
    renderPresets();
    setTimeout(showChosenTime, 0);
  });

  dialog.querySelector("[data-dcal-prev]").addEventListener("click", function () {
    view = new Date(view.getFullYear(), view.getMonth() - 1, 1);
    renderCalendar();
  });
  dialog.querySelector("[data-dcal-next]").addEventListener("click", function () {
    view = new Date(view.getFullYear(), view.getMonth() + 1, 1);
    renderCalendar();
  });
  dialog.querySelector("[data-dcal-clear]").addEventListener("click", function () {
    dateInput.value = "";
    changed();
    render();
  });

  field.querySelector("[data-deadline-native]").hidden = true;
  chip.hidden = false;
  openButton.hidden = false;
  render();
})();

// Both calendars: on a touch screen, a quick sideways swipe across the
// dates shows the next month (or the one before), as Later and Earlier do.
// (Holding a date first is picking a run of days, not a swipe.)
(function () {
  [["[data-calendar]", "[data-cal-prev]", "[data-cal-next]"],
   ["[data-deadline-calendar]", "[data-dcal-prev]", "[data-dcal-next]"]].forEach(function (parts) {
    var months = document.querySelector(parts[0]);
    var dialog = months && months.closest(".modal-dialog");
    if (!dialog) return;
    var start = null;
    months.addEventListener("pointerdown", function (e) {
      start = e.pointerType === "mouse" ? null : { id: e.pointerId, x: e.clientX, y: e.clientY, t: Date.now() };
    });
    months.addEventListener("pointermove", function (e) {
      if (!start || start.id !== e.pointerId || start.moved !== undefined) return;
      if (Math.abs(e.clientX - start.x) > 8 || Math.abs(e.clientY - start.y) > 8) start.moved = Date.now() - start.t;
    });
    months.addEventListener("pointercancel", function () { start = null; });
    months.addEventListener("pointerup", function (e) {
      if (!start || start.id !== e.pointerId) return;
      var dx = e.clientX - start.x;
      var dy = e.clientY - start.y;
      var swiped = start.moved !== undefined && start.moved < 300 && Date.now() - start.t < 800
        && Math.abs(dx) >= 60 && Math.abs(dx) > 2 * Math.abs(dy);
      start = null;
      if (swiped) dialog.querySelector(dx < 0 ? parts[2] : parts[1]).click();
    });
  });
})();
