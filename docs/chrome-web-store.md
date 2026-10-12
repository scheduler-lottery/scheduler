# Publishing Scheduler Helper on the Chrome Web Store

Scheduler Helper (the `extension/` folder) is the Chrome extension behind the
class-list step's **Get my class list from Canvas** button. It's published:
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

The upload is `dist/scheduler-helper.zip`. It is version **1.0.3**, the one to upload
now (in place of any earlier one still waiting for review). What changed since 1.0.0:
the helper notices a Canvas sign-in made in any tab, and signs in from a fresh Canvas tab
when an open one has signed the professor out; pressing the page's button again brings
Canvas's sign-in page back to the front; Scheduler's page can ask it to bring that page
back to the front, and, once the class list is in and the professor presses **Remove
Scheduler Helper from Chrome**, to remove itself. It asks for no new permissions.

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

  > Scheduler Helper brings a course's class list from your Canvas into Scheduler
  > (scheduler-lottery.vercel.app), the presentation-day sign-up site, with one click.
  > On a sign-up sheet's Class list step, click "Get my class list from Canvas". The
  > helper asks your school's Canvas for your courses, then for the students in the
  > one you pick, signed in as you, and hands Scheduler's page each student's name
  > and email address. Nothing else is read. Nothing in Canvas changes. It never
  > sees your password, does nothing until you click on Scheduler's own site, and
  > keeps nothing.

- **Category:** Education
- **Language:** English
- **Icon:** `dist/scheduler-helper/icon-128.png`
- **Screenshots:** the three in `dist/store/`, which are 1280×800
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

- **Why it needs the host permission (canvas.northwestern.edu):**

  > To read the user's courses and a chosen course's class list (names and email
  > addresses) from Canvas's API, signed in as the user, when they ask on
  > Scheduler's site. It never writes or changes anything.

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

- The class-list step offers **Add Scheduler Helper to Chrome**.
- Once the helper is added, the step offers **Get my class list from Canvas**.
- Uploading Canvas's student file stays as a folded fallback.

## Updates

Bump `version` in `extension/manifest.json`, run `tools/build_extension.py store`
again, upload the new zip under **Package**, and submit. The store has 1.0.0: upload
1.0.3 now (it is already the version in `extension/manifest.json`, so skip the bump this
once). It asks for no new permissions, so Chrome updates professors who have the helper
without asking them anything. Until it's approved, the site works with 1.0.0 too (its
Remove button then shows the steps to remove it in Chrome's menu).

Keep the permissions exactly as they are. An update that adds a permission Chrome
warns about turns the helper off until each professor approves it again.
