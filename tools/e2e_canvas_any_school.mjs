// End to end, in a real (headless) Chrome: Scheduler Helper 1.1.0 at a
// school whose Canvas it hasn't been allowed to read (the stand-in Canvas on
// 5078, which the test copy doesn't list: any other school's). The page is a
// checklist (add it, import, delete it); "Import class list" opens the
// helper's own window to allow that Canvas (closed: nothing is read; allowed:
// the list comes, after a Canvas sign-in), the class picked goes straight
// in, and the last step deletes the helper in one press. Run:
//
//     FAKE_CANVAS_PORT=5078 .venv/bin/python tools/fake_canvas.py
//     CANVAS_DEV_HOST=127.0.0.1:5078 CANVAS_HELPER_IDS=lgfmbiibmekimpipoeodjmkeimdffnmm \
//         CANVAS_HELPER_STORE_URL=https://chromewebstore.google.com/ OWNER_EMAIL=owner@school.edu SHORT_LINKS=0 \
//         .venv/bin/python -m flask --app app run --port 5072
//     DEV_SITE_PORTS=5072 DEV_CANVAS_PORTS=5077,5078 .venv/bin/python tools/build_extension.py dev
//     node tools/e2e_canvas_any_school.mjs
//
// (A headless Chrome can't answer its own "Allow" box, so the test copy
// lists the stand-in, the test takes it away first, as a professor limiting
// its site access would, and gives it back when the allow window opens, as
// chrome://extensions does: what Chrome's yes amounts to.)
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SITE = process.env.SITE || "http://127.0.0.1:5072";
const CANVAS = process.env.CANVAS || "http://127.0.0.1:5078";
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const HELPER = join(dirname(dirname(fileURLToPath(import.meta.url))), "dist", "scheduler-helper-dev");
const DEV_ID = "lgfmbiibmekimpipoeodjmkeimdffnmm";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failures = 0;
const check = (ok, what) => { console.log((ok ? "PASS " : "FAIL ") + what); if (!ok) failures++; };

const profile = mkdtempSync(join(tmpdir(), "scheduler-helper-e2e-"));
const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-pipe", "--enable-unsafe-extension-debugging",
  `--user-data-dir=${profile}`, "--no-first-run", "--no-default-browser-check", "about:blank"],
  { stdio: ["ignore", "ignore", "ignore", "pipe", "pipe"] });

