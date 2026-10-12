// End to end, in a real (headless) Chrome with no Scheduler Helper: what the
// Canvas step offers in each browser, and the file upload. Against this site
// running locally and told a store page for the helper:
//
//     CANVAS_DEV_HOST=127.0.0.1:5077 CANVAS_HELPER_IDS=lgfmbiibmekimpipoeodjmkeimdffnmm \
//         CANVAS_HELPER_STORE_URL=https://chromewebstore.google.com/ OWNER_EMAIL=owner@school.edu .venv/bin/python app.py
//     node tools/e2e_canvas_step.mjs
//
// Chrome and Edge: "Add Scheduler Helper", with their own steps, and the file
// folded under it. Safari and Firefox (the helper is Chrome's): how to open
// this page in Chrome, with the file folded under it; an iPhone: a phone's
// words. The file: Canvas opens in a tab of its own,
// both files from Safari's folder dropped at once send the student file, and
// the file goes in. Then the class page: its links open what they point at,
// and messages come up where screen readers read them.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const SITE = process.env.SITE || "http://127.0.0.1:5050";
const CANVAS = process.env.CANVAS || "http://127.0.0.1:5077";
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = Number(process.env.DEBUG_PORT || 9335);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failures = 0;
const check = (ok, what) => { console.log((ok ? "PASS " : "FAIL ") + what); if (!ok) failures++; };

const profile = mkdtempSync(join(tmpdir(), "scheduler-step-e2e-"));
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
  "--no-first-run", "--no-default-browser-check", "about:blank"], { stdio: "ignore" });

