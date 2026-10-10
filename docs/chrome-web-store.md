# Publishing Scheduler Helper on the Chrome Web Store

Scheduler Helper (the `extension/` folder) is the browser extension behind the
class-list step's **Get my class list from Canvas** button. Scheduler doesn't
offer it until it's published and the site is told its id. Edge users can
install it from the Chrome Web Store too.

You'll need about 45 minutes, plus Google's review, which usually takes a few
days and can take a couple of weeks for a new developer.

## 1. Build the package

```bash
.venv/bin/python tools/build_extension.py store
```

The upload is `dist/scheduler-helper.zip`. The screenshots are in `dist/store/`; the
browser test makes them:

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

Once it's published, the item's page shows its **id**: 32 letters, in the address
after `/detail/…/`. In Vercel, open **Project → Settings → Environment Variables**,
add these two, and redeploy:

| Name | Value |
| --- | --- |
| `CANVAS_HELPER_IDS` | the item's id |
| `CANVAS_HELPER_STORE_URL` | the item's page, `https://chromewebstore.google.com/detail/…` |

From then on, in Chrome and Edge:

- The class-list step offers **Add Scheduler Helper to Chrome**.
- Once the helper is added, the step offers **Get my class list from Canvas**.
- The bookmark button stays as a folded fallback.

## Updates

Run `tools/build_extension.py store` again, bump `version` in
`extension/manifest.json`, upload the new zip under **Package**, and submit.

Keep the permissions exactly as they are. An update that adds a permission Chrome
warns about turns the helper off until each professor approves it again.