// The DevTools protocol over the pipe: JSON messages ending in a NUL byte.
let nextId = 1;
const waiting = new Map();
const created = [];
const requests = []; // what pages with Network on asked for
let helperSession = null; // the helper itself, once the test is attached to it
let refusedHere = 0; // the helper's own reads of Canvas, refused (to make it read in a tab)
let slowMs = 4000; // how slow Canvas is to say who's signed in, near the end (null: until let go)
const held = []; // those answers, held while slowMs is null
let buffer = "";
chrome.stdio[4].on("data", (chunk) => {
  buffer += chunk.toString("utf8");
  let end;
  while ((end = buffer.indexOf("\0")) >= 0) {
    const msg = JSON.parse(buffer.slice(0, end));
    buffer = buffer.slice(end + 1);
    if (msg.id && waiting.has(msg.id)) {
      const { ok, no } = waiting.get(msg.id);
      waiting.delete(msg.id);
      msg.error ? no(new Error(JSON.stringify(msg.error))) : ok(msg.result);
    } else if (msg.method === "Target.targetCreated") {
      created.push(msg.params.targetInfo);
    } else if (msg.method === "Network.requestWillBeSent") {
      requests.push({ session: msg.sessionId, url: msg.params.request.url });
    } else if (msg.method === "Network.loadingFailed" && msg.sessionId === helperSession && msg.params.blockedReason) {
      refusedHere++;
    } else if (msg.method === "Target.attachedToTarget" && msg.params.waitingForDebugger) {
      // A tab opened while Canvas is slow (near the end): slow there too.
      const session = msg.params.sessionId;
      (msg.params.targetInfo.type === "page" ? send("Fetch.enable", { patterns: [{ urlPattern: "*/api/v1/users/self*" }] }, session) : Promise.resolve())
        .then(() => send("Runtime.runIfWaitingForDebugger", {}, session)).catch(() => {});
    } else if (msg.method === "Fetch.requestPaused") {
      const go = () => send("Fetch.continueRequest", { requestId: msg.params.requestId }, msg.sessionId).catch(() => {});
      if (slowMs === null) held.push(go); else setTimeout(go, slowMs);
    }
  }
});
function send(method, params = {}, sessionId) {
  const id = nextId++;
  chrome.stdio[3].write(JSON.stringify({ id, method, params, sessionId }) + "\0");
  return new Promise((ok, no) => waiting.set(id, { ok, no }));
}
async function attach(targetId) {
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  await send("Runtime.enable", {}, sessionId);
  await send("Page.enable", {}, sessionId).catch(() => {});
  return sessionId;
}
async function evaluate(session, expression, ms = 60000) {
  // (A page that navigates while it's asked, as one does once a class list goes in, may never answer.)
  let timer;
  const out = await Promise.race([
    send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true, userGesture: true }, session),
    new Promise((_ok, no) => { timer = setTimeout(() => no(new Error("no answer from the page")), ms); }),
  ]).finally(() => clearTimeout(timer));
  if (out.exceptionDetails) throw new Error(out.exceptionDetails.text + " " + JSON.stringify(out.exceptionDetails.exception));
  return out.result.value;
}
async function until(session, expression, ms = 8000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    try { if (await evaluate(session, expression)) return true; } catch { /* page changing */ }
    await sleep(150);
  }
  return false;
}
async function go(session, url) {
  await send("Page.navigate", { url }, session);
  await until(session, `location.href.startsWith(${JSON.stringify(url.split("#")[0])}) && document.readyState === "complete"`);
}
async function newPage(url) {
  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const session = await attach(targetId);
  if (url) await go(session, url);
  return { targetId, session };
}
const text = (session) => evaluate(session, "document.body ? document.body.innerText : ''");
// SHOTS=dist/store: the Chrome Web Store's 1280x800 screenshots, taken on the way.
const SHOTS = process.env.SHOTS;
async function shot(session, name, selector) {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  await send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false }, session);
  // As a professor at Northwestern would see it (the stand-in's address aside).
  await evaluate(session, `(() => {
    const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let n = walk.nextNode(); n; n = walk.nextNode()) {
      n.nodeValue = n.nodeValue.replace("127.0.0.1:5077", "canvas.northwestern.edu").replace("not chosen yet", "Northwestern University")
        .replace("Stand-in Canvas", "Northwestern University");
    }
    document.querySelector(${JSON.stringify(selector)}).scrollIntoView({ block: "center" });
  })()`);
  await sleep(700);
  const { data } = await send("Page.captureScreenshot", { format: "png" }, session);
  writeFileSync(join(SHOTS, name), Buffer.from(data, "base64"));
  await send("Emulation.clearDeviceMetricsOverride", {}, session);
}
const press = (session, label) => evaluate(session, `(() => {
  const b = [...document.querySelectorAll("button, a")].find((x) => x.textContent.includes(${JSON.stringify(label)}) && x.offsetParent !== null);
  if (!b) return false; b.click(); return true; })()`);
const pages = async () => (await send("Target.getTargets")).targetInfos.filter((t) => t.type === "page");
// A Canvas tab created since `before` (Target.targetCreated), once it has an address.
async function newCanvasTab(before, ms = 5000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    const ids = new Set(created.slice(before).filter((t) => t.type === "page").map((t) => t.targetId));
    const tab = (await pages()).find((t) => ids.has(t.targetId) && t.url.startsWith(CANVAS));
    if (tab) return tab;
    await sleep(100);
  }
  return null;
}
// A new sheet, opened at its class-list step with "my class is in Canvas" chosen.
async function canvasStep(session, title) {
  const sid = await evaluate(session, `(async () => {
    const csrf = document.querySelector('meta[name=csrf-token]').content;
    const day = (n) => new Date(Date.now() + n * 864e5).toISOString().slice(0, 10);
    const body = new URLSearchParams([["csrf_token", csrf], ["title", ${JSON.stringify(title)}], ["capacity", "30"],
      ["day_date", day(30)], ["day_key", ""], ["day_date", day(31)], ["day_key", ""]]);
    const r = await fetch("/teach/new", { method: "POST", body, redirect: "follow" });
    return (r.url.match(/\\/teach\\/s\\/([^/?#]+)/) || [])[1];
  })()`);
  if (!sid) return null;
  await go(session, `${SITE}/teach/s/${sid}/setup?step=students`);
  await evaluate(session, `sessionStorage.setItem("quiz-step-${sid}", "canvas")`);
  await go(session, `${SITE}/teach/s/${sid}/setup?step=students&again=1`);
  await until(session, `!!document.querySelector("[data-quiz]") && !!document.querySelector("[data-helper-go]")`);
  return sid;
}

