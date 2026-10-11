// The page where the professor lets Scheduler Helper use their Canvas and
// Scheduler. Safari asks for each site rather than at install; this page
// (opened when the helper is first turned on, from its toolbar button, or
// when it needs access) asks for both in one click, then brings the
// professor back to Scheduler's page, reloaded, so its button is there. In
// Chrome both are granted at install, so it just says "All set".
const manifest = chrome.runtime.getManifest();
const SCHEDULER = (manifest.externally_connectable || {}).matches || [];
const ORIGINS = (manifest.host_permissions || []).concat(SCHEDULER);
const ask = document.querySelector("[data-ask]");
const done = document.querySelector("[data-done]");
document.querySelector("[data-id]").textContent = chrome.runtime.id;

function show(allowed) {
  ask.hidden = allowed;
  done.hidden = !allowed;
}

// The Scheduler page the professor came from (the one used last), reloaded
// and in front.
async function backToScheduler() {
  const tabs = await chrome.tabs.query({ url: SCHEDULER }).catch(() => []);
  const tab = tabs.sort((a, b) => (b.lastAccessed || 0) - (a.lastAccessed || 0))[0];
  if (!tab) return;
  await chrome.tabs.reload(tab.id).catch(() => {});
  await chrome.tabs.update(tab.id, { active: true }).catch(() => {});
  if (chrome.windows && chrome.windows.update) await chrome.windows.update(tab.windowId, { focused: true }).catch(() => {});
}

chrome.permissions.contains({ origins: ORIGINS }).then(show, () => show(false));
document.querySelector("[data-allow]").addEventListener("click", () => {
  chrome.permissions.request({ origins: ORIGINS }).then((allowed) => {
    show(allowed);
    if (allowed) backToScheduler();
  }, () => show(false));
});
