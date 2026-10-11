// End to end, in a real (headless) Chrome: adding Scheduler Helper from the
// class-list step. The professor presses "Add Scheduler Helper to Chrome";
// the store opens in front; once the helper is added there, the Scheduler
// page comes back to the front by itself and points at its one button,
// having read nothing from Canvas (nothing does until it's pressed). Then one
// press brings the class list. Same setup as e2e_canvas_helper.mjs (the dev
// helper built, the stand-in Canvas and this site running):
//
//     node tools/e2e_canvas_install.mjs
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SITE = process.env.SITE || "http://127.0.0.1:5050";
const CANVAS = process.env.CANVAS || "http://127.0.0.1:5077";
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const HELPER = join(dirname(dirname(fileURLToPath(import.meta.url))), "dist", "scheduler-helper-dev");
const DEV_ID = "lgfmbiibmekimpipoeodjmkeimdffnmm";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failures = 0;
const check = (ok, what) => { console.log((ok ? "PASS " : "FAIL ") + what); if (!ok) failures++; };

const profile = mkdtempSync(join(tmpdir(), "scheduler-install-e2e-"));
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
async function evaluate(session, expression) {
  const out = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true, userGesture: true }, session);
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

try {
  await sleep(500);
  await send("Target.setDiscoverTargets", { discover: true });
  // A signed-in instructor, at a new sheet's class-list step, with no helper yet.
  const me = await newPage(`${SITE}/teach/login`);
  const email = `install-${Date.now()}@school.edu`;
  await evaluate(me.session, `document.querySelector('input[name=email]').value = ${JSON.stringify(email)}; document.querySelector('input[name=email]').form.submit()`);
  await until(me.session, `location.pathname === "/teach/verify" && document.readyState === "complete"`);
  const code = await evaluate(me.session, `(document.body.innerText.match(/The code is\\s+(\\d{6})/) || [])[1]`);
  await evaluate(me.session, `document.querySelector('input[name=code]').value = "${code}"; document.querySelector('input[name=code]').form.submit()`);
  await until(me.session, `location.pathname.startsWith("/teach") && location.pathname !== "/teach/verify" && document.readyState === "complete"`);
  const sid = await canvasStep(me.session, "LAW 540 Evidence");
  check(!!sid && await until(me.session, `document.querySelector("[data-helper-add]").offsetParent !== null`, 10000),
    "no helper yet: Add Scheduler Helper to Chrome");
  // Pressed: the store opens in front of this page.
  const before = created.length;
  await evaluate(me.session, `document.querySelector("[data-helper-add]").click()`);
  let store = null;
  for (let i = 0; i < 50 && !store; i++) {
    store = created.slice(before).find((t) => t.type === "page");
    await sleep(100);
  }
  check(!!store, "the store opens");
  if (store) await send("Target.activateTarget", { targetId: store.targetId });
  check(await until(me.session, `document.visibilityState === "hidden"`, 5000), "in front of this page");
  // Added there (as "Add extension" does): this page comes back by itself, and points at the button.
  const tabs = (await pages()).length;
  const loaded = await send("Extensions.loadUnpacked", { path: HELPER });
  check(loaded.id === DEV_ID, "Scheduler Helper is added");
  check(await until(me.session, `document.visibilityState === "visible"`, 10000), "this page comes back to the front by itself");
  check(await until(me.session, `document.querySelector("[data-helper-said]").textContent.includes("Scheduler Helper is added. Now press Get my class list from Canvas")
    && document.activeElement === document.querySelector("[data-helper-go]")`, 5000),
    "saying it's added, with the one button ready to press");
  await sleep(1500);
  check((await pages()).length === tabs && !(await pages()).some((t) => t.url.startsWith(CANVAS)),
    "and nothing read from Canvas until it's pressed (no Canvas tab)");
  // One press: signed out of Canvas, so Canvas's sign-in; then this course's list, no question.
  const was = created.length;
  await evaluate(me.session, `document.querySelector("[data-helper-go]").click()`);
  const signIn = await newCanvasTab(was, 10000);
  check(!!signIn, "one press: Canvas's sign-in comes up");
  if (signIn) {
    const s = await attach(signIn.targetId);
    await until(s, `location.pathname === "/login" && !!document.querySelector("form")`, 10000);
    await evaluate(s, `document.querySelector("form").submit()`);
  }
  check(await until(me.session, `!document.querySelector("[data-canvas-arrived]").hidden`, 20000)
    && (await evaluate(me.session, `document.querySelector("[data-canvas-arrived]").innerText`)).includes("8 students from 2026FA_LAW_540_SEC20"),
    "and back here, LAW 540's list, ready to add: no course to pick");
  await evaluate(me.session, `document.querySelector("[data-canvas-arrived] form").requestSubmit()`);
  check(await until(me.session, `document.readyState === "complete" && /Added 8 students/.test(document.body.innerText)`, 10000),
    "Add: 8 students on the sheet");
} catch (err) {
  console.log("FAIL " + err.message);
  failures++;
} finally {
  try { await send("Browser.close"); } catch { /* fine */ }
  chrome.kill();
  await sleep(300);
  rmSync(profile, { recursive: true, force: true });
  console.log(failures ? `${failures} failed` : "all passed");
  process.exit(failures ? 1 : 0);
}