// The stand-in Canvas, taken from the helper or given back, as chrome://extensions does (its site access).
async function siteAccess(origin, on) {
  const ext = await newPage("chrome://extensions/?id=" + DEV_ID);
  await sleep(600);
  const ok = await evaluate(ext.session, `new Promise((done) => chrome.developerPrivate.${on ? "addHostPermission" : "removeHostPermission"}(
    ${JSON.stringify(DEV_ID)}, ${JSON.stringify(origin + "/*")}, () => done(!chrome.runtime.lastError)))`);
  await send("Target.closeTarget", { targetId: ext.targetId }).catch(() => {});
  return ok;
}
const allowWindow = async () => (await send("Target.getTargets")).targetInfos
  .find((t) => t.type === "page" && t.url.startsWith(`chrome-extension://${DEV_ID}/allow.html`));
const state = (session, step) => evaluate(session, `(() => {
  const li = document.querySelector('[data-check="${step}"]');
  return li ? (li.classList.contains("is-done") ? "done" : li.classList.contains("is-now") ? "now" : "later") : "none";
})()`);

try {
  await sleep(500);
  await send("Target.setDiscoverTargets", { discover: true });
  const loaded = await send("Extensions.loadUnpacked", { path: HELPER });
  check(loaded.id === DEV_ID, "Scheduler Helper 1.1.0 is installed (" + loaded.id + ")");
  check(await siteAccess(CANVAS, false), "and may not read this school's Canvas yet");

  const me = await newPage(`${SITE}/teach/login`);
  const email = `anyschool-${Date.now()}@school.edu`;
  await evaluate(me.session, `document.querySelector('input[name=email]').value = ${JSON.stringify(email)}; document.querySelector('input[name=email]').form.submit()`);
  await until(me.session, `location.pathname === "/teach/verify" && document.readyState === "complete"`);
  const code = await evaluate(me.session, `(document.body.innerText.match(/The code is\\s+(\\d{6})/) || [])[1]`);
  await evaluate(me.session, `document.querySelector('input[name=code]').value = "${code}"; document.querySelector('input[name=code]').form.submit()`);
  await until(me.session, `location.pathname.startsWith("/teach") && location.pathname !== "/teach/verify" && document.readyState === "complete"`);
  const sid = await canvasStep(me.session, "Evidence Seminar");
  check(!!sid, "made a sheet, at its Canvas step");
  check(await until(me.session, `document.querySelector("[data-helper-go]").offsetParent !== null`, 10000),
    "the helper answers: its button shows");
  check(await state(me.session, "add") === "done" && await state(me.session, "import") === "now"
    && await state(me.session, "done") === "later", "checklist: 1 done (it's in Chrome), 2 now, 3 still to come");
  check(await evaluate(me.session, `document.querySelector("[data-helper-go]").textContent.trim() === "Import class list"`),
    "step 2's button: Import class list");
  check(await evaluate(me.session, `document.querySelector(".nothing-kept summary").textContent.includes("Is any of this recorded?")`),
    "a word for the curious: is any of this recorded?");

  // Import: the helper's own small window asks to allow this school's Canvas. Closed, nothing is read.
  await evaluate(me.session, `document.querySelector("[data-helper-go]").click()`);
  check(await until(me.session, `document.querySelector("[data-helper-said]").textContent.includes("One time only")
    && document.querySelector("[data-helper-go]").textContent === "Show me the Allow window"`, 10000),
    "the page says: allow it once, in the window that opened");
  let win = null;
  for (let i = 0; i < 40 && !win; i++) { win = await allowWindow(); if (!win) await sleep(150); }
  check(!!win && win.url.includes("host=127.0.0.1%3A5078"), "the allow window names this school's Canvas");
  if (win) {
    const w = await attach(win.targetId);
    const said = await text(w);
    check(said.includes("127.0.0.1:5078") && said.includes("It keeps nothing") && said.includes("Allow"),
      "it says which Canvas, what it reads, and that it keeps nothing");
    await evaluate(w, `document.querySelector("[data-not-now]").click()`);
  }
  check(await until(me.session, `document.querySelector("[data-helper-said]").textContent.includes("Not allowed, so nothing was read")`, 10000),
    "Not now: nothing read, and the page says so");
  check(!(await allowWindow()), "the allow window is gone");

  // Again, and allowed: then Canvas's sign-in, then the courses, here.
  const before = created.length;
  await evaluate(me.session, `document.querySelector("[data-helper-go]").click()`);
  win = null;
  for (let i = 0; i < 40 && !win; i++) { win = await allowWindow(); if (!win) await sleep(150); }
  check(!!win, "asked again, the allow window again");
  check(await siteAccess(CANVAS, true), "allowed (Chrome's yes)");
  check(await until(me.session, `!(document.querySelector("[data-helper-said]").textContent.includes("One time only"))`, 10000),
    "the page moves on by itself");
  let canvasTab = await newCanvasTab(before, 10000);
  check(!!canvasTab, "a Canvas tab, for signing in");
  if (canvasTab) {
    const canvas = await attach(canvasTab.targetId);
    await until(canvas, `location.pathname === "/login" && !!document.querySelector("form")`, 10000);
    await evaluate(canvas, `document.querySelector("form").submit()`);
  }
  check(await until(me.session, `!document.querySelector("[data-helper-courses]").hidden`, 15000), "the courses, on the page");
  check(!(await allowWindow()), "the allow window closed itself");
  await press(me.session, "2026FA_BUSCOM_615_SEC1");
  // Picked: straight in. The page it lands on: 1 and 2 done, 3 now: All done! Delete it?
  check(await until(me.session, `document.readyState === "complete" && /Added 57 students/.test(document.body.innerText)`, 30000),
    "picked, and the 57 students go right in (no extra press)");
  check(await until(me.session, `!!document.querySelector("[data-helper-remove]") && !document.querySelector("[data-helper-remove]").hidden`, 10000),
    "then the checklist's last step");
  check(await evaluate(me.session, `document.querySelectorAll("[data-helper-remove] .helper-check.is-done").length === 2
    && document.querySelector('[data-helper-remove] [data-check="done"]').classList.contains("is-now")
    && document.querySelector("[data-helper-remove-go]").textContent.trim() === "Delete Scheduler Helper"`),
    "1 and 2 checked off; 3 now: All done! Delete Scheduler Helper?");
  const remembered = await evaluate(me.session, `localStorage.getItem("canvas-course:${sid}")`);
  check(/"id":"101"/.test(remembered || ""), "this browser remembers the sheet's Canvas course (" + remembered + ")");
  // Delete: one press.
  await evaluate(me.session, `document.querySelector("[data-helper-remove-go]").click()`);
  check(await until(me.session, `document.querySelector("[data-helper-remove-said]").textContent.includes("is deleted from Chrome")
    && document.querySelector('[data-helper-remove] [data-check="done"]').classList.contains("is-done")`, 10000),
    "one press, and it's deleted, and step 3 is checked off");
  const after = await evaluate(me.session, `new Promise((done) => {
    if (!window.chrome || !chrome.runtime || !chrome.runtime.sendMessage) { done(null); return; }
    chrome.runtime.sendMessage(${JSON.stringify(DEV_ID)}, { type: "hello" }, (a) => { void chrome.runtime.lastError; done(a || null); });
  })`);
  check(after === null, "and it no longer answers");
  // Back to the Canvas step without it: step 1 again.
  await go(me.session, `${SITE}/teach/s/${sid}`);
  await evaluate(me.session, `document.querySelector(".canvas-update") && (document.querySelector(".canvas-update").open = true)`);
  check(await until(me.session, `!!document.querySelector('[data-check="add"].is-now') && document.querySelector("[data-helper-add]").offsetParent !== null`, 10000),
    "without it, the checklist starts at step 1 again: Add Scheduler Helper");
  check(await until(me.session, `!document.querySelector("[data-course-said]").hidden && document.querySelector("[data-course-said]").textContent.includes("2026FA_BUSCOM_615_SEC1")`, 5000),
    "the class page says where the list came from, from this browser's memory");
} catch (err) {
  console.log("FAIL " + err.message);
  failures++;
} finally {
  try { await send("Browser.close"); } catch { /* fine */ }
  chrome.kill();
  await sleep(300);
  try { rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 300 }); } catch { /* (Chrome still closing: the OS clears it) */ }
  console.log(failures ? `${failures} failed` : "all passed");
  process.exit(failures ? 1 : 0);
}
