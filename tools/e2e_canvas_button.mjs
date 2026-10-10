// End to end, in a real (headless) Chrome: the "Send to Scheduler" button
// and copy-and-paste, against the stand-in Canvas (tools/fake_canvas.py on
// :5077, run with no FAKE_CANVAS_* settings) and this site running locally
// (app.py on :5050, dev mode, so sign-in codes show on screen). Run both,
// then:
//
//     node tools/e2e_canvas_button.mjs
//
// 1. The button, clicked on the setup page, says it works and opens Canvas
//    in a tab; there (after signing in, on the Dashboard) it's clicked again
//    and a course picked; the list shows up on the setup page, and Add puts
//    it on the sheet, which remembers the course.
// 2. Later, "Update from Canvas": the button opens Canvas right on that
//    course, and the list comes back for a look at what changes.
// 3. A Canvas tab of its own (no Scheduler page behind it): the button takes
//    that tab to Scheduler, which asks which sheet it's for.
// 4. A list some other page posts, without the instructor's key: flagged,
//    and can be thrown away.
// 5. Copy and paste, 100 at a time: a 230-student course and a course of
//    exactly 100.
// The button is run the way a bookmark runs: its javascript: address,
// decoded, evaluated in the page.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const SITE = process.env.SITE || "http://127.0.0.1:5050";
const CANVAS = process.env.CANVAS || "http://127.0.0.1:5077";
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9333;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failures = 0;
const check = (ok, what) => { console.log((ok ? "PASS " : "FAIL ") + what); if (!ok) failures++; };

const profile = mkdtempSync(join(tmpdir(), "scheduler-e2e-"));
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
  "--no-first-run", "--no-default-browser-check", "about:blank"], { stdio: "ignore" });

let ws, nextId = 1;
const waiting = new Map();
const created = [];
async function connect() {
  for (let i = 0; i < 50; i++) {
    try {
      const info = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json();
      ws = new WebSocket(info.webSocketDebuggerUrl);
      await new Promise((ok, no) => { ws.onopen = ok; ws.onerror = no; });
      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.id && waiting.has(msg.id)) {
          const { ok, no } = waiting.get(msg.id);
          waiting.delete(msg.id);
          msg.error ? no(new Error(JSON.stringify(msg.error))) : ok(msg.result);
        } else if (msg.method === "Target.targetCreated") {
          created.push(msg.params.targetInfo);
        }
      };
      return;
    } catch { await sleep(200); }
  }
  throw new Error("Chrome didn't start");
}
function send(method, params = {}, sessionId) {
  const id = nextId++;
  ws.send(JSON.stringify({ id, method, params, sessionId }));
  return new Promise((ok, no) => waiting.set(id, { ok, no }));
}
async function attach(targetId) {
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  await send("Runtime.enable", {}, sessionId);
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
  await send("Page.enable", {}, session);
  if (url) await go(session, url);
  return { targetId, session };
}
const text = (session) => evaluate(session, "document.body.innerText");
const press = (session, label) => evaluate(session, `(() => {
  const b = [...document.querySelectorAll("button")].find((x) => x.textContent.includes(${JSON.stringify(label)}));
  if (!b) return false; b.click(); return true; })()`);

