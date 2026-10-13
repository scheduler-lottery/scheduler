// The helper's own small window, opened when Scheduler's page asks for a
// class list from a school's Canvas it hasn't read before: which Canvas, and
// one button that asks Chrome (Chrome then asks the professor). The helper
// (background.js) carries on once Chrome says yes, and closes this window;
// closed without a yes, nothing is read.
const host = (new URLSearchParams(location.search).get("host") || "").toLowerCase();
const ok = /^(?=.{4,120}$)([a-z0-9-]+\.)+[a-z]{2,}$/.test(host) || /^127\.0\.0\.1:\d{2,5}$/.test(host);
const origin = (/^127\.0\.0\.1:/.test(host) ? "http://" : "https://") + host;
const said = document.querySelector("[data-said]");
const ask = document.querySelector("[data-ask]");
document.querySelector("[data-host]").textContent = ok ? host : "";
if (!ok) { ask.hidden = true; said.textContent = "Nothing to allow here. You can close this window."; }

document.querySelector("[data-allow]").addEventListener("click", () => {
  chrome.permissions.request({ origins: [origin + "/*"] }).then((yes) => {
    if (yes) {
      ask.hidden = true;
      const mark = document.createElement("span");
      mark.className = "done-mark";
      mark.textContent = "Allowed.";
      said.replaceChildren(mark, " Taking you back to Scheduler…");
      setTimeout(() => window.close(), 700);
    } else {
      said.textContent = "Not allowed, so nothing was read. Press Allow to try again, or close this window.";
    }
  }, () => { said.textContent = "Chrome didn't ask. Close this window, and use Scheduler's other way (Canvas's file)."; });
});
document.querySelector("[data-not-now]").addEventListener("click", () => window.close());
