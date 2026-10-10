// Scheduler Helper: brings a course's class list from the professor's own
// Canvas into Scheduler, when (and only when) Scheduler's page asks.
//
// Scheduler's page talks to it through externally_connectable (only the
// Scheduler site listed in the manifest can): over a port in Chrome and
// Edge, by single messages in Safari. It asks for the professor's
// courses, then one course's students; the helper reads them from Canvas's
// own API, signed in as the professor, exactly as Canvas's pages do, and
// sends back names and emails, nothing else. It never changes anything in
// Canvas, never sees a password, and keeps nothing.
//
// How it reads Canvas:
//   1. straight from here (Chrome sends the professor's Canvas session with
//      requests to a site the helper may read), or, if that doesn't work
//      (third-party cookies blocked, say),
//   2. from inside a Canvas tab: an open one, or one it opens in the
//      background and closes after. Not signed in? That tab comes to the
//      front for the professor to sign in, the usual way, and the helper
//      carries on once Canvas is back.

const VERSION = chrome.runtime.getManifest().version;
const CANVAS_HOSTS = (chrome.runtime.getManifest().host_permissions || [])
  .map((pattern) => (pattern.match(/^https?:\/\/([^/]+)\/\*$/) || [])[1])
  .filter(Boolean);
const SCHEDULER_ORIGINS = ((chrome.runtime.getManifest().externally_connectable || {}).matches || [])
  .map((pattern) => (pattern.match(/^(https?:\/\/[^/]+)\//) || [])[1])
  .filter(Boolean);
const COURSES = "/api/v1/courses?per_page=100&include[]=term&include[]=total_students";
const SIGN_IN_WAIT_MS = 10 * 60 * 1000;

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
  return undefined;
});

chrome.runtime.onConnectExternal.addListener((port) => {
  if (!fromScheduler(port.sender)) { port.disconnect(); return; }
  const say = (msg) => { try { port.postMessage(msg); } catch (err) { /* the page went away */ } };
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
  // Safari asks the professor to allow each site; until they have, say so.
  const allowed = await chrome.permissions.contains({ origins: [origin + "/*"] }).catch(() => true);
  if (!allowed) throw new Error("access");
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

// Inside a Canvas tab: the same request, made by Canvas's own page.
async function inTab(origin, paths, say, schedulerTab) {
  let tab = (await chrome.tabs.query({ url: origin + "/*" })).find((t) => t.status === "complete");
  let opened = false;
  if (!tab) {
    tab = await chrome.tabs.create({ url: origin + "/", active: false });
    opened = true;
  }
  tab = await signedIn(tab, origin, say);
  try {
    const [result] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: readHere, args: [paths] });
    const answer = result && result.result;
    if (!answer || answer.error) throw new Error((answer && answer.error) || "canvas");
    return answer.data;
  } finally {
    if (opened) chrome.tabs.remove(tab.id).catch(() => {});
    if (schedulerTab) {
      chrome.tabs.update(schedulerTab.id, { active: true }).catch(() => {});
      chrome.windows.update(schedulerTab.windowId, { focused: true }).catch(() => {});
    }
  }
}

// Wait until the tab is a loaded Canvas page with the professor signed in;
// if it isn't, bring it forward for them to sign in.
async function signedIn(tab, origin, say) {
  const until = Date.now() + SIGN_IN_WAIT_MS;
  let asked = false;
  while (Date.now() < until) {
    const now = await chrome.tabs.get(tab.id).catch(() => null);
    if (!now) throw new Error("closed");
    if (now.status === "complete" && now.url && now.url.indexOf(origin + "/") === 0 && !/\/login\b/.test(now.url)) {
      const [check] = await chrome.scripting.executeScript({ target: { tabId: now.id }, func: whoAmI }).catch(() => [null]);
      if (check && check.result === 200) return now;
    }
    if (!asked && now.status === "complete") {
      asked = true;
      say({ type: "signin" });
      await chrome.tabs.update(now.id, { active: true });
      await chrome.windows.update(now.windowId, { focused: true });
    }
    await sleep(800);
  }
  throw new Error("signin");
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
