// The page where the professor lets Scheduler Helper use their Canvas and
// Scheduler. Safari asks for each site rather than at install; this page
// (opened when the helper is first turned on, from its toolbar button, or
// when it needs access) asks for both in one click. In Chrome both are
// granted at install, so it just says "All set".
const manifest = chrome.runtime.getManifest();
const ORIGINS = (manifest.host_permissions || [])
  .concat((manifest.externally_connectable || {}).matches || []);
const ask = document.querySelector("[data-ask]");
const done = document.querySelector("[data-done]");
document.querySelector("[data-id]").textContent = chrome.runtime.id;

function show(allowed) {
  ask.hidden = allowed;
  done.hidden = !allowed;
}
chrome.permissions.contains({ origins: ORIGINS }).then(show, () => show(false));
document.querySelector("[data-allow]").addEventListener("click", () => {
  chrome.permissions.request({ origins: ORIGINS }).then(show, () => show(false));
});
