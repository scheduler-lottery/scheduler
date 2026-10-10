// End to end, in a real (headless) Chrome with Scheduler Helper installed:
// one click on the class-list step brings the class list from the stand-in
// Canvas, signing in to Canvas on the way. Run the stand-in Canvas and this
// site, the site told the dev helper's id, then build the helper and run:
//
//     .venv/bin/python tools/fake_canvas.py
//     CANVAS_HELPER_IDS=lgfmbiibmekimpipoeodjmkeimdffnmm CANVAS_HELPER_STORE_URL=https://chromewebstore.google.com/ \
//         OWNER_EMAIL=owner@school.edu .venv/bin/python app.py
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
      n.nodeValue = n.nodeValue.replace("127.0.0.1:5077", "canvas.northwestern.edu").replace("not chosen yet", "Northwestern University");
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
  const sid = await evaluate(me.session, `(async () => {
    const csrf = document.querySelector('meta[name=csrf-token]').content;
    const day = (n) => new Date(Date.now() + n * 864e5).toISOString().slice(0, 10);
    const body = new URLSearchParams([["csrf_token", csrf], ["title", "Helper Seminar"], ["capacity", "30"],
      ["day_date", day(30)], ["day_key", ""], ["day_date", day(31)], ["day_key", ""]]);
    const r = await fetch("/teach/new", { method: "POST", body, redirect: "follow" });
    return (r.url.match(/\\/teach\\/s\\/([^/?#]+)/) || [])[1];
  })()`);
  check(!!sid, "made a sheet");

  // The class-list step, on Canvas (this school's Canvas is the stand-in).
  await go(me.session, `${SITE}/teach/s/${sid}/setup?step=students`);
  await evaluate(me.session, `sessionStorage.setItem("quiz-step-${sid}", "canvas"); localStorage.setItem("canvas-host", "127.0.0.1:5077")`);
  await go(me.session, `${SITE}/teach/s/${sid}/setup?step=students&again=1`);
  await until(me.session, `!!document.querySelector("[data-quiz]") && !!document.querySelector("[data-helper-go]")`);
  await evaluate(me.session, `document.querySelector("[data-quiz]").removeAttribute("data-canvas-host")`);
  check(await until(me.session, `document.querySelector("[data-helper-go]").offsetParent !== null`),
    "with the helper installed, the step offers one button: Get my class list from Canvas");
  check(await evaluate(me.session, `!document.querySelector("[data-canvas-download]").open && document.querySelectorAll(".canvas-guide details").length === 1`),
    "with only one other way, folded: upload Canvas's student file");
  await shot(me.session, "1-one-button.png", "[data-helper]");

  // One click. Not signed in to Canvas: a Canvas tab comes up to sign in.
  const before = created.length;
  await evaluate(me.session, `document.querySelector("[data-helper-go]").click()`);
  check(await until(me.session, `document.querySelector("[data-helper-said]").textContent.includes("Sign in to Canvas")`, 10000),
    "signed out of Canvas: the page says to sign in, in the tab that opened");
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
  const tabsBefore = (await send("Target.getTargets")).targetInfos.filter((t) => t.type === "page").length;
  await evaluate(me.session, `document.querySelector("[data-helper-go]").click()`);
  check(await until(me.session, `!document.querySelector("[data-canvas-arrived]").hidden`, 10000), "one click: the list comes straight back");
  const tabsAfter = (await send("Target.getTargets")).targetInfos.filter((t) => t.type === "page").length;
  check(tabsAfter === tabsBefore, "with no Canvas tab at all (read directly, signed in)");

  // Safari's way of asking (single messages, no ports): the same answers.
  const viaMessage = await evaluate(me.session, `new Promise((done) => chrome.runtime.sendMessage(${JSON.stringify(DEV_ID)},
    { type: "students", host: "127.0.0.1:5077", courseId: "202" }, (answer) => done(answer)))`);
  check(viaMessage && viaMessage.type === "students" && viaMessage.students.length === 8
    && !JSON.stringify(viaMessage).includes("login_id"), "asked by single message (as Safari does), the helper answers the same");
  const stranger = await newPage(`${CANVAS}/login`);
  const refused = await evaluate(stranger.session, `typeof chrome === "undefined" || !chrome.runtime || !chrome.runtime.sendMessage`);
  check(refused, "a page on any other site can't even reach the helper");
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
