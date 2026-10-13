# Publishing Scheduler Helper on the Chrome Web Store

Scheduler Helper (the `extension/` folder) is the Chrome extension behind the
class-list step's **Import class list** button, for any school's Canvas. It's published:
<https://chromewebstore.google.com/detail/scheduler-helper/hafmidgmpikkkjphmcbnggidbaeoiapd>,
and the site knows that id (`settings.CHROME_HELPER_ID`). Edge users can install it from
the Chrome Web Store too. In other browsers (Safari, Firefox), the step says how to open
the page in Chrome, or to use Canvas's file of students instead.

You'll need about 45 minutes, plus Google's review, which usually takes a few
days and can take a couple of weeks for a new developer.

## 1. Build the package

```bash
.venv/bin/python tools/build_extension.py store
```

The upload is `dist/scheduler-helper.zip`. It is version **1.1.0**, the one to upload
now. The store has 1.0.6 (checked against helper-v1.0.6: it matches). What changed:
- **Any school's Canvas**, not just Northwestern's. It may read no website at all until
  the professor allows their school's Canvas, once, the first time they import: the
  helper opens a small window of its own (`allow.html`) naming that Canvas, and its
  **Allow** button asks Chrome, which asks the professor. So adding it asks for nothing,
  and no install warning names another school's Canvas.
- The page is a checklist (add it, import the class list, all done: delete it?), and a
  first class list goes straight in once the class is picked.
- It keeps nothing, and says so in its own box and its allow window.

Permissions: still only `scripting`; Northwestern's Canvas moves from
`host_permissions` to `optional_host_permissions` (`https://*/*`, each site granted only
by the professor's own click). Optional permissions aren't asked for at install or on an
update, so Chrome updates everyone to 1.1.0 without turning it off. A professor who had
1.0.x allows their school's Canvas once, on their next import.

The screenshots are in `dist/store/`; the browser test makes them:

```bash
SHOTS=dist/store node tools/e2e_canvas_helper.mjs
```

That test needs the stand-in Canvas and a local Scheduler running; see the top of the
test file.

## 2. Make a developer account (once)

1. Use a Google account that's for Scheduler, not a personal one. Turn on 2-Step
   Verification; publishing requires it.
2. Go to <https://chrome.google.com/webstore/devconsole>, accept the developer
   agreement, and pay the one-time registration fee.
3. Set the publisher name to **Scheduler**, verify the contact email, and say you are
   not a trader (the EU question).

## 3. Add the item

Click **New item** and upload `dist/scheduler-helper.zip`. Then fill in each tab.

**Store listing**

- **Description:**

  > Scheduler Helper brings a course's class list from your school's Canvas into
  > Scheduler (scheduler-lottery.vercel.app), the presentation-day sign-up site, with
  > one click. On a sign-up sheet's Class list step, press "Import class list" and pick
  > the class. The first time, it asks you to allow it to read your school's Canvas
  > site (until then it can read no website at all). It asks Canvas for your courses,
  > then for the students in the one you pick, signed in as you, and hands Scheduler's
  > page each student's name and email address. Nothing else is read. Nothing in
  > Canvas changes. It never sees your password, does nothing until you ask on
  > Scheduler's own site, and keeps nothing: no class list, no course, no sign-in.
  > Once your list is in, it offers to delete itself.

- **Category:** Education
- **Language:** English
- **Icon:** `dist/scheduler-helper/icon-128.png`
- **Screenshots:** the three in `dist/store/`, which are 1280×800
- **Marquee promo tile:** `dist/chrome-web-store-upload/marquee-1400x560.png` (1400×560, no
  transparency), made from `docs/store-marquee.html`:
  `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --hide-scrollbars --force-device-scale-factor=1 --window-size=1400,560 --screenshot=marquee-1400x560.png docs/store-marquee.html`
- **Homepage:** <https://scheduler-lottery.vercel.app/helper>

**Privacy**

- **Single purpose:**

  > Fetch a course's student names and email addresses from the user's own Canvas,
  > when the user asks on Scheduler's site, and hand them to that site.

- **Why it needs `scripting`:**

  > When the user isn't signed in to Canvas, or their browser blocks the request
  > from the extension itself, the helper runs a read-only request inside a Canvas
  > tab (Canvas's own API, as the signed-in user) to get the course list and class
  > list. It runs only when the user clicks the button on Scheduler's site.

- **Why it needs the optional host permission (https://*/*):**

  > Schools run Canvas at their own addresses (canvas.myschool.edu), so the helper
  > can't list them in advance. It has no host access at install. The first time a
  > user imports a class list, the helper opens its own window naming their school's
  > Canvas, and only on the user's click there asks Chrome for that one site
  > (chrome.permissions.request with that origin alone). It then reads only the user's
  > courses and a chosen course's class list (names and email addresses) from Canvas's
  > API, signed in as the user, when they ask on Scheduler's site. It never writes or
  > changes anything, and stores nothing.

- **Remote code:** No, I am not using remote code.
- **Data usage:** tick **Personally identifiable information** (names and email
  addresses of the user's students) and **Website content**. Then tick all three
  statements: not sold to third parties, not used for purposes unrelated to the single
  purpose, and not used for creditworthiness or lending.
- **Privacy policy:** <https://scheduler-lottery.vercel.app/privacy#canvas>

**Distribution**

- **Visibility: Unlisted.** Only people with the link can find it, which is everyone
  Scheduler sends there.
- **Regions:** all.

Then click **Submit for review**.

## 4. Turn it on in Scheduler

Nothing to do: the site has the item's id and page built in (`settings.py`). Vercel's
`CANVAS_HELPER_IDS` can add another id (a copy for testing, say), and
`CANVAS_HELPER_STORE_URL` can point elsewhere, but neither is needed.

In Chrome and Edge (Edge installs from Chrome's store after an "Allow
extensions from other stores" click, and the page says so):

- The class-list step offers **Add Scheduler Helper to Chrome** (step 1).
- Once the helper is added, step 1 is checked off and step 2 offers **Import class list**.
- For schools other than Northwestern, the site offers it once the store has 1.1.0: it
  asks Chrome's update service which version the store has (`helperstore.py`, every six
  hours), so there's nothing to switch on.
- Uploading Canvas's student file stays as a folded fallback.

## Updates

Bump `version` in `extension/manifest.json`, run `tools/build_extension.py store`
again, upload the new zip under **Package**, and submit. Then tag that version, so the
public build keeps its zip under the repository's Releases for good:

```bash
git tag helper-v1.1.0 && git push origin helper-v1.1.0
```

(`.github/workflows/helper-release.yml` builds the zip from the tag, checks the tag
matches the manifest's version, and attaches it with its sha256. Anyone can then check the
store's copy against the code with `tools/verify_extension.py <file> --ref helper-v1.1.0`.)
Until 1.1.0 is approved, the site works with 1.0.6 and 1.0.0 too, for Northwestern's
Canvas.

Keep `permissions` exactly as they are (`scripting`). An update that adds a permission
Chrome warns about turns the helper off until each professor approves it again; optional
host permissions don't.
