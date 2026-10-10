# Scheduler Helper for Safari

Scheduler Helper is the same code in Safari and Chrome. The Safari build adds two
things:

- an **Allow** page, because Safari makes each person allow each website;
- a toolbar button that opens that page.

Build it:

```bash
.venv/bin/python tools/build_extension.py safari
```

That makes the `dist/scheduler-helper-safari/` folder and `dist/scheduler-helper-safari.zip`.

## 1. Try it in your own Safari first (free, about 10 minutes)

Safari 18.4 or later can load it without Xcode or an Apple account.

1. Turn on developer features: Safari menu → Settings → Advanced → tick **Show
   features for web developers**.
2. Load the helper: Settings → **Developer** tab → **Add Temporary Extension…** →
   choose the `dist/scheduler-helper-safari` folder → **Select**. Approve the
   "unsigned extension" prompt with your password or Touch ID.
3. The Scheduler Helper page opens. Click **Allow**.
4. On that page, copy the helper's **ID** from the small print. It looks like
   `something (ABCDE12345)`.
5. In Vercel, open **Settings → Environment Variables** and add that ID to
   `CANVAS_HELPER_IDS`, after a comma if the Chrome ID is already there. Redeploy.
6. In Safari, open a sheet's Class list → Canvas. The button should read **Get my
   class list from Canvas**. Click it.

Safari removes a temporary extension when it quits, so after a restart repeat step 2.

## 2. Publish it (Apple Developer account, 99 USD a year)

Apple's App Store Connect packages a web extension into a Mac app on the web; no Xcode
is needed.

1. Join the Apple Developer Program at <https://developer.apple.com/programs/enroll/>.
   Identity checks usually take a day or two. Universities can ask about a fee waiver.
2. In App Store Connect, go to **Apps → + → New App**:
   - **Platform:** macOS
   - **Name:** Scheduler Helper
   - **Bundle ID:** for example `app.scheduler.helper`
   - **SKU:** anything
3. On the app's **Xcode Cloud** tab, find **Safari Web Extension Packager**, click
   **Upload**, and choose `dist/scheduler-helper-safari.zip`. Packaging takes minutes.
4. Fill in the listing:
   - **Description:** reuse the Chrome text in `dist/store/listing.txt`.
   - **Privacy policy:** <https://scheduler-lottery.vercel.app/privacy#canvas>
   - **App Privacy answers:** it handles names and email addresses, it doesn't
     track, and it collects nothing for itself.
5. Submit for review. Most reviews finish within a day or two.
6. Once it's out, add these in Vercel and redeploy:
   - `CANVAS_HELPER_SAFARI_URL`: its Mac App Store page.
   - `CANVAS_HELPER_IDS`: add its ID (shown on its Allow page, see step 1.4).

From then on, Safari users see **Add Scheduler Helper to Safari** with these steps:

1. **Get**, then **Install**, then **Open**.
2. Open Safari's settings and tick the helper.
3. Click **Allow** on the page that opens.

## Updates

Raise `version` in `extension/manifest.json`, then rebuild and upload. Apple requires a
higher version each time.
