// End to end, in a real (headless) Chrome: "Connect Canvas" (canvas_oauth),
// against the stand-in Canvas (its OAuth2 pages) and a Scheduler told the
// stand-in's developer key. Run both, then this:
//
//     .venv/bin/python tools/fake_canvas.py
//     CANVAS_OAUTH_HOST=127.0.0.1:5077 CANVAS_OAUTH_CLIENT_ID=170000000000001 \
//         CANVAS_OAUTH_CLIENT_SECRET=fake-canvas-secret .venv/bin/flask --app app run --port 5051
//     node tools/e2e_canvas_connect.mjs
//
// Signed out of Canvas: Connect Canvas, sign in, Authorize, pick the course,
// and the class list is on the sheet, named.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const SITE = process.env.SITE || "http://127.0.0.1:5051";
const CANVAS = process.env.CANVAS || "http://127.0.0.1:5077";
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9334;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failures = 0;
const check = (ok, what) => { console.log((ok ? "PASS " : "FAIL ") + what); if (!ok) failures++; };

const profile = mkdtempSync(join(tmpdir(), "scheduler-connect-e2e-"));
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
  "--no-first-run", "--no-default-browser-check", "about:blank"], { stdio: "ignore" });

let ws, nextId = 1;
const waiting = new Map();
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
const loaded = (session, start) => until(session, `location.href.startsWith(${JSON.stringify(start)}) && document.readyState === "complete"`, 10000);
const press = (session, label) => evaluate(session, `(() => {
  const b = [...document.querySelectorAll("button, a")].find((x) => x.textContent.includes(${JSON.stringify(label)}));
  if (!b) return false; b.click(); return true; })()`);

try {
  await connect();
  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const { sessionId: me } = await send("Target.attachToTarget", { targetId, flatten: true });
  await send("Runtime.enable", {}, me);
  await send("Page.enable", {}, me);
  await send("Page.navigate", { url: `${SITE}/teach/login` }, me);
  await loaded(me, `${SITE}/teach/login`);
  await evaluate(me, `document.querySelector('input[name=email]').value = "connect-${Date.now()}@school.edu"; document.querySelector('input[name=email]').form.submit()`);
  await loaded(me, `${SITE}/teach/verify`);
  const code = await evaluate(me, `(document.body.innerText.match(/The code is\\s+(\\d{6})/) || [])[1]`);
  await evaluate(me, `document.querySelector('input[name=code]').value = "${code}"; document.querySelector('input[name=code]').form.submit()`);
  await until(me, `location.pathname.startsWith("/teach") && location.pathname !== "/teach/verify" && document.readyState === "complete"`);
  const sid = await evaluate(me, `(async () => {
    const csrf = document.querySelector('meta[name=csrf-token]').content;
    const day = (n) => new Date(Date.now() + n * 864e5).toISOString().slice(0, 10);
    const body = new URLSearchParams([["csrf_token", csrf], ["title", "Connect Seminar"], ["capacity", "30"],
      ["day_date", day(30)], ["day_key", ""], ["day_date", day(31)], ["day_key", ""]]);
    const r = await fetch("/teach/new", { method: "POST", body, redirect: "follow" });
    return (r.url.match(/\\/teach\\/s\\/([^/?#]+)/) || [])[1];
  })()`);
  check(!!sid, "made a sheet");
  await send("Page.navigate", { url: `${SITE}/teach/s/${sid}/setup?step=students` }, me);
  await loaded(me, `${SITE}/teach/s/${sid}/setup`);
  await evaluate(me, `sessionStorage.setItem("quiz-step-${sid}", "canvas")`);
  await send("Page.navigate", { url: `${SITE}/teach/s/${sid}/setup?step=students&s=1` }, me);
  await loaded(me, `${SITE}/teach/s/${sid}/setup`);
  check(await until(me, `[...document.querySelectorAll("a")].some((a) => a.textContent.trim() === "Connect Canvas" && a.offsetParent !== null)`),
    "the class-list step offers Connect Canvas first");
  check(await evaluate(me, `!document.querySelector("[data-canvas-download]").open`), "with the file upload folded under it");

  await press(me, "Connect Canvas");
  check(await loaded(me, `${CANVAS}/login`), "signed out of Canvas: Canvas's own sign-in page");
  await evaluate(me, `document.querySelector("form").submit()`);
  check(await until(me, `location.pathname === "/login/oauth2/auth" && document.body.innerText.includes("requesting access")`, 10000),
    "then Canvas's own Authorize page");
  await press(me, "Authorize");
  check(await until(me, `location.href.startsWith(${JSON.stringify(SITE)}) && document.body.innerText.includes("Which course is")`, 10000),
    "back on Scheduler, which asks which course");
  const choices = await evaluate(me, `document.body.innerText`);
  check(choices.includes("2026FA_BUSCOM_615_SEC1") && choices.includes("57 students") && !choices.includes("FACULTY_TRAINING"),
    "the professor's teaching courses, with sizes");
  await press(me, "2026FA_BUSCOM_615_SEC1");
  check(await until(me, `document.readyState === "complete" && /Added 57 students/.test(document.body.innerText)`, 10000),
    "one click on the course: 57 students on the sheet");
  check(await until(me, `document.body.innerText.includes("Who's on your list (57)")`), "named, right there");

  // Next time: Connect Canvas, Authorize (signed in now), and that course's list comes back for a look.
  await send("Page.navigate", { url: `${SITE}/teach/s/${sid}` }, me);
  await loaded(me, `${SITE}/teach/s/${sid}`);
  await evaluate(me, `document.querySelector(".canvas-update").open = true`);
  await press(me, "Connect Canvas");
  check(await until(me, `location.pathname === "/login/oauth2/auth"`, 10000), "next time: straight to Authorize");
  await press(me, "Authorize");
  check(await until(me, `location.pathname.includes("/roster/review/") && document.body.innerText.includes("Check the new class list")`, 10000),
    "and the course's list comes back with no picking, showing what changes");
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
