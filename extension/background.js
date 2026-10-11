// Scheduler Helper: brings a course's class list from the professor's own
// Canvas into Scheduler, when (and only when) Scheduler's page asks.
//
// Scheduler's page talks to it through externally_connectable (only the
// Scheduler site listed in the manifest can): over a port in Chrome and
// Edge, by single messages in Safari. It asks for the professor's
// courses, then one course's students; the helper reads them from Canvas's
// own API, signed in as the professor, exactly as Canvas's pages do, and
// sends back names and emails, nothing else. It never changes anything in
// Canvas, never sees a password, and keeps nothing. The page can also ask
// it to bring the page's own tab back to the front.
//
// How it reads Canvas:
//   1. straight from here (Chrome sends the professor's Canvas session with
//      requests to a site the helper may read), or, if that doesn't work
//      (third-party cookies blocked, say),
//   2. from inside a Canvas tab: an open one, if Canvas is signed in there,
//      or else one it opens in the background and closes after. Not signed
//      in? That new tab comes to the front for the professor to sign in,
//      the usual way, and the helper carries on once Canvas is back. An
//      open tab whose sign-in ran out is left alone: it still shows its old
//      page, with nowhere to sign in, and reloading it could lose the
//      professor's work there.

const VERSION = chrome.runtime.getManifest().version;
const SCHEDULER_ORIGINS = ((chrome.runtime.getManifest().externally_connectable || {}).matches || [])
  .map((pattern) => (pattern.match(/^(https?:\/\/[^/]+)\//) || [])[1])
  .filter(Boolean);
// (Safari's build also names Scheduler's own site among its websites, so
// Safari asks the professor about it too: that one isn't a Canvas.)
const CANVAS_HOSTS = (chrome.runtime.getManifest().host_permissions || [])
  .map((pattern) => (pattern.match(/^https?:\/\/([^/]+)\/\*$/) || [])[1])
  .filter((host) => host && !SCHEDULER_ORIGINS.some((origin) => origin.replace(/^https?:\/\//, "") === host));
const COURSES = "/api/v1/courses?per_page=100&include[]=term&include[]=total_students";
const IN_SAFARI = chrome.runtime.getURL("").indexOf("safari-web-extension:") === 0;
const KEEP_AWAKE_MS = 25 * 1000; // Safari stops a quiet helper after about 30 seconds
const SIGN_IN_WAIT_MS = 10 * 60 * 1000;
const LOOK_MS = 3 * 1000; // an open Canvas tab stuck (behind a "Leave site?" question, say) can't hold things up
const SLOW_MS = 20 * 1000; // but a Canvas that's only slow to answer isn't signed out

function fromScheduler(sender) {
  let origin = sender && sender.origin;
  if (!origin && sender && sender.url) {
    try { origin = new URL(sender.url).origin; } catch (err) { origin = ""; }
  }
  return !!origin && SCHEDULER_ORIGINS.indexOf(origin) >= 0;
}
function canvasOrigin(host) {
  if (CANVAS_HOSTS.indexOf(host) < 0) return null;
  return (/^(127\.0\.0\.1|localhost)(:\d+)?$/.test(host) ? "http://" : "https://") + host;
}
const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
// The Canvas sign-in tab a page is waiting on, by Canvas: a second press of
// the page's button brings it forward again ("show-signin").
const signInTabs = new Map();

// Safari asks the professor to allow each site; the page to do it in, opened
// (in front, over Safari's Settings window) when the helper is first turned
// on there and access is still missing, and whenever it's needed.
const ALL_SITES = (chrome.runtime.getManifest().host_permissions || [])
  .concat((chrome.runtime.getManifest().externally_connectable || {}).matches || []);
function openAllowPage() {
  return chrome.tabs.create({ url: chrome.runtime.getURL("allow.html"), active: true })
    .then((tab) => tab && focusWindow(tab.windowId)).catch(() => {});
}
chrome.runtime.onInstalled.addListener((details) => {
  if (!IN_SAFARI || details.reason !== "install") return;
  chrome.permissions.contains({ origins: ALL_SITES }).then((ok) => { if (!ok) openAllowPage(); }, openAllowPage);
});

// One request from the page: the courses, or one course's students. `say`
// carries word along the way (a sign-in), where the page can hear it.
async function handle(msg, say, schedulerTab) {
  const origin = canvasOrigin(String(msg.host || ""));
  if (!origin) return { type: "problem", why: "host" };
  try {
    if (msg.type === "courses") {
      const courses = await read(origin, [COURSES], say, schedulerTab);
      return { type: "courses", courses: courses[0].filter(teaches).map(course) };
    }
    if (msg.type === "students" && /^\d{1,15}$/.test(String(msg.courseId))) {
      const id = String(msg.courseId);
      const [info, users] = await read(origin, [
        "/api/v1/courses/" + id + "?include[]=term",
        "/api/v1/courses/" + id + "/users?enrollment_type[]=student&include[]=email&per_page=100",
      ], say, schedulerTab);
      return { type: "students", course: course(info), students: people(users) };
    }
    return { type: "problem", why: "canvas" };
  } catch (err) {
    return { type: "problem", why: err && err.message ? err.message : "canvas" };
  }
}

chrome.runtime.onMessageExternal.addListener((msg, sender, reply) => {
  if (!fromScheduler(sender) || !msg) return undefined;
  if (msg.type === "hello") {
    reply({ ok: true, v: VERSION, hosts: CANVAS_HOSTS });
    return undefined;
  }
  if (msg.type === "courses" || msg.type === "students") { // (Safari: no ports from pages)
    handle(msg, () => {}, sender.tab).then(reply);
    return true; // the answer comes later
  }
  if (msg.type === "front") { // the page's own tab, to the front
    toFront(sender.tab).then(() => reply({ ok: true }));
    return true;
  }
  if (msg.type === "show-signin") { // pressed again while Canvas's sign-in waits: that tab, in front again
    const id = signInTabs.get(canvasOrigin(String(msg.host || "")));
    if (!id) { reply({ ok: false }); return undefined; }
    chrome.tabs.get(id).then(toFront).then(() => reply({ ok: true }), () => reply({ ok: false }));
    return true;
  }
  return undefined;
});

chrome.runtime.onConnectExternal.addListener((port) => {
  if (!fromScheduler(port.sender)) { port.disconnect(); return; }
  let gone = false; // the page closed or reloaded: stop waiting on its behalf
  port.onDisconnect.addListener(() => { gone = true; });
  const say = (msg) => { try { port.postMessage(msg); } catch (err) { /* the page went away */ } };
  say.gone = () => gone;
  port.onMessage.addListener(async (msg) => {
    if (!msg || msg.type === "ping") return;
    say(await handle(msg, say, port.sender.tab));
  });
});

// The professor's teaching courses (teacher, TA, designer), with what tells
// them apart; and each student as name and email only.
function teaches(c) {
  return c && c.id && c.name && (!c.enrollments || c.enrollments.some((e) => /^(teacher|ta|designer)$/i.test(e.type || "")));
}
function course(c) {
  const term = c.term && c.term.name && !/default term/i.test(c.term.name) ? c.term : null;
  return {
    id: String(c.id), name: String(c.name).slice(0, 200), term: term ? String(term.name).slice(0, 80) : "",
    start: term ? term.start_at || "" : c.start_at || "", end: term ? term.end_at || "" : c.end_at || "",
    students: typeof c.total_students === "number" ? c.total_students : null,
    published: c.workflow_state !== "unpublished",
  };
}
function people(users) {
  const seen = {};
  const out = [];
  (Array.isArray(users) ? users : []).forEach((u) => {
    if (!u || seen[u.id]) return;
    seen[u.id] = true;
    const login = String(u.login_id || "");
    const email = String(u.email || (login.indexOf("@") > 0 ? login : ""));
    const name = String(u.name || u.sortable_name || "");
    if (name || email) out.push({ name: name.slice(0, 120), email: email.slice(0, 254) });
  });
  return out;
}

// Read these Canvas API paths (each with all its pages), one way or the
// other (see the top).
async function read(origin, paths, say, schedulerTab) {
  // Safari asks the professor to allow each site; until they have, the page
  // to allow it opens, and the Scheduler page says so.
  const allowed = await chrome.permissions.contains({ origins: [origin + "/*"] }).catch(() => true);
  if (!allowed) {
    if (IN_SAFARI) openAllowPage();
    throw new Error("access");
  }
  try {
    return await Promise.all(paths.map((path) => direct(origin, path)));
  } catch (err) {
    if (err.message === "forbidden" || err.message === "missing") throw err;
  }
  return inTab(origin, paths, say, schedulerTab);
}

async function direct(origin, path) {
  let url = origin + path;
  let all = [];
  for (let page = 0; url && page < 40; page++) {
    const r = await fetch(url, { credentials: "include", headers: { Accept: "application/json" } });
    if (r.status === 401) {
      const text = await r.text();
      throw new Error(/not authorized|unauthorized/i.test(text) ? "forbidden" : "signin");
    }
    if (r.status === 403) throw new Error("forbidden");
    if (r.status === 404) throw new Error("missing");
    if (!r.ok) throw new Error("canvas");
    const data = JSON.parse((await r.text()).replace(/^while\(1\);/, ""));
    if (!Array.isArray(data)) return data;
    all = all.concat(data);
    const next = (r.headers.get("Link") || "").match(/<([^>]+)>;\s*rel="next"/);
    url = next && next[1].indexOf(origin + "/") === 0 ? next[1] : null;
  }
  return all;
}

// Inside a Canvas tab: the same request, made by Canvas's own page. Only a
// tab the helper opened is closed after (never one of the professor's).
async function inTab(origin, paths, say, schedulerTab) {
  let tab = await openSignedIn(origin);
  let openedId = null;
  if (!tab) {
    tab = await chrome.tabs.create({ url: origin + "/", active: false });
    openedId = tab.id;
  }
  try {
    tab = await signedIn(tab, origin, say);
    const [result] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: readHere, args: [paths] });
    const answer = result && result.result;
    if (!answer || answer.error) throw new Error((answer && answer.error) || "canvas");
    return answer.data;
  } finally {
    if (openedId !== null) chrome.tabs.remove(openedId).catch(() => {});
    toFront(schedulerTab);
  }
}

// An open Canvas tab where the professor is signed in, if there is one
// (all looked at together, so a few seconds at most).
async function openSignedIn(origin) {
  const tabs = (await chrome.tabs.query({ url: origin + "/*" })).filter((t) => t.status === "complete");
  const said = await Promise.all(tabs.map((t) => whoIn(t.id, LOOK_MS)));
  return tabs[said.indexOf(200)] || null;
}

// What Canvas says, inside this tab, to "who am I?": 200 when signed in, or
// 0 if the tab can't be asked or doesn't answer within `ms`.
async function whoIn(tabId, ms) {
  let timer;
  const results = await Promise.race([
    chrome.scripting.executeScript({ target: { tabId }, func: whoAmI }).catch(() => null),
    new Promise((done) => { timer = setTimeout(done, ms, null); }),
  ]);
  clearTimeout(timer);
  return (results && results[0] && results[0].result) || 0;
}

// Give a window the focus (an iPhone or iPad has no windows to focus).
function focusWindow(windowId) {
  return chrome.windows && chrome.windows.update ? chrome.windows.update(windowId, { focused: true }).catch(() => {})
    : Promise.resolve();
}

// Bring a tab to the front, in a window that has the focus.
function toFront(tab) {
  if (!tab) return Promise.resolve();
  return Promise.all([chrome.tabs.update(tab.id, { active: true }).catch(() => {}), focusWindow(tab.windowId)]);
}

// Wait until the tab is a loaded Canvas page with the professor signed in;
// if it isn't, bring it forward for them to sign in. Signing in on another
// Canvas tab counts too (it's looked at every few seconds), and a page that
// went away stops the wait.
async function signedIn(tab, origin, say) {
  const until = Date.now() + SIGN_IN_WAIT_MS;
  let asked = false;
  let looked = 0;
  // A word to the page keeps Safari from stopping the helper, on a timer of
  // its own, so a slow look at Canvas (up to SLOW_MS) can't delay it.
  const awake = setInterval(() => say({ type: "wait" }), KEEP_AWAKE_MS);
  try {
    while (Date.now() < until) {
      if (say.gone && say.gone()) throw new Error("gone");
      const now = await chrome.tabs.get(tab.id).catch(() => null);
      if (!now) {
        const other = await openSignedIn(origin); // (signed in on their own tab, then closed this one)
        if (other) return other;
        throw new Error("closed");
      }
      if (now.status === "complete" && now.url && now.url.indexOf(origin + "/") === 0 && !/\/login\b/.test(now.url)) {
        if (await whoIn(now.id, SLOW_MS) === 200) return now;
      }
      if (asked && Date.now() - looked > 3000) {
        looked = Date.now();
        const other = await openSignedIn(origin);
        if (other && other.id !== now.id) return other;
      }
      if (!asked && now.status === "complete") {
        asked = true;
        signInTabs.set(origin, now.id);
        say({ type: "signin" });
        await chrome.tabs.update(now.id, { active: true });
        await focusWindow(now.windowId);
      }
      await sleep(800);
    }
    throw new Error("signin");
  } finally {
    clearInterval(awake);
    if (signInTabs.get(origin) === tab.id) signInTabs.delete(origin);
  }
}

// These two run inside the Canvas page (chrome.scripting), so they can use
// nothing from this file.
async function whoAmI() {
  try {
    const r = await fetch("/api/v1/users/self", { credentials: "same-origin", headers: { Accept: "application/json" } });
    return r.status;
  } catch (err) {
    return 0;
  }
}
async function readHere(paths) {
  async function all(url) {
    let out = [];
    for (let page = 0; url && page < 40; page++) {
      const r = await fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } });
      if (r.status === 401) {
        const text = await r.text();
        throw new Error(/not authorized|unauthorized/i.test(text) ? "forbidden" : "signin");
      }
      if (r.status === 403) throw new Error("forbidden");
      if (r.status === 404) throw new Error("missing");
      if (!r.ok) throw new Error("canvas");
      const data = JSON.parse((await r.text()).replace(/^while\(1\);/, ""));
      if (!Array.isArray(data)) return data;
      out = out.concat(data);
      const next = (r.headers.get("Link") || "").match(/<([^>]+)>;\s*rel="next"/);
      url = next && next[1].indexOf(location.origin + "/") === 0 ? next[1] : null;
    }
    return out;
  }
  try {
    return { data: await Promise.all(paths.map(all)) };
  } catch (err) {
    return { error: err.message || "canvas" };
  }
}
