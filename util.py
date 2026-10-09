"""Small helpers shared across the app: time, ids, text cleaning, and names."""

import re
import secrets
import unicodedata
from datetime import datetime, timezone

# Share-link codes avoid characters people confuse when reading one aloud
# (0/o, 1/l/i). Eight of these is about 40 bits — not guessable.
ID_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"

EMAIL_RE = re.compile(r"^[^@\s,;<>\"'()]+@[^@\s,;<>\"'()]+\.[^@\s,;<>\"'()]+$")
EMAIL_IN_TEXT = re.compile(r"[^@\s,;<>\"'()\[\]]+@[^@\s,;<>\"'()\[\]]+\.[A-Za-z]{2,}")
APOSTROPHES = "'’‘ʼ`´"
# Letters from other alphabets that look exactly like Latin ones (Cyrillic
# "а", Greek "ο"…): matched as the Latin letter, so "Sаm Lее" typed with a
# Cyrillic keyboard is still Sam Lee.
LOOKALIKES = str.maketrans({
    "а": "a", "в": "b", "е": "e", "ё": "e", "к": "k", "м": "m", "н": "h", "о": "o", "р": "p", "с": "c",
    "т": "t", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s", "ԁ": "d", "ԛ": "q", "ԝ": "w",
    "α": "a", "β": "b", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p", "τ": "t", "υ": "u",
    "χ": "x", "γ": "y",
})


def now():
    return datetime.now(timezone.utc)


def iso(dt=None):
    """UTC timestamp as text. Every timestamp in the database uses this exact
    format, which is what lets them be compared and sorted as strings."""
    return (dt or now()).astimezone(timezone.utc).isoformat(timespec="seconds")


def iso_precise(dt=None):
    """Like iso(), but to the microsecond: for sign-in and sign-out moments,
    which can fall within the same second."""
    return (dt or now()).astimezone(timezone.utc).isoformat(timespec="microseconds")


def parse_iso(value):
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def in_zone(value, zone_name):
    """A stored UTC timestamp in someone's own time zone (UTC if unknown)."""
    dt = parse_iso(value) if isinstance(value, str) else value
    try:
        from zoneinfo import ZoneInfo

        return dt.astimezone(ZoneInfo(zone_name)) if zone_name else dt
    except Exception:
        return dt


def friendly_time(value, zone_name=""):
    """'Oct 8, 3:42 PM' in the given time zone."""
    dt = in_zone(value, zone_name)
    hour = dt.strftime("%I").lstrip("0") or "12"
    return f"{dt.strftime('%b')} {dt.day}, {hour}:{dt.strftime('%M %p')}"


def new_id(length=8):
    return "".join(secrets.choice(ID_ALPHABET) for _ in range(length))


def clean_text(value, limit, keep_newlines=False):
    """Text as people meant it: Unicode normalized, invisible control and
    formatting characters (zero-width spaces, right-to-left overrides, stray
    surrogates) removed, whitespace tidied, cut to `limit` characters."""
    text = unicodedata.normalize("NFKC", value or "")
    kept = []
    for ch in text:
        category = unicodedata.category(ch)
        if ch == "\n" and keep_newlines:
            kept.append(ch)
        elif category in ("Cc", "Cf", "Cs", "Co", "Cn"):
            kept.append(" " if ch in "\t\r\n" else "")
        else:
            kept.append(ch)
    text = "".join(kept)
    if keep_newlines:
        # Keep each line's indentation (a note's nested list), tidy the rest.
        lines = []
        for line in text.split("\n"):
            indent = len(line) - len(line.lstrip(" \t"))
            lines.append(" " * min(indent, 8) + re.sub(r"[ \t]+", " ", line.strip()))
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n").rstrip()
    else:
        text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def clean_name(name):
    return clean_text(name, 60)


def tidy_name(name):
    """A typed name, cleaned, and capitalized if it was typed all in one case
    ("alex johnson" -> "Alex Johnson", "o'brien" -> "O'Brien")."""
    name = clean_name(name)
    if "@" not in name and any(c.isalpha() for c in name) and (name == name.lower() or name == name.upper()):
        name = name.title()
    return name


def plural(n, word, many=None):
    """plural(1, "ranking") -> "1 ranking"; plural(3, "ranking") -> "3 rankings"."""
    return f"{n} {word if n == 1 else (many or word + 's')}"


def fold(text):
    """For matching only: lowercase, no accents, no apostrophes, and any
    other punctuation treated as a space. "Zoë Ångström-Smith", "zoe
    angstrom smith" and "ZOE ANGSTROM SMITH" all fold the same way, as do
    "O'Brien" and "O’Brien"."""
    text = unicodedata.normalize("NFKD", clean_text(text, 500)).casefold()
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).translate(LOOKALIKES)
    text = "".join("" if ch in APOSTROPHES else ch for ch in text)
    text = re.sub(r"[^\w\s]|_", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_name(name):
    """A student's identity key within a sheet (see fold)."""
    return fold(name)


def valid_email(value):
    return bool(EMAIL_RE.match(value or ""))


def find_email(text):
    match = EMAIL_IN_TEXT.search(text or "")
    return match.group(0).strip(".").lower() if match else ""


def mask_email(email):
    """alex.johnson@school.edu -> ale*********@school.edu: enough for someone
    to recognize their own address, not enough to read anyone else's."""
    local, _, domain = (email or "").partition("@")
    if not domain or not local:
        return email
    shown = 3 if len(local) > 4 else 1
    return f"{local[:shown]}{'*' * (len(local) - shown)}@{domain}"


def slugify(text, fallback="sheet"):
    slug = re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-")
    return slug[:40] or fallback


def initials(name):
    """'Alex Johnson' -> 'AJ'. Copes with 'Last, First', generational
    suffixes (Jr., III), stray punctuation, and single-word names."""
    raw = (name or "").strip()
    if not raw:
        return "?"
    if "," in raw:
        last, _, first = raw.partition(",")
        raw = f"{first.strip()} {last.strip()}".strip()
    words = [w for w in re.split(r"\s+", raw) if w]
    suffixes = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}
    while len(words) > 1 and words[-1].lower().strip(".") in suffixes:
        words.pop()
    alpha_words = [w for w in words if w[0].isalpha()]
    if not alpha_words:
        return "?"
    if len(alpha_words) == 1:
        letters_only = re.sub(r"[^A-Za-z]", "", alpha_words[0])
        return (letters_only[:2] or alpha_words[0][0]).upper()
    return (alpha_words[0][0] + alpha_words[-1][0]).upper()


def minutes_until(when):
    """Whole minutes from now until a datetime (at least 1)."""
    seconds = (when - now()).total_seconds()
    return max(1, int(seconds // 60) + (1 if seconds % 60 else 0))
