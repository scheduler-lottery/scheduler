# Scheduler Helper for Safari

Scheduler Helper is the same code in Safari as in Chrome. The Safari build adds:

- an **Allow** page, because Safari asks each person about each website the helper
  touches (Canvas, and Scheduler itself, which the build names among its sites);
- a toolbar button that opens that page;
- 512- and 1024-pixel icons, from which Apple makes the app and its App Store icon.

Build it:

```bash
.venv/bin/python tools/build_extension.py safari
```

That makes `dist/scheduler-helper-safari/` (the extension, to try in your own Safari)
and `dist/scheduler-helper-safari.zip` (the same, zipped, for Apple). The upload folder
`dist/safari-upload/` holds the zip, the 1024-pixel icon, screenshots and `listing.txt`
(every App Store field, ready to paste).

Apple only lets people install a Safari extension that comes inside an app signed by an
Apple developer account, so there's no free way to hand it out. You don't need Xcode or
a Mac build, though: App Store Connect packages the zip into the app on its website.

## 1. Try it in your own Safari first (free, about 10 minutes)

Safari 18.4 or later can load it without an Apple account.

1. Safari menu → **Settings** → **Advanced** → tick **Show features for web developers**.
2. **Settings** → **Developer** tab → **Add Temporary Extension…** → choose the
   `dist/scheduler-helper-safari` folder → **Select**. Approve with your password or
   Touch ID.
3. The Scheduler Helper page opens. Click **Allow**. Safari asks about Canvas and about
   Scheduler: allow both (**Always Allow on These Websites**).
4. On that page, copy the helper's **ID** from the small print (it looks like
   `something (ABCDE12345)`).
5. In Vercel, **Settings → Environment Variables**: add that ID to `CANVAS_HELPER_IDS`,
   after a comma if the Chrome ID is already there. Redeploy.
6. In Safari, open a sign-up sheet's **Your students** step → **Yes, my class is in
   Canvas**. The button reads **Get my class list from Canvas**. Click it, sign in to
   Canvas if it asks, and your class list comes back to the page.

Safari forgets a temporary extension when it quits, so after a restart repeat step 2.

## 2. Publish it (Apple Developer Program, 99 USD a year)

Apple's guide: [Packaging and distributing Safari Web Extensions with App Store
Connect](https://developer.apple.com/documentation/safariservices/packaging-and-distributing-safari-web-extensions-with-app-store-connect).

1. Join the Apple Developer Program: <https://developer.apple.com/programs/enroll/>.
   Identity checks usually take a day or two.
2. In [App Store Connect](https://appstoreconnect.apple.com/), **Apps → + → New App**:
   - **Platforms:** macOS. (Scheduler doesn't offer the helper on phones, so iOS isn't
     needed.)
   - **Name:** Scheduler Helper
   - **Primary language:** English (U.S.)
   - **Bundle ID:** register a new one, for example `app.schedulerlottery.helper`.
   - **SKU:** anything, for example `scheduler-helper`.
   - **User Access:** Full Access.
3. On the app's **Xcode Cloud** tab, under **Safari Web Extension Packager**, click
   **Upload** and choose `dist/safari-upload/scheduler-helper-safari.zip`. (Some accounts
   show the Xcode Cloud page under **Users and Access**.) Packaging takes minutes; it
   shows on the **Builds** page.
4. Optional, before the public release: on the **TestFlight** tab, add yourself as a
   tester and install it with Apple's TestFlight app. Then do steps 1.3 to 1.6 with that
   copy, so the live site knows its ID.
5. Fill in the app's page from `dist/safari-upload/listing.txt`.
6. Pick the build, then **Add for Review → Submit**. Reviews usually take a day or two.
7. Once it's out, in Vercel (then redeploy):
   - `CANVAS_HELPER_SAFARI_URL`: its Mac App Store address.
   - `CANVAS_HELPER_IDS`: add its ID (shown on its Allow page, as in step 1.4), after a
     comma.

From then on, professors using Safari see **Add Scheduler Helper to Safari**, with these
steps on the page:

1. **Get**, then **Install**, then **Open**.
2. Open Safari's settings and tick **Scheduler Helper**.
3. Click **Allow** on the page that opens.

## Updates

Raise `version` in `extension/manifest.json`, rebuild, and upload the new zip the same
way (step 2.3). Apple requires a higher version each time. The first upload is version
**1.0.2**, as it is now.