let ws, nextId = 1;
const waiting = new Map();
const thrown = []; // the pages' uncaught errors
async function connect() {
  for (let i = 0; i < 50; i++) {
    try {
      const info = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json();
      ws = new WebSocket(info.webSocketDebuggerUrl);
      await new Promise((ok, no) => { ws.onopen = ok; ws.onerror = no; });
      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.method === "Runtime.exceptionThrown") thrown.push(msg.params.exceptionDetails);
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
  // SHOTS=<folder>: a picture of each browser's offer, for looking over.
  const shot = async (name) => {
    if (!process.env.SHOTS) return;
    await send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false }, me);
    await evaluate(me, `document.querySelector("[data-canvas-guide]").closest("section, details, .card").scrollIntoView({ block: "start" })`);
    await sleep(500);
    const { data } = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false }, me);
    writeFileSync(join(process.env.SHOTS, name), Buffer.from(data, "base64"));
    await send("Emulation.clearDeviceMetricsOverride", {}, me);
  };
  const shown = (sel) => evaluate(me, `(() => { const el = document.querySelector(${JSON.stringify(sel)}); return !!el && el.offsetParent !== null; })()`);
  const visibleText = () => evaluate(me, `document.querySelector("[data-canvas-guide]").innerText`);
  // Canvas's file, opened (folded under the helper, or under the way to Chrome).
  const openFile = () => evaluate(me, `document.querySelector("[data-canvas-download]").open = true`);

  // Chrome, no helper yet: add it (its store page), with the file folded under.
  await as("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36");
  check(await shown("[data-helper-add]"), "Chrome: Add Scheduler Helper to Chrome");
  check(await evaluate(me, `document.querySelector("[data-helper-add]").href`) === "https://chromewebstore.google.com/",
    "which goes to the helper's store page");
  let words = await visibleText();
  check(words.includes("Add to Chrome") && words.includes("Add extension") && !words.includes("Allow extensions from other stores"),
    "with Chrome's own words");
  check(await evaluate(me, `!document.querySelector("[data-canvas-download]").open`) && words.includes("Or use Canvas's file of your students instead"),
    "and the file folded under it");
  check(!/token|bookmark|copy and paste/i.test(words), "and nothing else: no token, no bookmark, no copy and paste");
  await shot("chrome-add-helper.png");

  // Edge: Edge's steps.
  await as("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36 Edg/155.0.0.0");
  words = await visibleText();
  check(words.includes("Add Scheduler Helper to Edge") && words.includes("Allow extensions from other stores") && words.includes("Get"),
    "Edge: Edge's own steps");
  await shot("edge-add-helper.png");

  // Safari and Firefox: the helper is Chrome's, so how to open this page in
  // Chrome (its link, copied in one click), or Canvas's file, folded under it.
  // (Neither has Chrome's way to open the file window in Downloads.)
  const { identifier: noPicker } = await send("Page.addScriptToEvaluateOnNewDocument", { source: "delete window.showOpenFilePicker;" }, me);
  for (const [name, agent] of [["Safari", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15"],
                               ["Firefox", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:140.0) Gecko/20100101 Firefox/140.0"]]) {
    await as(agent);
    words = await visibleText();
    check(!(await shown("[data-helper-add]")) && await shown("[data-helper-chrome]") && words.includes("let Google Chrome do it")
      && words.includes("Copy this page's link") && words.includes("Paste") && words.includes("Get Chrome")
      && words.includes("it emails you a new code"),
      `${name}: how to open this page in Chrome, where the helper does it`);
    check((await evaluate(me, `document.querySelector("[data-page-link]").value`)).indexOf(step + "&b=") === 0,
      `${name}: the link to copy is this page's`);
    check(await evaluate(me, `!document.querySelector("[data-canvas-download]").open`) && words.includes("Or use Canvas's file of your students instead"),
      `${name}: or Canvas's file, folded under it`);
    await evaluate(me, `document.querySelector("[data-canvas-download] summary").click()`);
    check(await shown("[data-canvas-download] form[data-dropzone]"), `${name}: and opened, the box for it, in the last step`);
    await shot(`${name.toLowerCase()}-use-chrome.png`);
  }
  check(await evaluate(me, `[...document.querySelectorAll("[data-safari-only]")].every((el) => el.hidden)`),
    "Firefox: no Safari folder lines");
  check((await visibleText()).includes("Downloads on the left") && await evaluate(me, `[...document.querySelectorAll("[data-canvas-step=drop] svg")].some((p) => p.getClientRects().length)`),
    "Firefox: step 4 says to click Downloads, with a picture of the window");

  // Both files from the folder Safari makes, dragged in together: the student file goes, not the grades file.
  await evaluate(me, `(() => {
    HTMLFormElement.prototype.submit = function () { window.__sent = this.querySelector("input[type=file]").files[0].name; };
    const dt = new DataTransfer();
    dt.items.add(new File(["Assignment Name,Points Possible\\nMemo,10\\n"], "course_grade.csv", { type: "text/csv" }));
    dt.items.add(new File(["Name,Email\\nAlex Kim,akim@u.school.edu\\n"], "students.csv", { type: "text/csv" }));
    document.querySelector("[data-canvas-download] .dropzone").dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true }));
  })()`);
  check(await until(me, `window.__sent === "students.csv"`, 3000), "both of Safari's files dropped at once: the student file is the one sent");

  // An iPhone: a phone's words, no Mac folder, and nothing about dragging.
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true }, me);
  await send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 }, me);
  await as("Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Mobile/15E148 Safari/604.1");
  words = await visibleText();
  check(await evaluate(me, `document.querySelector("[data-canvas-open]").textContent`) === "Open Canvas",
    "iPhone: the button says Open Canvas");
  check(words.includes("This part is easiest on a computer") && words.includes("Tap the box below") && words.includes("Browse"),
    "iPhone: what to do on a phone, and how to get back to this page later");
  check(!words.includes("Safari may turn") && !words.includes("double-click") && !words.includes("on the left of the window"),
    "iPhone: no Mac folder, no double-clicking, no window with Downloads on its left");
  check(!/drag/i.test(words), "iPhone: nothing about dragging");
  await shot("iphone-file.png");
  await send("Emulation.setTouchEmulationEnabled", { enabled: false }, me);
  await send("Emulation.clearDeviceMetricsOverride", {}, me);
  await as("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15");
  await openFile();
  words = await visibleText();
  check(words.includes("Double-click the newest folder there") && words.includes("opens Canvas's file into a folder")
    && !words.includes("its name ends in .zip"), "Safari on a Mac: the folder Safari makes is the usual case");
  await send("Page.removeScriptToEvaluateOnNewDocument", { identifier: noPicker }, me);

  // Windows: it hides the .zip ending, so the steps don't mention it.
  await as("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0");
  await openFile();
  words = await visibleText();
  check(words.includes("zipped folder") && !words.includes("ends in .zip"), "Windows: a zipped folder, no .zip ending");
  await as("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15");
  await openFile();

  // The file: Canvas opens in a tab of its own (the browser's usual top, where downloads show), and the file goes in.
  await evaluate(me, `(() => { window.__opened = []; window.open = (u, n, f) => { window.__opened.push([u, f]); return null; }; })()`);
  await evaluate(me, `document.querySelector("[data-canvas-open]").click()`);
  check(await evaluate(me, `window.__opened.length === 1 && window.__opened[0][0] === "http://127.0.0.1:5077/" && window.__opened[0][1] === "noopener"`),
    "Open Canvas: Canvas (its Dashboard) in a tab of its own, not a small window without downloads");
  await evaluate(me, `document.querySelector("[data-canvas-open]").click()`);
  check(await evaluate(me, `window.__opened.length === 2`), "pressed again, Canvas opens again (never a button that does nothing)");
  // Back from Canvas: a bar at the bottom to choose the file, and the page doesn't jump.
  const y = await evaluate(me, `(() => { document.querySelector("[data-canvas-steps]").scrollIntoView(); return scrollY; })()`);
  const { targetId: other } = await send("Target.createTarget", { url: "about:blank" });
  await until(me, `document.visibilityState === "hidden"`, 3000);
  await send("Target.activateTarget", { targetId: other });
  await sleep(300);
  await send("Target.closeTarget", { targetId: other });
  await send("Target.activateTarget", { targetId });
  check(await until(me, `!document.querySelector("[data-came-back]").hidden`, 4000) && await evaluate(me, `scrollY`) === y,
    "back from Canvas: a bar to choose Canvas's file, and the page stays where it was");
  check(await evaluate(me, `typeof window.showOpenFilePicker === "function"`) ? (await visibleText()).includes("opens right in your Downloads folder")
    : true, "Chrome: the box's window opens right in Downloads, and step 4 says so");
  const csv = join(profile, "students.csv");
  writeFileSync(csv, "Student Name,Student ID,Student SIS ID,Email,Section Name\nAlex Kim,1,111,akim@u.school.edu,Sec 1\nSam Lee,2,112,slee@u.school.edu,Sec 1\n");
  const { root } = await send("DOM.getDocument", {}, me);
  const { nodeId } = await send("DOM.querySelector", { nodeId: root.nodeId, selector: "[data-canvas-download] input[type=file]" }, me);
  await send("DOM.setFileInputFiles", { nodeId, files: [csv] }, me); // (as choosing it does: the page sends it at once)
  check(await until(me, `document.readyState === "complete" && /Added 2 students/.test(document.body.innerText)`, 10000),
    "the downloaded file goes in: 2 students");
  check(await until(me, `document.body.innerText.includes("Who's on your list (2)")`), "named, right there");
  check(await until(me, `document.activeElement && document.activeElement.hasAttribute("data-focus-on-load")`, 4000),
    "and the report of what was added has the focus (so it's read out)");

  // The class page: a link to a folded card opens it (and nothing throws).
  thrown.length = 0;
  await send("Page.navigate", { url: `${SITE}/teach/s/${sid}` }, me);
  await loaded(me, `${SITE}/teach/s/${sid}`);
  await sleep(400);
  await evaluate(me, `(() => { try { localStorage.clear(); } catch (e) {} document.getElementById("try-it").open = false; location.hash = "#try-it"; })()`);
  check(await until(me, `document.getElementById("try-it").open`, 3000), "class page: a link to See it as a student opens it");
  await evaluate(me, `(() => { document.getElementById("share").open = false; location.hash = "#share"; })()`);
  check(await until(me, `document.getElementById("share").open`, 3000), "and a link to Share opens Share");
  // A message after adding a student: brought up over the page, where screen readers read it, and gone after a while.
  await evaluate(me, `(() => { const f = document.querySelector("form.quick-add"); f.querySelector("textarea").value = "Dana Ho, dana@u.school.edu"; f.requestSubmit(); })()`);
  await loaded(me, `${SITE}/teach/s/${sid}`);
  check(await until(me, `(() => { const s = document.querySelector("[data-flash-stack]"); return !!s && s.parentElement === document.body; })()`, 4000),
    "class page: the message comes up where screen readers read it");
  check(await until(me, `!document.querySelector("[data-flash-stack] .flash")`, 12000), "and good news fades after a while");
  check(!thrown.length, "no errors on the page" + (thrown.length ? ": " + JSON.stringify(thrown[0]).slice(0, 300) : ""));
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
