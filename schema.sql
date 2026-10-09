-- Scheduler database schema.
--
-- Runs unchanged on Postgres (Supabase, in production) and SQLite (local
-- development and tests). The app applies it automatically when it starts,
-- and every statement is safe to re-run, so you can also paste the whole file
-- into Supabase's SQL editor yourself. Columns added after a table first
-- existed are added by db.MIGRATIONS.
--
-- Timestamps are ISO-8601 strings in UTC ("2026-10-08T17:30:00+00:00"), which
-- sort correctly as text. True/false flags are 0/1 integers.

CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Instructors sign in with an emailed code; there are no passwords.
-- timezone is the browser's ("America/Chicago"), for dates in downloads.
CREATE TABLE IF NOT EXISTS instructors (
    id            TEXT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    disabled      INTEGER NOT NULL DEFAULT 0,
    timezone      TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    last_login_at TEXT
);

-- One sign-up sheet per class. Its id is the code in the share link (/c/<id>).
-- lottery_seed fixes the random tie-breaks, so the same rankings always give
-- the same schedule (no re-rolling until a result looks nice).
CREATE TABLE IF NOT EXISTS sheets (
    id                 TEXT PRIMARY KEY,
    owner_id           TEXT NOT NULL REFERENCES instructors (id) ON DELETE CASCADE,
    title              TEXT NOT NULL,
    note               TEXT NOT NULL DEFAULT '',
    rank_by            TEXT NOT NULL DEFAULT '',
    capacity_per_day   INTEGER NOT NULL DEFAULT 4,
    algorithm          TEXT NOT NULL DEFAULT 'da_independent',
    bidding_open       INTEGER NOT NULL DEFAULT 1,
    allow_unlisted     INTEGER NOT NULL DEFAULT 0,
    show_preview       INTEGER NOT NULL DEFAULT 0,
    include_unranked   INTEGER NOT NULL DEFAULT 1,
    lottery_seed       INTEGER NOT NULL DEFAULT 0,
    last_algorithm     TEXT,
    scheduled_at       TEXT,
    schedule_signature TEXT,
    published_at       TEXT,
    roster_updated_at  TEXT,
    tested_at          TEXT,
    shared_at          TEXT,
    backup_emailed_at  TEXT,
    short_url          TEXT,  -- a short link made on request (is.gd or TinyURL)
    archived_at        TEXT,  -- tucked away on the dashboard (still works for students)
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sheets_by_owner ON sheets (owner_id);

-- The days (or slots) students rank, in display order.
CREATE TABLE IF NOT EXISTS sheet_days (
    sheet_id   TEXT NOT NULL REFERENCES sheets (id) ON DELETE CASCADE,
    day_key    TEXT NOT NULL,
    label      TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    day_date   TEXT,  -- YYYY-MM-DD, picked on a calendar (so always a real date)
    PRIMARY KEY (sheet_id, day_key)
);

-- The class list. Only names and emails are ever stored; every other column
-- of an uploaded file is dropped before it gets here. is_test marks the
-- pretend students an instructor uses to try the student side themselves.
CREATE TABLE IF NOT EXISTS roster (
    sheet_id     TEXT NOT NULL REFERENCES sheets (id) ON DELETE CASCADE,
    name_key     TEXT NOT NULL,
    display_name TEXT NOT NULL,
    email        TEXT NOT NULL DEFAULT '',
    is_test      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (sheet_id, name_key)
);

-- Each student's ranking. ranking/excluded_days are JSON lists of day keys;
-- comments is a JSON object of day key -> note.
CREATE TABLE IF NOT EXISTS submissions (
    sheet_id      TEXT NOT NULL REFERENCES sheets (id) ON DELETE CASCADE,
    name_key      TEXT NOT NULL,
    display_name  TEXT NOT NULL,
    ranking       TEXT NOT NULL,
    excluded_days TEXT NOT NULL,
    comments      TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (sheet_id, name_key)
);

-- The schedule, once the instructor makes it.
CREATE TABLE IF NOT EXISTS assignments (
    sheet_id     TEXT NOT NULL REFERENCES sheets (id) ON DELETE CASCADE,
    name_key     TEXT NOT NULL,
    display_name TEXT NOT NULL,
    day_key      TEXT NOT NULL,
    method       TEXT NOT NULL,
    assigned_at  TEXT NOT NULL,
    PRIMARY KEY (sheet_id, name_key)
);

-- PINs for students who sign in by name alone (no email on the class list,
-- or not on it at all). pin_hash is a keyed hash, never the PIN itself.
CREATE TABLE IF NOT EXISTS name_pins (
    sheet_id   TEXT NOT NULL REFERENCES sheets (id) ON DELETE CASCADE,
    name_key   TEXT NOT NULL,
    pin_hash   TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (sheet_id, name_key)
);

-- When an instructor resets a student's PIN, takes them off the list, or
-- deletes their ranking, any browser signed in as them before `at` is signed
-- out on its next visit.
CREATE TABLE IF NOT EXISTS student_signouts (
    sheet_id TEXT NOT NULL,
    name_key TEXT NOT NULL,
    at       TEXT NOT NULL,
    reason   TEXT NOT NULL DEFAULT '',
    email    TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (sheet_id, name_key)
);

-- Rate-limit rows: wrong PIN and code guesses (per browser and per network
-- address, stored hashed, so a guesser locks themselves out rather than the
-- real student), plus name lookups and code requests. Pruned after a day.
CREATE TABLE IF NOT EXISTS auth_failures (
    id      TEXT PRIMARY KEY,
    scope   TEXT NOT NULL,
    subject TEXT NOT NULL,
    client  TEXT NOT NULL,
    at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS auth_failures_lookup ON auth_failures (scope, subject, at);

-- Outstanding sign-in codes. scope is 'teach' for instructors or a sheet id
-- for students; subject is the instructor's email or the student's name key.
-- The previous code keeps working until it expires, so a slow email never
-- makes the newer one fail.
CREATE TABLE IF NOT EXISTS login_codes (
    scope           TEXT NOT NULL,
    subject         TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    email           TEXT NOT NULL,
    code_hash       TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    prev_hash       TEXT NOT NULL DEFAULT '',
    prev_expires_at TEXT,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_sent_at    TEXT NOT NULL,
    from_instructor INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (scope, subject)
);
-- One-click sign-in links from code emails. Each email has its own; they
-- last a day and work once. Only a keyed hash of the token is stored.
CREATE TABLE IF NOT EXISTS login_links (
    token_hash TEXT PRIMARY KEY,
    scope      TEXT NOT NULL,
    subject    TEXT NOT NULL,
    email      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

-- One row per email sent, for rate limiting. Addresses and IPs are stored as
-- keyed hashes, never in the clear, and rows are pruned after two days.
CREATE TABLE IF NOT EXISTS email_log (
    id        TEXT PRIMARY KEY,
    sent_at   TEXT NOT NULL,
    recipient TEXT NOT NULL,
    client_ip TEXT NOT NULL DEFAULT '',
    kind      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS email_log_by_time ON email_log (sent_at);

-- Saved versions of a sheet (JSON), taken automatically once a day and before
-- anything destructive. sheet_id deliberately has no foreign key, so a deleted
-- sheet's copies survive for 30 days. summary is a short description ("3
-- rankings · 24 on the class list"); content_hash skips identical copies.
CREATE TABLE IF NOT EXISTS snapshots (
    id           TEXT PRIMARY KEY,
    owner_id     TEXT NOT NULL REFERENCES instructors (id) ON DELETE CASCADE,
    sheet_id     TEXT NOT NULL,
    title        TEXT NOT NULL,
    kind         TEXT NOT NULL,
    reason       TEXT NOT NULL,
    summary      TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    data         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS snapshots_by_sheet ON snapshots (sheet_id, created_at);

-- An uploaded class list or backup file waiting for the instructor to
-- confirm it. Kept for a day at most.
CREATE TABLE IF NOT EXISTS pending_uploads (
    id         TEXT PRIMARY KEY,
    owner_id   TEXT NOT NULL REFERENCES instructors (id) ON DELETE CASCADE,
    sheet_id   TEXT NOT NULL,
    kind       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    data       TEXT NOT NULL
);

-- Unexpected errors, so they outlive Vercel's one-hour log window. Paths are
-- route patterns and email addresses are blanked out (see db.record_error).
CREATE TABLE IF NOT EXISTS error_log (
    id         TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    method     TEXT NOT NULL,
    path       TEXT NOT NULL,
    error      TEXT NOT NULL,
    detail     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS error_log_by_time ON error_log (created_at);
