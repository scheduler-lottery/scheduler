// The day picker on the create/edit form: a calendar in a pop-up. Click a
// date to add or remove it; press on one date and drag to another to add (or
// remove) every day in between. The picked days show on the form as a list,
// carried by hidden inputs — and the server checks each is a real date.
(function () {
  var field = document.querySelector("[data-day-field]");
  var dialog = document.getElementById("day-calendar");
  if (!field || !dialog) return;

  var list = field.querySelector("[data-day-chips]");
  var openButton = field.querySelector("[data-day-open]");
  var months = dialog.querySelector("[data-calendar]");
  var summary = dialog.querySelector("[data-cal-summary]");
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
  }

  // The calendar in the pop-up: two months side by side (stacked on a phone).
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
        cell.textContent = d;
        grid.appendChild(cell);
      }
      box.appendChild(grid);
      months.appendChild(box);
    }
    var n = count();
    summary.textContent = n
      ? n + (n === 1 ? " day" : " days") + " picked: " + pickedDates().map(labelOf).concat(
          undated.map(function (d) { return d.label; })).join(" · ")
      : "No days picked yet.";
    if (n >= MAX) summary.textContent += " (That's the most a sheet can have.)";
  }

  function refreshCells() {
    Array.prototype.forEach.call(months.querySelectorAll(".cal-day"), function (cell) {
      cell.setAttribute("aria-pressed", cell.getAttribute("data-date") in picked ? "true" : "false");
    });
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
  // pointer goes is added (or, starting on a picked day, removed).
  var drag = null;
  var dragged = false;

  function apply(target) {
    var next = Object.assign({}, drag.before);
    datesBetween(drag.anchor, target).forEach(function (iso) {
      if (drag.adding) {
        if (!(iso in next) && Object.keys(next).length + undated.length < MAX) next[iso] = knownKeys[iso] || "";
      } else {
        delete next[iso];
      }
    });
    picked = next;
    refreshCells();
  }

  months.addEventListener("pointerdown", function (e) {
    var cell = e.target.closest(".cal-day");
    if (!cell || e.button > 0) return;
    e.preventDefault();
    var iso = cell.getAttribute("data-date");
    drag = { anchor: iso, adding: !(iso in picked), before: Object.assign({}, picked), last: iso };
    dragged = false;
    apply(iso);
    if (months.setPointerCapture) months.setPointerCapture(e.pointerId);
  });

  months.addEventListener("pointermove", function (e) {
    if (!drag) return;
    var under = document.elementFromPoint(e.clientX, e.clientY);
    var cell = under && under.closest(".cal-day");
    if (!cell || cell.getAttribute("data-date") === drag.last) return;
    drag.last = cell.getAttribute("data-date");
    dragged = true;
    apply(drag.last);
  });

  function endDrag() {
    if (!drag) return;
    drag = null;
    renderList();
    renderCalendar();
  }
  months.addEventListener("pointerup", endDrag);
  months.addEventListener("pointercancel", endDrag);
  months.addEventListener("lostpointercapture", endDrag);

  // Keyboard: Enter or Space on a date toggles it (mouse and touch already
  // did their work on pointerdown).
  months.addEventListener("click", function (e) {
    var cell = e.target.closest(".cal-day");
    if (!cell || e.detail !== 0) return;
    var iso = cell.getAttribute("data-date");
    if (iso in picked) delete picked[iso];
    else if (count() < MAX) picked[iso] = knownKeys[iso] || "";
    renderList();
    renderCalendar();
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
    renderList();
    renderCalendar();
  });

  renderList();
  renderCalendar();
})();
