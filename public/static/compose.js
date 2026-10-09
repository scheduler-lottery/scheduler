// Emails sent from the person's own email account: the backup for the
// site's sign-in emails. Each [data-compose] link carries one message
// (data-to, data-bcc, data-subject, data-body); this points it at the email
// service picked in the "Emails open in" menu. The menu starts on the
// service that matches the person's address (data-default, worked out by
// compose.py) and remembers a change on this device. Without JavaScript the
// links are plain mailto: links, which open the device's own email app.
(function () {
  var links = Array.prototype.slice.call(document.querySelectorAll("[data-compose]"));
  var pickers = Array.prototype.slice.call(document.querySelectorAll("[data-mail-picker]"));
  if (!links.length && !pickers.length) return;

  var ua = navigator.userAgent || "";
  var device = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1) ? "ios"
    : /Android/.test(ua) ? "android"
    : /Macintosh|Mac OS X/.test(ua) ? "mac"
    : /Windows/.test(ua) ? "windows" : "other";
  var APP = {
    mac: ["Apple Mail on this Mac", "Opens a new message in Apple Mail, or in Outlook if you've made Outlook your default email app."],
    windows: ["Outlook or Mail on this PC", "Opens a new message in this PC's default email app (usually Outlook or Mail)."],
    ios: ["Mail on this iPhone or iPad", "Opens a new message in Mail, or in whichever email app is your default."],
    android: ["The email app on this phone", "Opens a new message in this phone's email app (Gmail, Outlook, or another)."],
    other: ["The email app on this computer", "Opens a new message in this computer's default email app."],
  }[device];
  var NOTES = {
    outlook: "Opens a new message in Outlook on the web: Microsoft 365 and Exchange, which most universities use. Sign in there if it asks.",
    gmail: "Opens a new message in Gmail (Google Workspace accounts too).",
    app: APP[1],
    outlookcom: "Opens a new message in Outlook.com (personal Outlook, Hotmail and Live accounts).",
    yahoo: "Opens a new message in Yahoo Mail.",
    copy: "The button copies the message. Paste it into a new email in any program, to the address shown.",
  };

  function enc(value) { return encodeURIComponent(value || ""); }
  function addresses(list) {
    return (list || "").split(",").map(function (a) { return enc(a.trim()).replace(/%40/g, "@"); })
      .filter(Boolean).join(",");
  }
  function query(pairs) {
    return pairs.filter(function (p) { return p[1]; })
      .map(function (p) { return p[0] + "=" + (p[2] ? p[1] : enc(p[1])); }).join("&");
  }
  function tidy(list) {
    return (list || "").split(",").map(function (a) { return a.trim(); }).filter(Boolean).join(",");
  }
  function web(base, m, subjectKey, extra) {
    return base + query((extra || []).concat([
      ["to", tidy(m.to)], ["bcc", tidy(m.bcc)], [subjectKey, m.subject], ["body", m.body],
    ]));
  }
  var BUILD = {
    app: function (m) {
      // Line breaks in mailto: bodies are CRLF (RFC 6068); Outlook needs it.
      return "mailto:" + addresses(m.to) + "?" + query([
        ["bcc", addresses(m.bcc), true], ["subject", m.subject], ["body", (m.body || "").replace(/\r?\n/g, "\r\n")],
      ]);
    },
    outlook: function (m) { return web("https://outlook.office.com/mail/deeplink/compose?", m, "subject"); },
    outlookcom: function (m) { return web("https://outlook.live.com/mail/0/deeplink/compose?", m, "subject"); },
    gmail: function (m) {
      return web("https://mail.google.com/mail/?", m, "su", [["authuser", m.account], ["view", "cm"], ["fs", "1"]]);
    },
    yahoo: function (m) { return web("https://compose.mail.yahoo.com/?", m, "subject"); },
  };

  var picked = null;
  try { picked = localStorage.getItem("mail-service"); } catch (e) { /* storage blocked */ }
  var first = pickers[0];
  var suggested = (first && first.getAttribute("data-default")) || "app";
  var account = (first && first.getAttribute("data-account")) || "";
  // On a phone, its own email app beats a website.
  var current = BUILD[picked] || picked === "copy" ? picked
    : device === "ios" || device === "android" ? "app" : suggested;

  function message(link) {
    return {
      to: link.getAttribute("data-to"), bcc: link.getAttribute("data-bcc"), subject: link.getAttribute("data-subject"),
      body: link.getAttribute("data-body"), account: account,
    };
  }

  function apply() {
    links.forEach(function (link) {
      if (current === "copy") {
        link.setAttribute("href", "#");
        link.removeAttribute("target");
      } else {
        link.setAttribute("href", BUILD[current](message(link)));
        if (current === "app") link.removeAttribute("target");
        else { link.setAttribute("target", "_blank"); link.setAttribute("rel", "noopener"); }
      }
    });
    pickers.forEach(function (picker) {
      picker.querySelector("[data-mail-service]").value = current;
      // "Picked to match your address" only when the address told us something.
      var why = !picked && current === suggested && suggested !== "app" ? picker.getAttribute("data-why") : "";
      picker.querySelector("[data-mail-note]").textContent = NOTES[current] + (why ? " " + why : "");
    });
  }

  function copyText(text, done) {
    var fallback = function () {
      var area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      try { document.execCommand("copy"); } catch (e) { /* nothing more to try */ }
      document.body.removeChild(area);
      done();
    };
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, fallback);
    else fallback();
  }

  function say(link, words) {
    var note = link.parentNode.querySelector(".compose-status");
    if (!note) {
      note = document.createElement("span");
      note.className = "compose-status";
      note.setAttribute("role", "status");
      link.insertAdjacentElement("afterend", note);
    }
    note.textContent = words;
  }

  links.forEach(function (link) {
    link.addEventListener("click", function (e) {
      var row = link.closest("[data-mail-row]");
      if (row) row.classList.add("is-opened");
      if (current !== "copy") return;
      e.preventDefault();
      copyText(link.getAttribute("data-body") || "", function () {
        var to = link.getAttribute("data-bcc") ? "your class (use “Copy all the addresses”)" : link.getAttribute("data-to");
        say(link, "Copied. Paste it into a new email to " + to + ".");
      });
    });
  });

  pickers.forEach(function (picker) {
    var appChoice = picker.querySelector('option[value="app"]');
    if (appChoice) appChoice.textContent = APP[0];
    picker.querySelector("[data-mail-service]").addEventListener("change", function (e) {
      current = e.target.value;
      picked = current;
      try { localStorage.setItem("mail-service", current); } catch (err) { /* lasts for this page */ }
      apply();
    });
    picker.hidden = false;
  });
  apply();
})();
