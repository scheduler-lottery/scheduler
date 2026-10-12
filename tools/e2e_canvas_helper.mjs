// End to end, in a real (headless) Chrome with Scheduler Helper installed:
// one click on the class-list step brings the class list from the stand-in
// Canvas, signing in to Canvas on the way, even when a Canvas tab left open
// has been signed out; the page can ask the helper to bring it back to the
// front; and when the helper has to read inside a Canvas tab, a tab that's
// stuck or slow can't stop it, nor keep it from saying it's still there.
// Run the stand-in Canvas and this site, the site told the dev helper's id,
// then build the helper and run:
//
//     .venv/bin/python tools/fake_canvas.py
//     CANVAS_DEV_HOST=127.0.0.1:5077 CANVAS_HELPER_IDS=lgfmbiibmekimpipoeodjmkeimdffnmm \
//         CANVAS_HELPER_STORE_URL=https://chromewebstore.google.com/ OWNER_EMAIL=owner@school.edu .venv/bin/python app.py
//     .venv/bin/python tools/build_extension.py dev
//     node tools/e2e_canvas_helper.mjs
//
// Chrome loads the helper through its DevTools protocol (a pipe, with
// --enable-unsafe-extension-debugging), the way Chrome's own test tools do,
// since Chrome no longer takes --load-extension.
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
  const loaded = await send("Extensions.loadUnpacked", { path: HELPER });
  check(loaded.id === DEV_ID, "Scheduler Helper is installed (" + loaded.id + ")");

  // A signed-in instructor and a new sheet.
  const me = await newPage(`${SITE}/teach/login`);
  const email = `helper-${Date.now()}@school.edu`;
  await evaluate(me.session, `document.querySelector('input[name=email]').value = ${JSON.stringify(email)}; document.querySelector('input[name=email]').form.submit()`);
  await until(me.session, `location.pathname === "/teach/verify" && document.readyState === "complete"`);
  const code = await evaluate(me.session, `(document.body.innerText.match(/The code is\\s+(\\d{6})/) || [])[1]`);
  await evaluate(me.session, `document.querySelector('input[name=code]').value = "${code}"; document.querySelector('input[name=code]').form.submit()`);
  await until(me.session, `location.pathname.startsWith("/teach") && location.pathname !== "/teach/verify" && document.readyState === "complete"`);
  // The class-list step, on Canvas (this school's Canvas is the stand-in).
  const sid = await canvasStep(me.session, "Helper Seminar");
  check(!!sid, "made a sheet");
  check(await until(me.session, `document.querySelector("[data-helper-go]").offsetParent !== null`),
    "with the helper installed, the step offers one button: Get my class list from Canvas");
  check(await evaluate(me.session, `!document.querySelector("[data-canvas-download]").open
    && [...document.querySelectorAll(".canvas-guide details")].filter((d) => !d.closest("[hidden]") && !d.parentElement.closest("details")).length === 1`),
    "with only one other way, folded: upload Canvas's student file");
  await shot(me.session, "1-one-button.png", "[data-helper]");

  // One click. Not signed in to Canvas: a Canvas tab comes up to sign in.
  const before = created.length;
  await evaluate(me.session, `document.querySelector("[data-helper-go]").click()`);
  check(await until(me.session, `document.querySelector("[data-helper-said]").textContent.includes("Canvas wants you to sign in")
    && document.querySelector("[data-helper-go]").textContent === "Show me Canvas's sign-in page"`, 10000),
    "signed out of Canvas: the page says to sign in, in the tab that opened, and its button can show it again");
  let canvasTab = null;
  for (let i = 0; i < 50 && !canvasTab; i++) {
    canvasTab = created.slice(before).find((t) => t.type === "page" && (t.url || "").startsWith(CANVAS));
    if (!canvasTab) {
      const { targetInfos } = await send("Target.getTargets");
      canvasTab = targetInfos.find((t) => t.type === "page" && t.url.startsWith(CANVAS));
    }
    await sleep(100);
  }
  check(!!canvasTab, "a Canvas tab opened for signing in");
  const canvas = await attach(canvasTab.targetId);
  await until(canvas, `location.pathname === "/login" && !!document.querySelector("form")`, 10000);
  await evaluate(canvas, `document.querySelector("form").submit()`);
  check(await until(me.session, `!document.querySelector("[data-helper-courses]").hidden`, 15000),
    "after signing in, the professor's courses are on the Scheduler page, with nothing else to click in Canvas");
  await sleep(500);
  const { targetInfos } = await send("Target.getTargets");
  check(!targetInfos.some((t) => t.targetId === canvasTab.targetId), "the Canvas tab the helper opened closed itself");
  await shot(me.session, "2-pick-the-course.png", "[data-helper-courses]");
  const choices = await evaluate(me.session, `document.querySelector("[data-helper-courses]").textContent`);
  check(choices.includes("2026FA_BUSCOM_615_SEC1 · 2026 Fall · 57 students") && choices.includes("2026FA_LAW_200_TA")
    && !choices.includes("FACULTY_TRAINING"), "courses they teach or TA, with term and size");
  check(choices.indexOf("2026FA_BUSCOM_615_SEC1") < choices.indexOf("2027SP_LAW_610_LECTURE"), "this term's first");
  await press(me.session, "2026FA_BUSCOM_615_SEC1");
  check(await until(me.session, `!document.querySelector("[data-canvas-arrived]").hidden`, 10000), "one click on the course, and the list is here");
  await shot(me.session, "3-the-class-list.png", "[data-canvas-arrived]");
  check(await evaluate(me.session, `!document.querySelector("[data-canvas-download]").open`), "the upload stays folded");
  const arrived = await evaluate(me.session, `document.querySelector("[data-canvas-arrived]").innerText`);
  check(arrived.includes("57 students from 2026FA_BUSCOM_615_SEC1 (2026 Fall)") && arrived.includes("All with email addresses"),
    "57 students, with their emails");
  const sent = await evaluate(me.session, `document.querySelector("[data-canvas-arrived] input[name=list]").value`);
  check(!/login_id|sortable_name|"id"/.test(sent) && /"how"|helper/.test(await evaluate(me.session, `document.querySelector("[data-canvas-arrived] form").innerHTML`)),
    "only names and emails go to Scheduler");
  await evaluate(me.session, `document.querySelector("[data-canvas-arrived] form").requestSubmit()`);
  check(await until(me.session, `document.readyState === "complete" && /Added 57 students/.test(document.body.innerText)`, 10000),
    "Add puts them on the sheet");
  check(await until(me.session, `document.body.innerText.includes("Who's on your list (57)")`), "and shows who they are");

  // Next time (signed in now, and the sheet knows its course): one click, no picking.
  await go(me.session, `${SITE}/teach/s/${sid}`);
  await evaluate(me.session, `document.querySelector(".canvas-update").open = true`);
  check(await until(me.session, `document.querySelector("[data-helper-go]") && document.querySelector("[data-helper-go]").offsetParent !== null`),
    "Update from Canvas offers the same one button");
  const tabsBefore = (await send("Target.getTargets")).targetInfos.filter((t) => t.type === "page" && !t.url.startsWith("chrome-extension:")).length;
  await evaluate(me.session, `document.querySelector("[data-helper-go]").click()`);
  check(await until(me.session, `document.readyState === "complete" && document.body.innerText.includes("already matches 2026FA_BUSCOM_615_SEC1")`, 15000),
    "one click: Canvas's list read and compared, nothing changed, and it says so");
  const tabsAfter = (await send("Target.getTargets")).targetInfos.filter((t) => t.type === "page" && !t.url.startsWith("chrome-extension:")).length;
  check(tabsAfter === tabsBefore, "with no Canvas tab at all (read directly, signed in)");

  // Canvas is read only over a port (single messages are for "hello" and the like), and "hello" says what else it can do.
  const viaMessage = await evaluate(me.session, `new Promise((done) => chrome.runtime.sendMessage(${JSON.stringify(DEV_ID)},
    { type: "students", host: "127.0.0.1:5077", courseId: "202" }, (answer) => { void chrome.runtime.lastError; done(answer || null); }))`);
  const hello = await evaluate(me.session, `new Promise((done) => chrome.runtime.sendMessage(${JSON.stringify(DEV_ID)},
    { type: "hello" }, (answer) => done(answer || null)))`);
  check(viaMessage === null && hello && hello.ok && hello.can.includes("remove") && hello.can.includes("front"),
    "a single message reads nothing; hello says it can bring the page forward and remove itself");
  const stranger = await newPage(`${CANVAS}/login`);
  const refused = await evaluate(stranger.session, `typeof chrome === "undefined" || !chrome.runtime || !chrome.runtime.sendMessage`);
  check(refused, "a page on any other site can't even reach the helper");
  await send("Target.closeTarget", { targetId: stranger.targetId });

  // A sheet named for its course: one click, and that course's list is here, with no question first.
  check(!!(await canvasStep(me.session, "LAW 540 Evidence"))
    && await until(me.session, `document.querySelector("[data-helper-go]").offsetParent !== null`), "a sheet named LAW 540 Evidence");
  await evaluate(me.session, `document.querySelector("[data-helper-go]").click()`);
  check(await until(me.session, `!document.querySelector("[data-canvas-arrived]").hidden`, 10000)
    && (await evaluate(me.session, `document.querySelector("[data-canvas-arrived]").innerText`)).includes("8 students from 2026FA_LAW_540_SEC20")
    && await evaluate(me.session, `document.querySelector("[data-helper-courses]").hidden`),
    "one click: its course's list (LAW 540), no course question");
  await evaluate(me.session, `document.querySelector(".arrived-other").click()`);
  check(await until(me.session, `!document.querySelector("[data-helper-courses]").hidden`, 10000),
    "and \"Wrong course?\" asks which course after all");

  // The same number in another school's course ("LAW 615" isn't "BUSCOM_615"): asked, not guessed.
  check(!!(await canvasStep(me.session, "LAW 615 Privacy"))
    && await until(me.session, `document.querySelector("[data-helper-go]").offsetParent !== null`), "a sheet named LAW 615 Privacy");
  await evaluate(me.session, `document.querySelector("[data-helper-go]").click()`);
  check(await until(me.session, `!document.querySelector("[data-helper-courses]").hidden`, 10000)
    && await evaluate(me.session, `document.querySelector("[data-canvas-arrived]").hidden`),
    "one click: the list of courses, since only the business school's course has 615 in it");

  // Canvas left open on a course page all day, and its sign-in ran out: the
  // helper leaves that tab alone and opens a new one to sign in.
  const old = await newPage(`${CANVAS}/courses/101`);
  check(await evaluate(old.session, `location.pathname === "/courses/101"`), "Canvas is open on a course page, signed in");
  await evaluate(old.session, `window.untouched = true`); // gone if the tab were reloaded or sent elsewhere
  await send("Network.enable", {}, old.session);
  await send("Network.deleteCookies", { name: "canvas_session", url: CANVAS + "/" }, old.session);
  const left = await send("Network.getCookies", { urls: [CANVAS + "/"] }, old.session);
  check(!left.cookies.some((c) => c.name === "canvas_session"), "then Canvas's sign-in runs out (its cookie is gone)");
  check(!!(await canvasStep(me.session, "Stale Tab Seminar"))
    && await until(me.session, `document.querySelector("[data-helper-go]").offsetParent !== null`),
    "on a second sheet's class-list step, the same one button");
  await send("Target.activateTarget", { targetId: me.targetId });
  const before2 = created.length;
  await evaluate(me.session, `document.querySelector("[data-helper-go]").click()`);
  check(await until(me.session, `document.querySelector("[data-helper-said]").textContent.includes("Canvas wants you to sign in")`, 10000),
    "the page says to sign in to Canvas, in the tab that opened");
  const fresh = await newCanvasTab(before2);
  check(!!fresh && fresh.targetId !== old.targetId, "a new Canvas tab opened for signing in, not the old one");
  if (fresh) {
    const signIn = await attach(fresh.targetId);
    check(await until(signIn, `location.pathname === "/login" && !!document.querySelector("form")`, 10000),
      "the new tab shows Canvas's sign-in form");
    check(await until(signIn, `document.visibilityState === "visible"`, 5000), "and it is in front");
    await evaluate(signIn, `document.querySelector("form").submit()`);
    check(await until(me.session, `!document.querySelector("[data-helper-courses]").hidden`, 15000),
      "after signing in, the professor's courses are on the Scheduler page");
    await sleep(500);
    check(!(await pages()).some((t) => t.targetId === fresh.targetId), "the new Canvas tab closed itself");
  }
  check(await evaluate(old.session, `location.pathname === "/courses/101" && window.untouched === true`),
    "the professor's own Canvas tab is still on its course page, never reloaded");

  // Signed out again, with the professor's own Canvas tab on its sign-in page.
  // Pressing the button again while Canvas's sign-in waits brings that page
  // back in front; and signing in on their OWN tab (not the helper's) still
  // brings the list, and closes only the helper's tab.
  await send("Network.deleteCookies", { name: "canvas_session", url: CANVAS + "/" }, old.session);
  const own = await newPage(`${CANVAS}/login`); // (their own tab, not the one left on a course page)
  check(!!(await canvasStep(me.session, "Own Tab Seminar"))
    && await until(me.session, `document.querySelector("[data-helper-go]").offsetParent !== null`), "a third sheet, signed out of Canvas");
  await send("Target.activateTarget", { targetId: me.targetId });
  const before3 = created.length;
  await evaluate(me.session, `document.querySelector("[data-helper-go]").click()`);
  await until(me.session, `document.querySelector("[data-helper-said]").textContent.includes("sign-in page just came up")`, 10000);
  const helperTab = await newCanvasTab(before3);
  const signInSession = helperTab && await attach(helperTab.targetId);
  await send("Target.activateTarget", { targetId: me.targetId }); // back to Scheduler, without signing in
  await until(me.session, `document.visibilityState === "visible"`, 5000);
  await evaluate(me.session, `document.querySelector("[data-helper-go]").click()`);
  check(await until(me.session, `document.querySelector("[data-helper-said]").textContent.includes("in front again")`, 5000)
    && !!signInSession && await until(signInSession, `document.visibilityState === "visible"`, 5000),
    "pressed again while Canvas's sign-in waits: its sign-in page comes back in front, and the page says so");
  await send("Target.activateTarget", { targetId: own.targetId });
  await evaluate(own.session, `document.querySelector("form").submit()`); // signs in on their own tab
  check(await until(me.session, `!document.querySelector("[data-helper-courses]").hidden`, 15000),
    "signed in on their own Canvas tab instead: the courses still come");
  await sleep(500);
  check(!!helperTab && !(await pages()).some((t) => t.targetId === helperTab.targetId)
    && (await pages()).some((t) => t.targetId === own.targetId), "the helper's sign-in tab closed; the professor's own stayed");
  await send("Target.closeTarget", { targetId: own.targetId });

  // The page asks the helper to bring it to the front, from behind another tab.
  await send("Target.activateTarget", { targetId: old.targetId });
  check(await until(me.session, `document.visibilityState === "hidden"`, 5000), "with another tab in front, the Scheduler page is hidden");
  const fronted = await evaluate(me.session, `new Promise((done) => chrome.runtime.sendMessage(${JSON.stringify(DEV_ID)},
    { type: "front" }, (answer) => done(answer || String(chrome.runtime.lastError && chrome.runtime.lastError.message))))`);
  check(fronted && fronted.ok === true, "asked to, the helper answers { ok: true }");
  check(await until(me.session, `document.visibilityState === "visible"`, 5000)
    && await until(old.session, `document.visibilityState === "hidden"`, 5000), "and the Scheduler page is in front again");

  // Signed in to Canvas, but the helper can't read it from where it is (as
  // when the browser blocks that): it reads inside the professor's open
  // Canvas tab, opening nothing, and leaves that tab where it was.
  await evaluate(me.session, `new Promise((done) => chrome.runtime.sendMessage(${JSON.stringify(DEV_ID)}, { type: "hello" }, done))`);
  const worker = (await send("Target.getTargets")).targetInfos
    .find((t) => t.type === "service_worker" && t.url.startsWith(`chrome-extension://${DEV_ID}/`));
  helperSession = (await send("Target.attachToTarget", { targetId: worker.targetId, flatten: true })).sessionId;
  await send("Network.enable", {}, helperSession);
  await send("Network.setBlockedURLs", { urls: [CANVAS + "/api/*"] }, helperSession);
  const courses = (ms) => evaluate(me.session, `new Promise((done) => { // (over a port, as the page asks)
    const port = chrome.runtime.connect(${JSON.stringify(DEV_ID)});
    const late = setTimeout(() => { done({ type: "no answer" }); port.disconnect(); }, ${ms});
    port.onMessage.addListener((m) => {
      if (m.type === "signin" || m.type === "wait") return;
      clearTimeout(late);
      done(m);
      port.disconnect();
    });
    port.postMessage({ type: "courses", host: "127.0.0.1:5077" });
  })`);
  const opened = (since) => created.slice(since).filter((t) => t.type === "page");
  let since = created.length, turnedAway = refusedHere, asked = requests.length;
  let answer = await courses(10000);
  check(refusedHere > turnedAway && answer.type === "courses" && answer.courses.length > 0
    && requests.slice(asked).some((r) => r.session === old.session && r.url.includes("/api/v1/courses")),
    "the helper can't read Canvas itself: it reads in the professor's signed-in Canvas tab");
  check(opened(since).length === 0 && await evaluate(old.session, `location.pathname === "/courses/101" && window.untouched === true`),
    "with no new tab, and the professor's tab left as it was");

  // That tab stuck behind a question (“Leave site?”, say): the helper doesn't
  // wait on it, and reads in a tab of its own for a moment instead.
  await evaluate(old.session, `setTimeout(() => alert("Changes you made may not be saved."), 0); true`);
  await sleep(300);
  since = created.length; turnedAway = refusedHere;
  let start = Date.now();
  answer = await courses(20000);
  check(refusedHere > turnedAway && answer.type === "courses" && answer.courses.length > 0 && Date.now() - start < 10000,
    "with that Canvas tab stuck behind a question, the courses still come, in seconds");
  await sleep(500);
  check(opened(since).length === 1 && !(await pages()).some((t) => t.targetId === opened(since)[0].targetId),
    "through a Canvas tab the helper opened and closed again");
  await send("Page.handleJavaScriptDialog", { accept: true }, old.session);
  check(await evaluate(old.session, `location.pathname === "/courses/101" && window.untouched === true`),
    "and the professor's tab, once the question is answered, is just as it was");

  // Canvas slow (slowMs) to say who's signed in, in every tab: slow isn't
  // signed out, so no sign-in comes up, and the courses still come. (Last:
  // from here on, every new tab waits for the test.)
  await send("Fetch.enable", { patterns: [{ urlPattern: "*/api/v1/users/self*" }] }, old.session);
  await send("Target.setAutoAttach", { autoAttach: true, waitForDebuggerOnStart: true, flatten: true });
  since = created.length; turnedAway = refusedHere;
  const heard = await evaluate(me.session, `new Promise((done) => {
    const heard = [];
    const port = chrome.runtime.connect(${JSON.stringify(DEV_ID)});
    port.onMessage.addListener((m) => { heard.push(m.type); if (m.type !== "signin" && m.type !== "wait") done(heard); });
    port.postMessage({ type: "courses", host: "127.0.0.1:5077" });
    setTimeout(() => done(heard.concat("no answer")), 25000);
  })`);
  check(refusedHere > turnedAway && heard.join(" ") === "courses",
    "Canvas slow to say who's signed in: no sign-in is asked for, and the courses come (heard: " + heard.join(" ") + ")");
  await sleep(500);
  check(opened(since).length === 1 && !(await pages()).some((t) => t.targetId === opened(since)[0].targetId),
    "and the tab the helper opened for it closed again");

  // Canvas so slow to say who's signed in that each look gives up (after 20
  // seconds) and a sign-in is asked for: the helper still sends the page a
  // word every 25 seconds (so the page knows it's still waiting), and
  // carries on once Canvas answers.
  slowMs = null;
  since = created.length;
  const posting = evaluate(me.session, `new Promise((done) => {
    const start = Date.now();
    window.posted = [];
    const port = chrome.runtime.connect(${JSON.stringify(DEV_ID)});
    port.onMessage.addListener((m) => { posted.push([m.type, Date.now() - start]); if (m.type !== "signin" && m.type !== "wait") done(posted); });
    port.postMessage({ type: "courses", host: "127.0.0.1:5077" });
    setTimeout(() => done(posted.concat([["no answer", Date.now() - start]])), 70000);
  })`);
  await until(me.session, `window.posted.some((p) => p[0] === "wait")`, 40000);
  slowMs = 0;
  held.splice(0).forEach((go) => go());
  const posted = await posting;
  const at = (type) => (posted.find((p) => p[0] === type) || [])[1];
  check(at("signin") >= 20000 && at("wait") < 35000 && posted[posted.length - 1][0] === "courses",
    "Canvas very slow: the page still hears from the helper within 35 seconds, then gets the courses (heard: "
    + posted.map((p) => p[0] + " at " + Math.round(p[1] / 1000) + "s").join(", ") + ")");
  await sleep(500);
  check(opened(since).length === 1 && !(await pages()).some((t) => t.targetId === opened(since)[0].targetId),
    "and that tab closed again too");
  // The helper removed while the page is open: the page says so, and offers
  // to add it again (it doesn't blame Canvas).
  slowMs = 0;
  held.splice(0).forEach((go) => go());
  check(!!(await canvasStep(me.session, "Removed Helper Seminar"))
    && await until(me.session, `document.querySelector("[data-helper-go]").offsetParent !== null`), "a sheet, with the helper ready");
  await send("Extensions.uninstall", { id: DEV_ID });
  await evaluate(me.session, `document.querySelector("[data-helper-go]").click()`);
  check(await until(me.session, `document.querySelector("[data-helper-said]").textContent.includes("is no longer in Chrome")
    && document.querySelector("[data-helper-add]").offsetParent !== null`, 15000),
    "the helper removed: the page says so, and offers to add it again");

  // Added again: a list it brings, added, and then the offer to remove it
  // from Chrome, which does just that, in one press.
  await send("Extensions.loadUnpacked", { path: HELPER });
  check(await until(me.session, `!document.querySelector("[data-canvas-arrived]").hidden
    || !document.querySelector("[data-helper-courses]").hidden
    || /Getting|sign in/.test(document.querySelector("[data-helper-said]").textContent)`, 15000),
    "added again: the page notices by itself, and carries on getting the list (said: "
    + await evaluate(me.session, `document.querySelector("[data-helper-said]").textContent`) + ")");
  check(!!(await canvasStep(me.session, "LAW 540 Remove Seminar"))
    && await until(me.session, `document.querySelector("[data-helper-go]").offsetParent !== null`, 15000), "a new sheet's one button");
  await evaluate(me.session, `document.querySelector("[data-helper-go]").click()`);
  check(await until(me.session, `!document.querySelector("[data-canvas-arrived]").hidden`, 15000), "its course's list, here");
  await evaluate(me.session, `document.querySelector("[data-canvas-arrived] button[type=submit]").click()`);
  check(await until(me.session, `document.readyState === "complete" && !!document.querySelector("[data-helper-remove]")
    && !document.querySelector("[data-helper-remove]").hidden`, 15000),
    "added: the report offers to remove Scheduler Helper from Chrome");
  await evaluate(me.session, `document.querySelector("[data-helper-remove-go]").click()`);
  check(await until(me.session, `document.querySelector("[data-helper-remove-said]").textContent.includes("is removed from Chrome")`, 10000),
    "one press, and it's removed (" + await evaluate(me.session, `document.querySelector("[data-helper-remove-said]").textContent`) + ")");
  const after = await evaluate(me.session, `new Promise((done) => {
    if (!window.chrome || !chrome.runtime || !chrome.runtime.sendMessage) { done(null); return; }
    chrome.runtime.sendMessage(${JSON.stringify(DEV_ID)}, { type: "hello" }, (a) => { void chrome.runtime.lastError; done(a || null); });
  })`);
  check(after === null, "and it no longer answers");
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
