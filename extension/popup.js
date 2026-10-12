// Scheduler Helper's own box (its toolbar button, and opened by itself once
// a class list it brought is in Scheduler): "Delete me" in one click, or
// back to Scheduler. "Done" is remembered in the button's badge.
const SCHEDULER = ((chrome.runtime.getManifest().externally_connectable || {}).matches || [])
  .map((pattern) => (pattern.match(/^(https?:\/\/[^/]+)\//) || [])[1]).filter(Boolean)[0];

chrome.action.getBadgeText({}).then((text) => {
  const done = text === "✓";
  document.querySelector('[data-state="done"]').hidden = !done;
  document.querySelector('[data-state="ready"]').hidden = done;
});

document.querySelectorAll("[data-delete]").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll("[data-state]").forEach((box) => { box.hidden = true; });
  document.querySelector("[data-gone]").hidden = false;
  chrome.management.uninstallSelf({ showConfirmDialog: false }).catch(() => window.close());
}));
document.querySelector("[data-keep]").addEventListener("click", () => {
  chrome.action.setBadgeText({ text: "" }).finally(() => window.close());
});
document.querySelector("[data-open]").addEventListener("click", () => {
  chrome.tabs.create({ url: SCHEDULER + "/teach/" }).finally(() => window.close());
});
