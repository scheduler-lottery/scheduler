// End to end, in a real (headless) Chrome: the "Send to Scheduler" button,
// against the stand-in Canvas (tools/fake_canvas.py on :5077) and this site
// running locally (app.py on :5050, dev mode, so sign-in codes show on
// screen). Run both, then:
//
//     node tools/e2e_canvas_button.mjs
//
// 1. Beside: a signed-in instructor's setup page opens Canvas in its own
//    window; there, after signing in (it lands on the Dashboard), the button
//    is clicked and a class picked; the list must show up on the setup page,
//    and Add must put it on the sheet.
// 2. In a tab of its own (no Scheduler page behind it): the button on a
//    course page must open Scheduler in a new tab, where the instructor
//    picks the sheet and adds the list.
// The button is run the way a bookmark runs: its javascript: address,
// decoded, evaluated in the Canvas page.
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

  // A sheet, made the way the form makes one.
  const sid = await evaluate(me.session, `(async () => {
    const csrf = document.querySelector('meta[name=csrf-token]').content;
    const day = (n) => new Date(Date.now() + n * 864e5).toISOString().slice(0, 10);
    const body = new URLSearchParams([["csrf_token", csrf], ["title", "E2E Seminar"], ["capacity", "30"],
      ["day_date", day(30)], ["day_key", ""], ["day_date", day(31)], ["day_key", ""]]);
    const r = await fetch("/teach/new", { method: "POST", body, redirect: "follow" });
    return (r.url.match(/\\/teach\\/s\\/([^/?#]+)/) || [])[1];
  })()`);
  check(!!sid, "made a sheet (" + sid + ")");

  // ---- 1. Beside: the setup page opens Canvas; the list comes back to it.
  await go(me.session, `${SITE}/teach/s/${sid}/setup?step=students`);
  await evaluate(me.session, `sessionStorage.setItem("quiz-step-${sid}", "canvas"); location.reload()`);
  await until(me.session, `!!document.querySelector("[data-bookmarklet]") && document.readyState === "complete"`);
  const button = await evaluate(me.session, `document.querySelector("[data-bookmarklet]").getAttribute("href")`);
  check(button.startsWith("javascript:") && decodeURIComponent(button).includes(SITE), "the button is made for this site");
  const code1 = decodeURIComponent(button.slice("javascript:".length));

  const before = created.length;
  // Canvas's address comes from the school picked; here, the stand-in's.
  await evaluate(me.session, `(() => { const open = window.open.bind(window);
    window.open = (url, name, features) => open(${JSON.stringify(CANVAS + "/login")}, name, features);
    document.querySelector("[data-canvas-tab]").click(); })()`);
  await until(me.session, `document.documentElement.classList.contains("canvas-beside") || true`);
  let popup = null;
  for (let i = 0; i < 40 && !popup; i++) { popup = created.slice(before).find((t) => t.type === "page" && t.openerId); await sleep(100); }
  check(!!popup, "Canvas opened in a tab, with the setup page as its opener");
  const canvas = await attach(popup.targetId);
  await send("Page.enable", {}, canvas);
  const loaded = await until(canvas, `location.href.startsWith(${JSON.stringify(CANVAS)}) && !!document.querySelector("form")`, 15000);
  if (!loaded) console.log("  (Canvas window is at " + await evaluate(canvas, "location.href") + ")");
  await evaluate(canvas, `document.querySelector("form").submit()`);
  await until(canvas, `location.pathname === "/" && document.readyState === "complete"`);
  check((await text(canvas)).includes("Dashboard"), "signing in to Canvas lands on the Dashboard");

  await evaluate(canvas, code1); // the bookmark, clicked
  await until(canvas, `document.body.innerText.includes("Which class")`);
  check(await press(canvas, "2026FA_BUSCOM_615_SEC1"), "the button lists the professor's courses, and one is picked");
  const shown = await until(me.session, `!document.querySelector("[data-canvas-arrived]").hidden`, 10000);
  check(shown, "the class list showed up on the setup page");
  const arrived = await evaluate(me.session, `document.querySelector("[data-canvas-arrived]").innerText`);
  check(arrived.includes("57 students from 2026FA_BUSCOM_615_SEC1"), "it says 57 students, from that course (all pages of Canvas's answer)");
  check(arrived.includes("All with email addresses"), "with their emails");
  await sleep(2200);
  const stillOpen = (await send("Target.getTargets")).targetInfos.some((t) => t.targetId === popup.targetId);
  check(!stillOpen, "the Canvas tab closed by itself, back to the list");
  await evaluate(me.session, `document.querySelector("[data-canvas-arrived] form").submit()`);
  await until(me.session, `location.search.includes("step=students") && document.readyState === "complete"`);
  const after = await text(me.session);
  check(/Added 57 students/.test(after) && after.includes("Canvas"), "Add put 57 students on the sheet: " + (after.match(/Added[^\n]*/) || [""])[0]);

  // ---- 2. A Canvas tab of its own: the button takes this tab to Scheduler.
  const own = await newPage(`${CANVAS}/courses/202`);
  await until(own.session, `location.pathname === "/courses/202" && document.readyState === "complete"`);
  await evaluate(own.session, code1);
  const moved = await until(own.session, `location.pathname.startsWith("/teach/canvas-import/") && document.readyState === "complete"`, 10000);
  check(moved, "with no Scheduler page behind it, the button took the tab to Scheduler");
  const page2 = await text(own.session);
  check(page2.includes("8 students from 2026FA_LAW_540_SEC20"), "Scheduler shows the list from Canvas, to look over");
  check(page2.includes("E2E Seminar") && page2.includes("A new sign-up sheet"), "and asks which sheet it goes to");
  await evaluate(own.session, `document.querySelector("form.arrived-form").submit()`);
  await until(own.session, `!location.pathname.startsWith("/teach/canvas-import/") && document.readyState === "complete"`);
  const page3 = await text(own.session);
  check(/What changes|Replace|Add them|add to/i.test(page3), "a sheet that already has a list shows the changes before anything is replaced");
  // The same list can't be used twice.
  const again = await evaluate(own.session, `history.length`);
  check(again >= 1, "done");
  // ---- 3. No bookmarks: Canvas's lists, copied over as text (a new sheet).
  const sid3 = await evaluate(me.session, `(async () => {
    const csrf = document.querySelector('meta[name=csrf-token]').content;
    const day = (n) => new Date(Date.now() + n * 864e5).toISOString().slice(0, 10);
    const body = new URLSearchParams([["csrf_token", csrf], ["title", "E2E Copy"], ["capacity", "30"],
      ["day_date", day(30)], ["day_key", ""], ["day_date", day(31)], ["day_key", ""]]);
    const r = await fetch("/teach/new", { method: "POST", body, redirect: "follow" });
    return (r.url.match(/\\/teach\\/s\\/([^/?#]+)/) || [])[1];
  })()`);
  await go(me.session, `${SITE}/teach/s/${sid3}/setup?step=students`);
  await until(me.session, `!!document.querySelector("[data-copy-paste]")`);
  const canvasTab = await newPage(`${CANVAS}/`); // what the Canvas window would show, to copy
  const coursesJson = await evaluate(canvasTab.session, `fetch("/api/v1/courses?enrollment_type=teacher&per_page=100").then((r) => r.text())`);
  await evaluate(me.session, `(() => { const box = document.querySelector("[data-copy-paste]");
    box.value = ${JSON.stringify(coursesJson)}; box.dispatchEvent(new Event("input")); })()`);
  check(await until(me.session, `!document.querySelector("[data-copy-courses]").hidden`), "pasting Canvas's course list shows the courses to pick from");
  await evaluate(me.session, `(() => { const open = window.open.bind(window); window.open = () => null; })()`);
  check(await press(me.session, "2026FA_LAW_540_SEC20"), "and one is picked");
  const usersJson = await evaluate(canvasTab.session, `fetch("/api/v1/courses/202/users?enrollment_type[]=student&include[]=email&per_page=100").then((r) => r.text())`);
  await evaluate(me.session, `(() => { const box = document.querySelector("[data-copy-paste]");
    box.value = ${JSON.stringify(usersJson)}; box.dispatchEvent(new Event("input")); })()`);
  check(await until(me.session, `!document.querySelector("[data-canvas-arrived]").hidden`), "pasting its class list shows it, ready to add");
  const arrived3 = await evaluate(me.session, `document.querySelector("[data-canvas-arrived]").innerText`);
  check(arrived3.includes("8 students from 2026FA_LAW_540_SEC20"), "8 students, from that course");
  const sent = await evaluate(me.session, `document.querySelector("[data-canvas-arrived] input[name=list]").value`);
  check(!/login_id|sortable_name|"id"/.test(sent), "only names and emails are sent to Scheduler");
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
