// End to end, in a real (headless) Chrome with no Scheduler Helper: what the
// Canvas step offers in each browser, and the file upload. Against this site
// running locally and told a store page for the helper:
//
//     CANVAS_HELPER_IDS=lgfmbiibmekimpipoeodjmkeimdffnmm CANVAS_HELPER_STORE_URL=https://chromewebstore.google.com/ \
//         OWNER_EMAIL=owner@school.edu .venv/bin/python app.py
//     node tools/e2e_canvas_step.mjs
//
// Chrome and Edge: "Add Scheduler Helper", with their own steps, and the file
// folded under it. Safari and Firefox (no helper to add yet): the file, open,
// first. The file: Canvas opens beside the page, and the file goes in.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const SITE = process.env.SITE || "http://127.0.0.1:5050";
const CANVAS = process.env.CANVAS || "http://127.0.0.1:5077";
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9335;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failures = 0;
const check = (ok, what) => { console.log((ok ? "PASS " : "FAIL ") + what); if (!ok) failures++; };

const profile = mkdtempSync(join(tmpdir(), "scheduler-step-e2e-"));
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
  await evaluate(me, `document.querySelector('input[name=email]').value = "step-${Date.now()}@school.edu"; document.querySelector('input[name=email]').form.submit()`);
  await loaded(me, `${SITE}/teach/verify`);
  const code = await evaluate(me, `(document.body.innerText.match(/The code is\\s+(\\d{6})/) || [])[1]`);
  await evaluate(me, `document.querySelector('input[name=code]').value = "${code}"; document.querySelector('input[name=code]').form.submit()`);
  await until(me, `location.pathname.startsWith("/teach") && location.pathname !== "/teach/verify" && document.readyState === "complete"`);
  const sid = await evaluate(me, `(async () => {
    const csrf = document.querySelector('meta[name=csrf-token]').content;
    const day = (n) => new Date(Date.now() + n * 864e5).toISOString().slice(0, 10);
    const body = new URLSearchParams([["csrf_token", csrf], ["title", "Step Seminar"], ["capacity", "30"],
      ["day_date", day(30)], ["day_key", ""], ["day_date", day(31)], ["day_key", ""]]);
    const r = await fetch("/teach/new", { method: "POST", body, redirect: "follow" });
    return (r.url.match(/\\/teach\\/s\\/([^/?#]+)/) || [])[1];
  })()`);
  check(!!sid, "made a sheet");
  const step = `${SITE}/teach/s/${sid}/setup?step=students`;
  const as = async (agent) => {
    await send("Network.setUserAgentOverride", { userAgent: agent }, me);
    await send("Page.navigate", { url: step + "&a=" + Date.now() }, me);
    await loaded(me, step);
    await evaluate(me, `sessionStorage.setItem("quiz-step-${sid}", "canvas")`);
    await send("Page.navigate", { url: step + "&b=" + Date.now() }, me);
    await loaded(me, step);
    await sleep(600); // (the page asks for the helper, and hears nothing)
  };
  const shown = (sel) => evaluate(me, `(() => { const el = document.querySelector(${JSON.stringify(sel)}); return !!el && el.offsetParent !== null; })()`);
  const visibleText = () => evaluate(me, `document.querySelector("[data-canvas-guide]").innerText`);

  // Chrome, no helper yet: add it (its store page), with the file folded under.
  await as("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36");
  check(await shown("[data-helper-add]"), "Chrome: Add Scheduler Helper to Chrome");
  check(await evaluate(me, `document.querySelector("[data-helper-add]").href`) === "https://chromewebstore.google.com/",
    "which goes to the helper's store page");
  let words = await visibleText();
  check(words.includes("Add to Chrome") && words.includes("Add extension") && !words.includes("Allow extensions from other stores"),
    "with Chrome's own words");
  check(await evaluate(me, `!document.querySelector("[data-canvas-download]").open`) && words.includes("Or upload Canvas's student file instead"),
    "and the file folded under it");
  check(!/token|bookmark|copy and paste/i.test(words), "and nothing else: no token, no bookmark, no copy and paste");

  // Edge: Edge's steps.
  await as("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36 Edg/155.0.0.0");
  words = await visibleText();
  check(words.includes("Add Scheduler Helper to Edge") && words.includes("Allow extensions from other stores") && words.includes("Get"),
    "Edge: Edge's own steps");

  // Safari and Firefox, with no helper to add: the file, open, first.
  for (const [name, agent] of [["Safari", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15"],
                               ["Firefox", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:140.0) Gecko/20100101 Firefox/140.0"]]) {
    await as(agent);
    check(!(await shown("[data-helper-add]")) && await evaluate(me, `document.querySelector("[data-canvas-download]").open`)
      && (await visibleText()).includes("Upload Canvas's student file"), `${name}: no helper to add yet, so the file, first`);
    check(await shown("[data-quiz-drop]"), `${name}: and the box for it`);
  }

  // The file: Canvas opens beside this page (a window of its own), and the file goes in.
  await evaluate(me, `(() => { window.__opened = []; window.open = (u, n, f) => { const w = { closed: false, focus() {}, close() { w.closed = true; } };
    w.location = { set href(x) { window.__opened.push(x); } }; return w; }; })()`);
  await evaluate(me, `document.querySelector("[data-canvas-beside]").click()`);
  check(await evaluate(me, `document.documentElement.classList.contains("canvas-beside") && window.__opened[0].endsWith("/courses")`),
    "Open Canvas beside this page: Canvas on the right, the steps on the left");
  const csv = join(profile, "students.csv");
  writeFileSync(csv, "Student Name,Student ID,Student SIS ID,Email,Section Name\nAlex Kim,1,111,akim@u.school.edu,Sec 1\nSam Lee,2,112,slee@u.school.edu,Sec 1\n");
  const { root } = await send("DOM.getDocument", {}, me);
  const { nodeId } = await send("DOM.querySelector", { nodeId: root.nodeId, selector: "[data-quiz-drop] input[type=file]" }, me);
  await send("DOM.setFileInputFiles", { nodeId, files: [csv] }, me); // (as choosing it does: the page sends it at once)
  check(await until(me, `document.readyState === "complete" && /Added 2 students/.test(document.body.innerText)`, 10000),
    "the downloaded file goes in: 2 students");
  check(await until(me, `document.body.innerText.includes("Who's on your list (2)")`), "named, right there");
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
