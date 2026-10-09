# Scheduler

Fair presentation-day sign-ups for any class. Students rank the days they'd prefer, and
everyone gets a day as high on their own list as possible. When more students want a day
than it has seats, a fair random draw decides — not who clicked first. Under the hood it's
deferred acceptance (the kind of matching school-choice systems use), via the peer-reviewed
[`matching`](https://github.com/daffidwilde/matching) package.

Any professor with a school email can use it — no setup on their end:

1. **Sign in** with their school email (a 6-digit code, no password).
2. **Create a sign-up sheet**: a title, the days, seats per day, and an optional "rank by" date.
3. **Drag in the class list** from Canvas (a step-by-step Canvas guide is built in), drop an
   Excel file, or paste names.
4. **See it as a student** with one click.
5. **Share the link** (a ready-to-paste Canvas announcement is provided).
6. **Close sign-ups and make the schedule** — one button. Check it, move anyone by hand.
7. **Publish it**: each student sees their own day on the class page. Download it for your records.

Each professor sees only their own sheets. Nothing is lost by accident: a new class list is
shown for review before it replaces the old one, every delete offers an Undo, every sheet
keeps earlier versions (daily, and before anything is deleted or replaced) that can be
restored without erasing newer rankings, professors can download or email themselves a
backup any time (one is emailed when they close sign-ups), and a deleted sheet can be
brought back for 30 days. Students' rankings save as they drag, and an old browser tab can
never overwrite a newer ranking.

---

## Setting it up (one time, about 30 minutes)

You'll make four free accounts: **GitHub** (holds the code), **Supabase** (the database),
**WorkOS** (emails the sign-in codes), and **Vercel** (runs the site), plus, optionally, a
**Gmail account** (emails professors their backups, and stands in if WorkOS is down).
Nothing costs money.

### 1. Put the code on GitHub

Create a repository and push this folder to it. Public or private both work — nothing
secret lives in the code. From this folder:

```bash
git init
```
```bash
git add . && git commit -m "Scheduler"
```
```bash
gh repo create scheduler --public --source . --push
```

(Use `--private` instead if you'd rather keep the code to yourself.)

`.gitignore` keeps local data (`local.db`), secrets (`.env`), and the virtualenv out of the
repository. Nothing secret lives in the code — every password goes in Vercel's settings (step 4).

### 2. Create the database (Supabase)

1. Go to [supabase.com](https://supabase.com), sign up, and click **New project**.
   - **Name:** `scheduler`
   - **Region:** **East US (North Virginia)** — next door to Vercel's default region, so pages are fast.
   - **Database password:** click **Generate a password** and save it somewhere. (Stick to
     letters and numbers — symbols like `@` or `/` break the connection string.)
2. When the project is ready, click **Connect** at the top, find **Transaction pooler**
   (port **6543**), and copy that connection string. Replace `[YOUR-PASSWORD]` with your
   password. This is your `DATABASE_URL`.
   - Use the *pooler* string, not "Direct connection": the direct one is IPv6-only and
     won't connect from Vercel.
3. That's all — the app creates its tables the first time it starts. (Or paste
   [`schema.sql`](schema.sql) into Supabase's SQL Editor yourself.)

Supabase will say the tables have "RLS enabled, no policies." That's on purpose: it shuts
Supabase's automatic public API out of the data. The app connects directly, as the owner.

### 3. Set up WorkOS, which emails the sign-in codes

WorkOS's "Magic Auth" emails a 6-digit code from `access@workos-mail.com`; the site checks
the code itself. It's free for the first million users a month, needs no domain of your
own, and has no low daily ceiling. WorkOS keeps a record of each address it emails a code
to; the site deletes it once that person signs in, or within a day.

1. Sign up at [workos.com](https://workos.com) and switch the dashboard to the
   **Production** environment (Staging sends from `workos.dev` and is only for testing).
2. Under **Authentication**, make sure **Magic Auth** (email codes) is turned on.
3. Under **Branding**, set the display name to `Scheduler`: it's how the emails are signed.
4. Under **API Keys**, copy the secret key (it starts `sk_live_`). This is `WORKOS_API_KEY`.

Optional, a mailbox of the site's own (Gmail). It emails professors a backup when they
close sign-ups, sends you `/owner`'s usage alerts, and sends the codes itself whenever
WorkOS can't. Without it, professors download backups instead.

1. Create a new Gmail account just for this, e.g. `scheduler.signin@gmail.com`.
2. Turn on **2-Step Verification**: Google Account → Security.
3. Make an **app password** at <https://myaccount.google.com/apppasswords> (name it
   "Scheduler") and copy the 16 characters. This is `SMTP_PASSWORD`.

### 4. Deploy on Vercel

1. Go to [vercel.com](https://vercel.com) and sign up with GitHub (the free **Hobby** plan).
2. **Add New → Project**, and import your `scheduler` repository.
3. **Project Name** becomes your address: `<name>.vercel.app`. `scheduler.vercel.app` is
   taken, so use something like `scheduler-edu` → **scheduler-edu.vercel.app**.
4. Open **Environment Variables** and add these before clicking Deploy:

   | Name | Value |
   |---|---|
   | `SECRET_KEY` | a long random string — run `python3 -c "import secrets; print(secrets.token_hex(32))"` |
   | `DATABASE_URL` | the Supabase connection string from step 2 |
   | `WORKOS_API_KEY` | the WorkOS secret key from step 3 |
   | `SMTP_HOST` | `smtp.gmail.com` (only with the optional Gmail account) |
   | `SMTP_PORT` | `587` (likewise) |
   | `SMTP_USER` | the Gmail address (likewise) |
   | `SMTP_PASSWORD` | its app password (likewise) |
   | `OWNER_EMAIL` | your own school email |
   | `CRON_SECRET` | another random string (same command as `SECRET_KEY`) |

   Optional: `INSTRUCTOR_EMAIL_DOMAINS` (who can make accounts; default `.edu`),
   `EMAIL_DAILY_LIMIT` (default `1000` with WorkOS, `90` with Gmail alone), `APP_NAME`
   (default `Scheduler`), `CONTACT_EMAIL`
   (an address for questions about the site, shown on the Privacy page), and
   `DEFAULT_TIMEZONE` (default `America/Chicago`; each professor's own time zone is picked up
   from their browser when they sign in), and `BUILT_BY` / `BUILT_BY_EMAIL` (the credit at the
   foot of every page; set `BUILT_BY` to `-` to hide it).
5. Click **Deploy**.

### 5. Check it

- Open `https://<your-address>/healthz` — it should say `{"ok": true}`.
- Sign in with your school email, make a sheet, and press **See it as a student**.
- `https://<your-address>/owner` shows the site's status (totals only — see Privacy below).
- In Vercel → your project → **Settings → Cron Jobs**, you should see `/cron/daily`.

If a required setting is missing, every page shows an "Almost there" notice naming it.

---

## Running it

- **Updating:** push to GitHub; Vercel redeploys on its own.
- **Inviting professors:** send them the address. Anyone whose email ends in `.edu` can sign
  in. To let in a school with a different ending, add it to `INSTRUCTOR_EMAIL_DOMAINS`
  (comma-separated, e.g. `.edu,.ac.uk`).
- **Email budget:** the site sends at most `EMAIL_DAILY_LIMIT` sign-in emails a day (with
  Gmail alone keep it under Gmail's 100–500). Students stay signed in for 90 days, so a class
  of 50 uses about 50–60 emails once. `/owner` shows the day's count.
- **Problems:** Vercel's free plan keeps logs for only an hour, so errors are also saved to
  the database and listed on `/owner` (without any student data).
- **Abuse:** on `/owner`, switch an account off by its email address. That instructor can't
  sign in and their students' links stop working; nothing is deleted.

### The daily job

Vercel calls `/cron/daily` once a day (see `vercel.json`). It takes a restore point of every
sheet that changed, clears out expired codes and old logs, and — just by running — keeps
Supabase's free plan from pausing the database after a week of no activity.

---

## Privacy and security

- **Each professor sees only their own sheets.** Every sheet page checks ownership and answers
  "not found" otherwise. `/owner` shows totals only — no names, no sheets, no students. (As the
  person running the database you *could* read it directly in Supabase; the site gives you no
  way to, and the privacy page tells users so.)
- **Class lists keep only names and emails.** Every other column of a Canvas export — grades,
  activity, IDs — is discarded in memory during the upload and never saved.
- **Sign-in** is by emailed one-time code. Codes are stored as keyed hashes, expire in 30
  minutes, allow 5 guesses per browser, and are rate-limited per address, per network, per
  class, and site-wide. A student's email also has a one-click link (a long random token,
  stored hashed, good once within 24 hours; opening it only shows a "Sign me in" button, so
  email scanners can't use it up). Because any code email a student received still works,
  someone typing classmates' names can't lock them out, and a student's first code of the day
  is never held back by their class's limits. Codes after the first stop while the site is
  down to its last third of the day's emails, so one busy class can't use up everyone's.
  Students with no email on the class list choose a PIN (also stored as a keyed hash); wrong
  guesses pause the guessing browser, not the real student. If a student can't get an email,
  the professor's page lists them with the reason, and the backup is the professor's own
  email: "Sign-in link" opens a ready-to-send message (with a one-click link for that
  student) in the professor's own Outlook, Gmail or mail app, and "Send sign-in links from
  your own email" does the same for the whole class, one message per student (a link in a
  group email would let anyone in it sign in as anyone). Which service opens is guessed from
  the professor's address (well-known providers by name, others from the domain's MX/SPF
  records over DNS-over-HTTPS, remembered per domain) and can be changed. The student's page
  offers a ready-made "Ask your instructor for a sign-in link" email. Resetting a PIN, taking a student off the
  list, or deleting their ranking while sign-ups are open signs that student out on every
  device.
- **Only the class list, by default.** New sheets let in only names on the class list, and
  students can't see each other's days unless the professor turns that on. A student who
  types their name differently ("jose hernandez", "Lee, Sam", their email) is matched to
  their own entry, and near misses get "Did you mean…?" instead of a duplicate.
- **The web layer:** CSRF tokens on every form, a strict Content-Security-Policy (no inline
  scripts at all), `HttpOnly`/`Secure`/`SameSite` cookies, no caching of pages, and HTTPS.
- **The database:** connections require TLS, row-level security closes Supabase's public API,
  and database error messages never include the values involved.

## Free-plan limits worth knowing

- **Vercel Hobby:** 1,000,000 requests and 4 hours of compute a month — far more than this
  needs. Hitting a limit pauses the site until the month resets rather than charging you.
  Hobby is for non-commercial personal use.
- **Supabase free:** 500 MB (enough for thousands of classes); pauses after a week idle (the
  daily job prevents that); no automatic database backups (each sheet's restore points and
  backup downloads cover that).
- **WorkOS:** free for the first million users a month. (Firebase's free email sign-in
  allows only 5 emails a day, and services like Resend or Brevo need a domain of your own.)
- **Gmail** (optional): a few hundred emails a day.

---

## Working on the code

```bash
python3 -m venv .venv
```
```bash
.venv/bin/pip install -r requirements-dev.txt
```
```bash
.venv/bin/python app.py
```

Then open <http://127.0.0.1:5050>. With no settings, it uses a local SQLite file
(`local.db`) and shows sign-in codes on screen instead of emailing them. Copy `.env.example`
to `.env` to try real settings locally.

Run the tests (they also run against Postgres if `TEST_PG_BIN` points at a folder with
`initdb` and `pg_ctl`):

```bash
.venv/bin/python -m pytest
```

| File | What it does |
|---|---|
| `app.py` | Entry point Vercel runs; security headers, error pages, public pages, the daily job |
| `teach.py` | Instructor pages: sign-in, dashboard, everything you do to a sheet |
| `student.py` | Student pages for one sheet, under `/c/<sheet id>` |
| `owner.py` | The site owner's status page |
| `sheets.py` | Sheet data, backups, restore points |
| `roster.py` | Reading class lists from Canvas exports, spreadsheets, and pasted text |
| `signin.py` | Emailed codes, PINs, and rate limits |
| `auth.py` | Who's signed in; ending a student's sign-in from the professor's side |
| `matching_engine.py` | The assignment algorithms (can't-do days, students who didn't rank, a fixed random draw per sheet) |
| `db.py`, `schema.sql` | Database access and tables (same SQL on SQLite and Postgres) |
| `maintenance.py` | The daily job |
| `settings.py` | Every setting, read from environment variables |
