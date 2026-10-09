"""
Everything you can configure, read once from environment variables.

Locally, put overrides in a .env file next to app.py (copy .env.example).
On Vercel, set them under Project -> Settings -> Environment Variables.
Nothing secret belongs in this file — it's going on GitHub.
"""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

try:  # python-dotenv is only a convenience for local development
    from dotenv import load_dotenv

    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass


def _env(name, default=""):
    return (os.environ.get(name) or default).strip()


def _int(name, default):
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


# Vercel sets VERCEL=1 in every deployment (production and preview alike).
IS_VERCEL = _env("VERCEL") == "1"

APP_NAME = _env("APP_NAME", "Scheduler")

# Signs the sign-in cookie. Anyone who has it can forge a sign-in, so in
# production it must be a long random string set as an environment variable.
SECRET_KEY = _env("SECRET_KEY")

# Postgres connection string — in Supabase: Connect -> Transaction pooler.
# Leave it unset locally and the app uses a SQLite file next to app.py.
DATABASE_URL = _env("DATABASE_URL")
SQLITE_PATH = os.path.join(BASE_DIR, "local.db")

# Sign-in codes are emailed by WorkOS (its free "Magic Auth" service) when
# it has an API key: WORKOS_API_KEY here, or the key the owner saves on
# /owner (stored encrypted; see sitesecrets.py). Use the production key from
# the WorkOS dashboard (it starts "sk_live_"). Its emails come from
# WORKOS_SENDER.
WORKOS_API_KEY = _env("WORKOS_API_KEY")
WORKOS_API_URL = _env("WORKOS_API_URL", "https://api.workos.com")
WORKOS_SENDER = _env("WORKOS_SENDER", "access@workos-mail.com")

# Outgoing mail through an ordinary mailbox (such as Gmail: smtp.gmail.com,
# port 587, the address, and an app password). It sends sign-in codes only
# when WorkOS isn't set up (or as its fallback), plus backups and the
# owner's usage alerts. With neither set up, locally, codes are shown on
# screen instead of emailed.
SMTP_HOST = _env("SMTP_HOST")
SMTP_PORT = _int("SMTP_PORT", 587)
SMTP_USER = _env("SMTP_USER")
SMTP_PASSWORD = _env("SMTP_PASSWORD")
SMTP_FROM = _env("SMTP_FROM") or SMTP_USER
SMTP_FROM_NAME = _env("SMTP_FROM_NAME", APP_NAME)

# The site owner: can always sign in as an instructor, and gets /owner —
# a page of site-wide totals that never shows anyone else's sheets.
OWNER_EMAIL = _env("OWNER_EMAIL").lower()

# Who may create an instructor account, by email ending ("*" = anyone).
# ".edu" matches any address ending in .edu; "myschool.edu" matches
# that domain and its subdomains.
INSTRUCTOR_EMAIL_DOMAINS = [
    d.strip().lower() for d in _env("INSTRUCTOR_EMAIL_DOMAINS", ".edu").split(",") if d.strip()
]

# Sign-in emails allowed per rolling 24 hours across the whole site. Unset
# (0), it's 1000 with WorkOS and 90 with Gmail alone, which starts refusing
# somewhere between 100 and 500 a day (signin.email_daily_limit).
EMAIL_DAILY_LIMIT = _int("EMAIL_DAILY_LIMIT", 0)

# Vercel sends "Authorization: Bearer <CRON_SECRET>" with its cron requests.
CRON_SECRET = _env("CRON_SECRET")

# Where people can reach whoever runs the site (shown on the Privacy page and
# to switched-off accounts). Optional; leave it unset to show no address.
CONTACT_EMAIL = _env("CONTACT_EMAIL").lower()

# Where the site's code is published, linked from the privacy notes so anyone
# can check what it does with a class list.
SOURCE_URL = _env("SOURCE_URL", "https://github.com/scheduler-lottery/scheduler")

# The credit at the foot of every page. Running your own copy? Set these to
# yourself (or BUILT_BY to "-" to show no credit).
BUILT_BY = _env("BUILT_BY", "Nathan Reitinger")
BUILT_BY_EMAIL = _env("BUILT_BY_EMAIL", "nathan.reitinger@law.northwestern.edu").lower()
BUILT_BY_URL = _env("BUILT_BY_URL", "https://www.law.northwestern.edu/faculty/profiles/nathanreitinger/")

# The looks a reader (or, for a whole class, its instructor) can choose.
THEMES = ("paper", "light", "solarized-light", "contrast-light", "dark", "black", "solarized-dark", "contrast-dark")
FONTS = ("mixed", "oldstyle", "sans", "readable")
DEFAULT_THEME = "paper"

# Times in downloads and emails use the instructor's own time zone, which
# their browser reports when they sign in. This is the fallback until then.
DEFAULT_TIMEZONE = _env("DEFAULT_TIMEZONE", "America/Chicago")