try {
  await connect();
  await send("Target.setDiscoverTargets", { discover: true });

  // A signed-in instructor (dev mode shows the code on the page).
  const me = await newPage(`${SITE}/teach/login`);
  const email = `e2e-${Date.now()}@school.edu`;
  await evaluate(me.session, `document.querySelector('input[name=email]').value = ${JSON.stringify(email)}; document.querySelector('input[name=email]').form.submit()`);
  await until(me.session, `location.pathname === "/teach/verify" && document.readyState === "complete"`);
  const code = await evaluate(me.session, `(document.body.innerText.match(/The code is\\s+(\\d{6})/) || [])[1]`);
  await evaluate(me.session, `document.querySelector('input[name=code]').value = "${code}"; document.querySelector('input[name=code]').form.submit()`);
  await until(me.session, `location.pathname.startsWith("/teach") && location.pathname !== "/teach/verify" && document.readyState === "complete"`);
  check(await evaluate(me.session, `location.pathname`) !== "/teach/login", "signed in as an instructor");

  const newSheet = (title) => evaluate(me.session, `(async () => {
    const csrf = document.querySelector('meta[name=csrf-token]').content;
    const day = (n) => new Date(Date.now() + n * 864e5).toISOString().slice(0, 10);
    const body = new URLSearchParams([["csrf_token", csrf], ["title", ${JSON.stringify(title)}], ["capacity", "30"],
      ["day_date", day(30)], ["day_key", ""], ["day_date", day(31)], ["day_key", ""]]);
    const r = await fetch("/teach/new", { method: "POST", body, redirect: "follow" });
    return (r.url.match(/\\/teach\\/s\\/([^/?#]+)/) || [])[1];
  })()`);
  // Canvas's address comes from the school picked; here, the stand-in's.
  const toFakeCanvas = `(() => { const open = window.open.bind(window);
    window.open = (url, name, features) => open(String(url).replace(/^https:\\/\\/[^/]+/, ${JSON.stringify(CANVAS)}), name, features); })()`;
  const opened = async (before, ms = 6000) => {
    const end = Date.now() + ms;
    while (Date.now() < end) {
      const t = created.slice(before).find((x) => x.type === "page" && x.openerId);
      if (t) return t;
      await sleep(100);
    }
    return null;
  };

  const sid = await newSheet("E2E Seminar");
  check(!!sid, "made a sheet (" + sid + ")");

  // ---- 1. The button: checked here, then clicked in Canvas.
  await go(me.session, `${SITE}/teach/s/${sid}/setup?step=students`);
  await evaluate(me.session, `sessionStorage.setItem("quiz-step-${sid}", "canvas"); location.reload()`);
  await until(me.session, `!!document.querySelector("[data-bookmarklet]") && document.readyState === "complete"`);
  const button = await evaluate(me.session, `document.querySelector("[data-bookmarklet]").getAttribute("href")`);
  check(button.startsWith("javascript:") && decodeURIComponent(button).includes(SITE), "the button is made for this site");
  const code1 = decodeURIComponent(button.slice("javascript:".length));
  check(await evaluate(me.session, `!document.querySelector("[data-quiz-drop]") || document.querySelector("[data-quiz-drop]").hidden`),
    "no box for a downloaded file until the download steps are opened");

  await evaluate(me.session, toFakeCanvas);
  let before = created.length;
  await evaluate(me.session, code1); // the bookmark, clicked on this page
  check(await until(me.session, `document.querySelector("[data-bookmarklet-said]").innerText.includes("It works")`),
    "clicked here, the button says it works");
  const tab = await opened(before);
  check(!!tab, "and Canvas opens in a tab, with this page as its opener");
  const canvas = await attach(tab.targetId);
  await send("Page.enable", {}, canvas);
  await until(canvas, `location.href.startsWith(${JSON.stringify(CANVAS)}) && !!document.querySelector("form")`, 15000);
  await evaluate(canvas, `document.querySelector("form").submit()`);
  await until(canvas, `location.pathname === "/" && document.readyState === "complete"`);
  check((await text(canvas)).includes("Dashboard"), "signing in to Canvas lands on the Dashboard");
  check(await evaluate(me.session, `!!document.querySelector("[data-button-known]:not([hidden])")`),
    "the page now just says to click the button");

  await evaluate(canvas, code1); // the bookmark, clicked in Canvas
  await until(canvas, `document.body.innerText.includes("Which course's students")`);
  const picker = await evaluate(canvas, `document.querySelector('[aria-label="Send to Scheduler"]').innerText`);
  check(picker.includes("2026FA_LAW_200_TA") && !picker.includes("FACULTY_TRAINING"),
    "the course list has courses they teach or TA, not ones they take");
  check(picker.indexOf("2026FA_BUSCOM_615_SEC1") < picker.indexOf("2027SP_LAW_610_LECTURE"), "this term's courses first");
  check(/2026FA_BUSCOM_615_SEC1 · 2026 Fall · 57 students/.test(picker), "each with its term and size");
  check(await press(canvas, "2026FA_BUSCOM_615_SEC1"), "one is picked");
  check(await until(me.session, `!document.querySelector("[data-canvas-arrived]").hidden`, 10000), "the class list shows up on the setup page");
  const arrived = await evaluate(me.session, `document.querySelector("[data-canvas-arrived]").innerText`);
  check(arrived.includes("57 students from 2026FA_BUSCOM_615_SEC1") && arrived.includes("All with email addresses"),
    "57 students, from that course, all with emails");
  await sleep(2200);
  check(!(await send("Target.getTargets")).targetInfos.some((t) => t.targetId === tab.targetId), "the Canvas tab closed by itself");
  await evaluate(me.session, `document.querySelector("[data-canvas-arrived] form").requestSubmit()`);
  check(await until(me.session, `document.readyState === "complete" && /Added 57 students/.test(document.body.innerText)`),
    "Add put 57 students on the sheet");

  // ---- 2. Update from Canvas: straight to that course.
  await go(me.session, `${SITE}/teach/s/${sid}`);
  check(await evaluate(me.session, `document.querySelector("[data-canvas-guide]").getAttribute("data-canvas-course-id")`) === "101",
    "the sheet remembers its Canvas course");
  await evaluate(me.session, toFakeCanvas);
  before = created.length;
  await evaluate(me.session, code1);
  const tab2 = await opened(before);
  check(!!tab2, "the button, clicked here, opens Canvas again");
  const canvas2 = await attach(tab2.targetId);
  await send("Page.enable", {}, canvas2);
  check(await until(canvas2, `location.pathname === "/courses/101" && document.readyState === "complete"`, 10000), "right on that course");
  await evaluate(canvas2, code1);
  check(await until(me.session, `!document.querySelector("[data-canvas-arrived]").hidden`, 10000), "no picking: the list comes right back");
  check(await evaluate(me.session, `document.querySelector("[data-canvas-arrived]").closest("details").open`), "shown, unfolded");
  await evaluate(me.session, `document.querySelector("[data-canvas-arrived] form").requestSubmit()`);
  await until(me.session, `document.readyState === "complete" && location.pathname.includes("/roster/review/")`);
  check((await text(me.session)).includes("Check the new class list"), "and a sheet with a list shows what changes first");

  // ---- 3. A Canvas tab of its own: the button takes the tab to Scheduler.
  const own = await newPage(`${CANVAS}/courses/202`);
  await evaluate(own.session, code1);
  check(await until(own.session, `location.pathname.startsWith("/teach/canvas-import/") && document.readyState === "complete"`, 10000),
    "with no Scheduler page behind it, the button takes the tab to Scheduler");
  const page3 = await text(own.session);
  check(page3.includes("8 students from 2026FA_LAW_540_SEC20") && page3.includes("From Canvas · 127.0.0.1:5077"),
    "which shows the list, from where it really came");
  check(!page3.includes("Did you just click"), "with no warning: the instructor's own button sent it");
  check(await evaluate(own.session, `!document.querySelector(".arrived-list b") && document.querySelector(".arrived-list").textContent.includes("<b>and</b>")`),
    "a name that reads like markup stays plain text");
  check(await evaluate(own.session, `![...document.querySelectorAll("input[name=sheet]")].some((r) => r.checked)`)
    && page3.includes("its list is from 2026FA_BUSCOM_615_SEC1"),
    "the sheet last open is tied to another Canvas course, so nothing is picked for them, and it says so");

  // ---- 4. A list another page sends (no key): flagged, and thrown away.
  const forged = await newPage(`${CANVAS}/courses/202`);
  await evaluate(forged.session, `(() => { const f = document.createElement("form"); f.method = "post";
    f.action = ${JSON.stringify(SITE + "/canvas-import")};
    const i = document.createElement("input"); i.name = "list";
    i.value = JSON.stringify({ type: "scheduler-class-list", course: "LAW 501", students: [{ name: "Jane", email: "attacker@evil.example" }] });
    f.appendChild(i); document.body.appendChild(f); f.submit(); })()`);
  await until(forged.session, `location.pathname.startsWith("/teach/canvas-import/") && document.readyState === "complete"`, 10000);
  const page4 = await text(forged.session);
  check(page4.includes("Did you just click Send to Scheduler") && page4.includes("Sent from 127.0.0.1:5077"), "a list without the key is flagged");
  check(await evaluate(forged.session, `![...document.querySelectorAll("input[name=sheet]")].some((r) => r.checked)`), "with no sheet picked");
  await press(forged.session, "Don't add them");
  await until(forged.session, `!location.pathname.startsWith("/teach/canvas-import/") && document.readyState === "complete"`);
  check((await text(forged.session)).includes("Nothing was added"), "and thrown away");

  // ---- 5. Copy and paste, 100 at a time.
  const canvasTab = await newPage(`${CANVAS}/`);
  const grab = (path) => evaluate(canvasTab.session, `fetch(${JSON.stringify(path)}).then((r) => r.text())`);
  const paste = (value) => evaluate(me.session, `(() => { const box = document.querySelector("[data-copy-paste]");
    const dt = new DataTransfer(); dt.setData("text/plain", ${JSON.stringify(value)});
    box.dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
    return box.value; })()`);
  const said = () => evaluate(me.session, `document.querySelector("[data-copy-said]").textContent`);
  const stubWindows = `(() => { window.__opened = []; window.open = () => { const w = { closed: false, focus() {}, close() { w.closed = true; } };
    w.location = { set href(u) { window.__opened.push(u); } }; return w; }; })()`;
  const copyFlow = async (title) => {
    const id = await newSheet(title);
    await go(me.session, `${SITE}/teach/s/${id}/setup?step=students`);
    await evaluate(me.session, `sessionStorage.setItem("quiz-step-${id}", "canvas"); location.reload()`);
    await until(me.session, `!!document.querySelector("[data-copy-paste]") && document.readyState === "complete"`);
    await evaluate(me.session, stubWindows);
    await evaluate(me.session, `document.querySelector("[data-canvas-copy]").open = true`); // as the professor would
    await press(me.session, "Show my Canvas courses");
    return id;
  };
  const sid5 = await copyFlow("E2E Copy");
  check(await evaluate(me.session, `window.__opened.some((u) => u.includes("/api/v1/courses?"))`), "the window beside shows Canvas's courses");
  const courses = await grab("/api/v1/courses?per_page=100&include[]=term&include[]=total_students");
  check(await paste(courses) === "", "pasting them leaves nothing in the box");
  check(await until(me.session, `!document.querySelector("[data-copy-courses]").hidden`), "the courses are shown to pick from");
  const choices = await evaluate(me.session, `document.querySelector("[data-copy-courses]").textContent`);
  check(choices.includes("2026FA_LAW_200_TA") && !choices.includes("FACULTY_TRAINING"), "the ones they teach or TA: " + JSON.stringify(choices));
  check(await press(me.session, "2027SP_LAW_610_LECTURE"), "one is picked");
  check(await evaluate(me.session, `window.__opened.at(-1).includes("/courses/505/users")`), "the window moves to its class list");
  const page1 = await grab("/api/v1/courses/505/users?enrollment_type[]=student&include[]=email&per_page=100");
  await paste(page1);
  const said1 = await said();
  check(said1.includes("Got 100 so far") && await evaluate(me.session, `window.__opened.at(-1).includes("page=2")`),
    "100 at a time: it asks for the next hundred: " + JSON.stringify([said1, await evaluate(me.session, "window.__opened")]));
  await paste(page1);
  check((await said()).includes("the part you pasted before"), "the same part again is caught");
  await paste(await grab("/api/v1/courses/505/users?enrollment_type[]=student&include[]=email&per_page=100&page=2"));
  check((await said()).includes("Got 200 so far"), "then 200");
  await paste(await grab("/api/v1/courses/505/users?enrollment_type[]=student&include[]=email&per_page=100&page=3"));
  check(await until(me.session, `!document.querySelector("[data-canvas-arrived]").hidden`), "then the whole list shows, ready to add");
  const arrived5 = await evaluate(me.session, `document.querySelector("[data-canvas-arrived]").innerText`);
  check(arrived5.includes("230 students from 2027SP_LAW_610_LECTURE (2027 Spring)"), "230 students, from that course");
  const sent = await evaluate(me.session, `document.querySelector("[data-canvas-arrived] input[name=list]").value`);
  check(!/login_id|sortable_name|"id"/.test(sent), "only names and emails are sent to Scheduler");
  await evaluate(me.session, `document.querySelector("[data-canvas-arrived] form").requestSubmit()`);
  check(await until(me.session, `document.readyState === "complete" && /Added 230 students/.test(document.body.innerText)`),
    "Add put all 230 on the sheet (" + sid5 + ")");

  await copyFlow("E2E Hundred");
  await paste(courses);
  await until(me.session, `!document.querySelector("[data-copy-courses]").hidden`);
  await press(me.session, "2026FA_LAW_700_HUNDRED");
  await paste(await grab("/api/v1/courses/606/users?enrollment_type[]=student&include[]=email&per_page=100"));
  await paste(await grab("/api/v1/courses/606/users?enrollment_type[]=student&include[]=email&per_page=100&page=2"));
  check(await until(me.session, `document.querySelector("[data-canvas-arrived]").innerText.includes("100 students from")`),
    "a class of exactly 100: the empty next page ends it");

  // ---- 6. Clicked while the class-list questions are still on their first
  // step: the Canvas step comes into view, and so does the list.
  const sid6 = await newSheet("E2E Start Step");
  await go(me.session, `${SITE}/teach/s/${sid6}/setup?step=students`);
  await until(me.session, `!!document.querySelector("[data-quiz]") && document.readyState === "complete"`);
  check(await evaluate(me.session, `!document.querySelector('[data-step="start"]').hidden && document.querySelector('[data-step="canvas"]').hidden`),
    "a new sheet's questions start at “Do you use Canvas?”");
  await evaluate(me.session, toFakeCanvas);
  before = created.length;
  await evaluate(me.session, code1);
  check(await until(me.session, `!document.querySelector('[data-step="canvas"]').hidden && document.querySelector("[data-bookmarklet-said]").offsetParent !== null`),
    "clicking the button there brings the Canvas step into view, saying so");
  const tab6 = await opened(before);
  const canvas6 = await attach(tab6.targetId);
  await send("Page.enable", {}, canvas6);
  await until(canvas6, `location.pathname === "/courses" || location.pathname === "/" ? document.readyState === "complete" : false`, 10000);
  await evaluate(canvas6, code1);
  await until(canvas6, `!!document.querySelector('[aria-label="Send to Scheduler"]') && document.querySelector('[aria-label="Send to Scheduler"]').innerText.includes("2026FA_LAW_540_SEC20")`);
  await press(canvas6, "2026FA_LAW_540_SEC20");
  check(await until(me.session, `document.querySelector("[data-canvas-arrived]").offsetParent !== null && document.querySelector("[data-canvas-arrived]").innerText.includes("8 students")`, 10000),
    "and the list arrives where it can be seen, with its Add button");

  // ---- 7. An old button (another key): told to drag the new one.
  await evaluate(me.session, `document.dispatchEvent(new CustomEvent("scheduler-button-check", { detail: { v: 2, key: "oldkey" } }))`);
  check(await until(me.session, `document.querySelector("[data-bookmarklet-said]").textContent.includes("out of date") && !document.querySelector(".canvas-easy-steps").hidden`),
    "an out-of-date button is spotted, and the steps to drag a new one show again");
} catch (err) {
  console.log("FAIL " + err.message);
  failures++;
} finally {
  try { ws && ws.close(); } catch { /* fine */ }
  chrome.kill();
  await sleep(300);
  rmSync(profile, { recursive: true, force: true });
  console.log(failures ? `${failures} failed` : "all passed");
  process.exit(failures ? 1 : 0);
}
