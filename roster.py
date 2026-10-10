"""
Reading a class list out of whatever an instructor drops in or pastes.

Handles Canvas exports (the New Analytics "Class Roster" report, the student
analytics export, the gradebook export with "Last, First" names), Excel
workbooks (.xlsx) as they are, any CSV or tab-separated file with a name
column (English, French, Spanish, or German headings), and plain pasted
lists — one student per line, a name and maybe an email, in any order.

Only names and emails are kept. Every other column (grades, activity, IDs)
is dropped right here, in memory, and never stored.
"""

import codecs
import csv
import io
import json
import re
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from xml.etree import ElementTree

from util import clean_name, find_email, fold, normalize_name, valid_email

MAX_ROWS = 3000
MAX_ZIPPED_BYTES = 5 * 1024 * 1024  # each file inside a ZIP, unpacked

# Header names, after folding (lowercase, no accents, punctuation as spaces).
FULL_NAME_HEADERS = (
    "full name", "name", "student name", "display name", "preferred name", "user name",
    "nom complet", "nombre completo", "vollstandiger name", "nome completo", "student",
)
LAST_FIRST_HEADERS = ("sortable name",)  # Canvas writes these "Last, First"
FIRST_HEADERS = ("first name", "given name", "firstname", "first", "prenom", "nombre", "vorname", "nome")
LAST_HEADERS = (
    "last name", "surname", "family name", "lastname", "last", "nom", "nom de famille",
    "apellido", "apellidos", "nachname", "cognome",
)
EMAIL_HEADERS = (
    "email", "email address", "e mail", "e mail address", "school email", "student email",
    "primary email", "courriel", "adresse electronique", "adresse e mail", "correo",
    "correo electronico", "mail", "e mail adresse",
)
# Some schools' Canvas "login ID" is the student's email address.
LOGIN_HEADERS = ("sis login id", "login id", "login", "username", "netid", "net id")
# A People-page copy lists teachers and TAs too.
ROLE_HEADERS = ("role", "roles", "enrollment type", "enrollment role", "type")
ROLE_WORDS = {"student", "students", "teacher", "ta", "teaching assistant", "designer", "observer"}
NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

SAVE_AS_CSV = (
    "In Excel: File → Save As → choose “CSV UTF-8 (Comma delimited)”, then drop that file here. "
    "In Google Sheets: File → Download → “Comma-separated values”."
)
PASTE_INSTEAD = "Or copy the names (and emails) and paste them into “Or paste names” below."
SECTION_HEADERS = ("section", "section name", "course", "course name", "course section", "class", "course title")
NO_NAME_COLUMN = (
    "We couldn't find a column of names. The first row should have headings, with one called "
    "“Name” (or “First name” and “Last name”), plus “Email” if you have it. Or paste the names "
    "instead — one student per line."
)


@dataclass
class RosterResult:
    students: list = field(default_factory=list)  # [{name_key, display_name, email}]
    error: str = ""
    name_column: str = ""
    email_column: str = ""
    ignored_columns: list = field(default_factory=list)
    skipped: int = 0  # rows with no name at all
    canvas_rows: int = 0  # Canvas's "Points Possible" and "Test Student" rows
    email_only: list = field(default_factory=list)  # added by email alone: each types their name later
    non_students: int = 0  # teachers/TAs dropped by a role column
    duplicates: list = field(default_factory=list)  # [(display name, dropped email)]: one name, can't tell apart
    numbered: list = field(default_factory=list)  # ["Alex Kim (2)"]: two people, one name, told apart by email
    same_name: list = field(default_factory=list)  # (while reading) the second of two people with one name
    repeated: list = field(default_factory=list)  # one person listed twice
    bad_emails: list = field(default_factory=list)  # [(display name, the incomplete address)]
    flipped: bool = False  # names arrived "Last, First" and were turned around
    garbled: list = field(default_factory=list)  # names with letters lost to an encoding
    suspicious: list = field(default_factory=list)  # [(name, email, likely domain)]
    sections: Counter = field(default_factory=Counter)  # values of a Section/Course column
    tab: int = 1  # which worksheet of an Excel file the list came from

    @property
    def with_email(self):
        return sum(1 for s in self.students if s["email"])


# ---------------------------------------------------------------------------
# Getting rows out of the bytes
# ---------------------------------------------------------------------------
def _letters_score(text):
    """How much decoded text looks like real names: accented letters count
    for, odd symbols (what a wrong encoding produces) count against."""
    good = sum(1 for ch in text if ch.isalpha())
    bad = sum(1 for ch in text if unicodedata.category(ch) in ("Cc", "Co", "Cn", "So", "Sm")
              and ch not in "\n\r\t")
    return good - 5 * bad


def decode_bytes(data):
    if data.startswith(codecs.BOM_UTF16_LE) or data.startswith(codecs.BOM_UTF16_BE):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    # Excel on Windows writes cp1252; older Macs write Mac Roman. Pick
    # whichever reads more like names.
    candidates = []
    for encoding in ("cp1252", "mac_roman"):
        try:
            text = data.decode(encoding)
            candidates.append((_letters_score(text), encoding == "cp1252", text))
        except UnicodeDecodeError:
            continue
    if candidates:
        return max(candidates)[2]
    return data.decode("latin-1")


def _xlsx_sheets(data):
    """Every worksheet of an .xlsx file (in order, at most ten), each as a
    list of rows of text."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            root = ElementTree.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", ns):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t")))
        found = [n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n)]
        found.sort(key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
        sheets = []
        for name in found[:10]:
            info = z.getinfo(name)
            if info.file_size > 20 * 1024 * 1024:
                continue
            sheets.append(_xlsx_rows(ElementTree.fromstring(z.read(info)), shared, ns))
    return sheets


def _xlsx_rows(root, shared, ns):
    rows = []
    for row in root.iter(f"{{{ns['m']}}}row"):
        cells = {}
        for c in row.findall("m:c", ns):
            ref = c.get("r") or ""
            letters = re.match(r"[A-Z]+", ref)
            col = 0
            for ch in (letters.group(0) if letters else ""):
                col = col * 26 + (ord(ch) - 64)
            kind = c.get("t")
            if kind == "inlineStr":
                value = "".join(t.text or "" for t in c.iter(f"{{{ns['m']}}}t"))
            else:
                v = c.find("m:v", ns)
                value = v.text if v is not None and v.text else ""
                if kind == "s" and value.isdigit() and int(value) < len(shared):
                    value = shared[int(value)]
            cells[(col or len(cells) + 1) - 1] = value
        if cells:
            width = max(cells) + 1
            rows.append([cells.get(i, "") for i in range(width)])
    return rows


def _norm_header(value):
    return fold(value)


def _guess_delimiter(lines):
    """Tabs win whenever there are any (names often contain commas — "Lee,
    Sam" — but never tabs); otherwise the more common of comma and semicolon
    in the first line."""
    if any("\t" in line for line in lines[:10]):
        return "\t"
    first = lines[0] if lines else ""
    return ";" if first.count(";") > first.count(",") else ","


def _rows_from_text(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line for line in text.split("\n") if line.strip()]
    if not lines:
        return [], []
    if not any(d in lines[0] for d in "\t,;"):
        # One column ("Full name", or a plain list of names): a comma further
        # down belongs to the name ("John Smith, Jr."), not a new column.
        return [next(csv.reader([line], delimiter="\x1f"), [""])[:1] or [""] for line in lines], lines
    delimiter = _guess_delimiter(lines)
    try:
        rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter))
    except csv.Error:
        rows = [line.split(delimiter) for line in lines]
    return rows, lines


# ---------------------------------------------------------------------------
# Finding the columns
# ---------------------------------------------------------------------------
def _find(headers, candidates):
    for candidate in candidates:
        if candidate in headers:
            return headers.index(candidate)
    return None


def _name_columns(headers):
    first, last = _find(headers, FIRST_HEADERS), _find(headers, LAST_HEADERS)
    full = _find(headers, FULL_NAME_HEADERS)
    if full is not None and headers[full] != "student":
        return "full", (full,)
    if first is not None and last is not None and first != last:
        return "first_last", (first, last)
    last_first = _find(headers, LAST_FIRST_HEADERS)
    if full is not None:  # Canvas gradebook "Student": "Last, First"
        return "full", (full,)
    if last_first is not None:
        return "last_first", (last_first,)
    return None


def _cell(row, index):
    return str(row[index]).strip() if index is not None and index < len(row) else ""


def _mostly(rows, index, test):
    values = [_cell(r, index) for r in rows if _cell(r, index)]
    return bool(values) and sum(1 for v in values if test(v)) >= len(values) / 2


def _email_column(headers, rows):
    exact = _find(headers, EMAIL_HEADERS)
    if exact is not None:
        return exact
    for i, h in enumerate(headers):
        if "email" in h.split() or "e mail" in h or "courriel" in h or "correo" in h:
            return i
    for candidate in LOGIN_HEADERS:
        if candidate in headers:
            i = headers.index(candidate)
            if _mostly(rows, i, lambda v: "@" in v):
                return i
    return None


def _role_column(headers, rows):
    index = _find(headers, ROLE_HEADERS)
    if index is not None and _mostly(rows, index, lambda v: "student" in v.lower()):
        return index
    return None


def flip_last_first(raw):
    """'Johnson, Alex' -> 'Alex Johnson'."""
    raw = raw.strip()
    if "," in raw:
        last, _, first = raw.partition(",")
        return f"{first.strip()} {last.strip()}".strip() or raw
    return raw


def _is_last_first(value):
    """True for "Last, First" — but not "Smith, Jr." or "Smith, III", where
    the comma just sets off a suffix and flipping would mangle the name."""
    if value.count(",") != 1:
        return False
    after = value.split(",", 1)[1].strip().lower().strip(".")
    return bool(after) and after not in NAME_SUFFIXES and "@" not in after


def _looks_like_name(value):
    return any(c.isalpha() for c in value) and value.strip().lower() not in ROLE_WORDS and "@" not in value


def _is_canvas_filler(name):
    lowered = normalize_name(name)
    return lowered.startswith("points possible") or lowered.startswith("test student")


def _people_page_columns(rows):
    """For a tab-separated copy of a table with no heading row (e.g. Canvas's
    People page): names in the first column that isn't emails, emails
    wherever they are, and a role column if there is one."""
    width = max(len(r) for r in rows)
    emails = next((i for i in range(width) if _mostly(rows, i, lambda v: "@" in v)), None)
    names = next((i for i in range(width) if i != emails and _mostly(rows, i, _looks_like_name)), None)
    roles = next(
        (i for i in range(width) if i not in (emails, names)
         and _mostly(rows, i, lambda v: v.strip().lower() in ROLE_WORDS)),
        None,
    )
    if roles is not None and not _mostly(rows, roles, lambda v: "student" in v.lower()):
        roles = None
    return names, emails, roles


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def _not_a_list(data):
    """A plain-words reason why these bytes can't be a class list, or ''."""
    head = data[:300000]
    if data.startswith(b"\xd0\xcf\x11\xe0"):
        if "EncryptedPackage".encode("utf-16-le") in head:
            return ("That Excel file is password-protected, so we can't open it. Remove the password in Excel "
                    "(File → Info → Protect Workbook), or save it as CSV. " + PASTE_INSTEAD)
        if "WordDocument".encode("utf-16-le") in head:
            return "That's a Word document, not a spreadsheet. " + PASTE_INSTEAD
        return f"That's an older Excel file (.xls), which we can't read. {SAVE_AS_CSV} {PASTE_INSTEAD}"
    if data.startswith(b"%PDF"):
        return ("That's a PDF, not a class list. In Canvas, go to New Analytics → Reports → Class Roster and "
                "drop the file it downloads (the sheet's page has a “Where do I find this in Canvas?” guide). "
                + PASTE_INSTEAD)
    if (data.startswith(b"\x89PNG") or data.startswith(b"\xff\xd8\xff") or data.startswith(b"GIF8")
            or data[4:12] in (b"ftypheic", b"ftypmif1", b"ftypheix") or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")):
        return "That's a picture, not a list we can read. " + PASTE_INSTEAD
    return ""


def _parse_zipped(data, names):
    """A ZIP of CSV files (Canvas's Course Analytics downloads its student
    list that way): the class list in it, from the file with the most
    students with emails. Each file is read only up to MAX_ZIPPED_BYTES."""
    found = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in names[:20]:
            info = z.getinfo(name)
            if info.file_size > MAX_ZIPPED_BYTES:
                continue
            with z.open(info) as handle:
                inner = handle.read(MAX_ZIPPED_BYTES + 1)
            if len(inner) > MAX_ZIPPED_BYTES or inner.startswith(b"PK"):
                continue
            result = parse_roster(inner)
            # Only a real list of people: Course Analytics' other files (grades
            # by assignment) would otherwise read as a list of "names".
            if not result.error and result.students and (result.name_column or result.with_email):
                found.append(result)
    if not found:
        return RosterResult(error=(
            "That ZIP file has no class list in it. In Canvas's Course Analytics, open the Students tab, then "
            "click its download button. " + PASTE_INSTEAD
        ))
    return max(found, key=lambda r: (r.with_email, bool(r.name_column), len(r.students)))


def parse_roster(data):
    """Parse uploaded or pasted bytes into a RosterResult. Never raises on
    bad input — problems come back in .error, worded for the instructor."""
    reason = _not_a_list(data)
    if reason:
        return RosterResult(error=reason)
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
        except zipfile.BadZipFile:
            names = []
        if any(n.startswith("word/") for n in names):
            return RosterResult(error="That's a Word document, not a spreadsheet. " + PASTE_INSTEAD)
        if any(n.startswith("Index/") or n.endswith(".iwa") for n in names):
            return RosterResult(error=(
                "That's an Apple Numbers file. In Numbers, choose File → Export To → CSV…, then drop that file "
                "here. " + PASTE_INSTEAD
            ))
        tables = [n for n in names if n.lower().endswith((".csv", ".tsv", ".txt")) and not n.startswith("__MACOSX/")]
        if tables and not any(n.startswith("xl/") for n in names):
            return _parse_zipped(data, tables)
        try:
            sheets = _xlsx_sheets(data)
        except (zipfile.BadZipFile, ElementTree.ParseError, KeyError, ValueError):
            sheets = []
        sheets = [[[str(c) for c in r] for r in rows if any(str(c).strip() for c in r)] for rows in sheets]
        if not any(sheets):
            return RosterResult(error=f"We couldn't read that file as a spreadsheet. {SAVE_AS_CSV} {PASTE_INSTEAD}")
        # The class list might not be on the first tab. Prefer a tab with a
        # real "Name" heading over one that only looks like a list of names
        # (a notes tab, say), then the one with the most names.
        parsed = []
        for number, rows in enumerate(sheets, start=1):
            if rows:
                result = _parse_rows(rows, None)
                result.tab = number
                parsed.append(result)
        usable = [r for r in parsed if not r.error and r.students]
        if not usable:
            return parsed[0]
        return max(usable, key=lambda r: (bool(r.name_column), len(r.students), -r.tab))
    text = decode_bytes(data)
    if "\x00" in text or sum(1 for ch in text[:2000] if unicodedata.category(ch) == "Cc"
                             and ch not in "\n\r\t") > 20:
        return RosterResult(error=f"That doesn't look like a class list. {SAVE_AS_CSV} {PASTE_INSTEAD}")
    from_canvas = _canvas_api_people(text)
    if isinstance(from_canvas, str):
        return RosterResult(error=from_canvas)
    if from_canvas is not None:
        return parse_people(from_canvas) if from_canvas else RosterResult(
            error="That's from Canvas, but it has no students in it. Is it the right course's list?")
    rows, lines = _rows_from_text(text)
    return _parse_rows(rows, lines)


CANVAS_SIGNED_OUT = ("That's Canvas saying you're signed out. Sign in to Canvas, open the page again, then copy and "
                     "paste it here.")
CANVAS_NOT_ALLOWED = "That's Canvas saying you can't see that course's class list. Is it a course you teach?"


def _canvas_api_people(text):
    """A copy of what Canvas's API shows in the browser (a course's users, as
    JSON; once it started "while(1);", and a copy may carry a little text
    around it): its people as (name, email) pairs, keeping nothing else. A
    message, if it's Canvas saying no; None if it isn't from Canvas."""
    body = text.strip()
    if body.startswith("while(1);"):
        body = body[len("while(1);"):].strip()
    if body.startswith("{") and '"errors"' in body[:500]:
        lowered = body[:500].lower()
        if "authorization required" in lowered or "unauthenticated" in lowered:
            return CANVAS_SIGNED_OUT
        if "not authorized" in lowered or "unauthorized" in lowered:
            return CANVAS_NOT_ALLOWED
        return None
    start, end = body.find("["), body.rfind("]")
    if start < 0 or end < start or start > 200:
        return None
    body = body[start:end + 1]
    if body.replace(" ", "") == "[]":
        return []
    if '"name"' not in body[:2000]:
        return None
    try:
        people = json.loads(body)
    except ValueError:
        return None
    if not isinstance(people, list) or not all(isinstance(p, dict) for p in people):
        return None
    out = []
    for person in people:
        name = " ".join(str(person.get("name") or person.get("sortable_name") or "").split())
        login = str(person.get("login_id") or "")
        email = str(person.get("email") or person.get("primary_email") or (login if "@" in login else "")).strip()
        if name or email:
            out.append((name, email))
    return out


def parse_people(pairs):
    """Students whose names and emails came already apart (a list from
    Canvas): read as a table headed Name and Email, so a name written
    "Kim, Alex" is turned round, never split at its comma."""
    result = RosterResult()
    rows = [[" ".join(str(name or "").split()), str(email or "").strip()] for name, email in pairs]
    if not rows:
        result.error = "That list is empty."
        return result
    _parse_table(["Name", "Email"], rows, ("full", (0,)), result)
    result.name_column = result.email_column = ""
    if result.error:
        return result
    _number_same_names(result)
    _check_emails(result)
    if not result.students:
        result.error = "We found where the names should be, but no student names under it."
    return result


def _parse_rows(rows, lines):
    result = RosterResult()
    if not rows:
        result.error = "That file is empty."
        return result

    # The heading row is the first of the opening few rows that names a name
    # column — and has names underneath it (a People-page paste has
    # "Student" as a role, not a heading).
    header = None
    for i, candidate in enumerate(rows[:10]):
        if any("@" in c for c in candidate):
            continue
        found = _name_columns([_norm_header(h) for h in candidate])
        below = rows[i + 1:]
        if found and (len(rows) == 1 or (below and _mostly(below, found[1][0], _looks_like_name))):
            header, data_rows, name_cols = candidate, below, found
            break

    if header is None:
        # Pasted or plain-text lines are read one at a time ("Lee, Sam;
        # sam@x.edu"); only a tab-separated copy of a table (Canvas's People
        # page) or a spreadsheet is read as columns.
        widths = Counter(len(r) for r in rows[:30])
        width, how_many = widths.most_common(1)[0]
        table_like = len(rows) >= 2 and width >= 3 and how_many >= 0.8 * min(len(rows), 30)
        if lines is not None and not table_like and not any("\t" in line for line in lines[:10]):
            _parse_lines(lines, result)
        else:
            _parse_table_without_header(rows, result)
    else:
        _parse_table(header, data_rows, name_cols, result)
    if result.error:
        return result
    _number_same_names(result)
    _check_emails(result)
    if not result.students:
        result.error = (
            "Everyone in that list is a teacher or TA — no students found." if result.non_students
            else "We found where the names should be, but no student names under it."
        )
    return result


def name_from_email(email):
    """A stand-in name, until the student types their own: "alex.johnson@x.edu"
    -> "Alex Johnson", "ajohnson3@x.edu" -> "Ajohnson"."""
    local = (email or "").partition("@")[0]
    words = [w for w in re.split(r"[._\-+\d\s]+", local) if w]
    return clean_name(" ".join(w[:1].upper() + w[1:].lower() for w in words)) or clean_name(local)


def _add(result, raw_name, email, seen, pending=False):
    display_name = clean_name(raw_name)
    email = (email or "").strip()
    if not display_name:
        found = find_email(email) if email else ""
        if found and valid_email(found.lower()):
            # An email and no name: they're on the list, and they type
            # their own name the first time they sign in.
            result.email_only.append(found.lower())
            return _add(result, name_from_email(found), found, seen, pending=True)
        if email:
            result.bad_emails.append(("A line", email))
        else:
            result.skipped += 1
        return
    if _is_canvas_filler(display_name):
        result.canvas_rows += 1
        return
    if "@" in display_name:
        found = find_email(display_name)
        display_name = clean_name(display_name.replace(found, " ")) if found else display_name
        email = email or found
        if not display_name or "@" in display_name:
            return _add(result, "", email or display_name, seen)
    email = email.lower()
    if email.startswith("mailto:"):
        email = email[7:]
    if email and not valid_email(email):
        result.bad_emails.append((display_name, email))
        email = ""
    if any(0x80 <= ord(ch) <= 0x9F or ch == "�" for ch in display_name):
        result.garbled.append(display_name)
    key = normalize_name(display_name)
    if not key:
        result.skipped += 1
        return
    if key in seen:
        first = next(st for st in result.students if st["name_key"] == key)
        if not email or email == first["email"] or any(e["email"] == email for e in result.same_name
                                                         if e["name_key"] == key):
            result.repeated.append(display_name)  # the same person listed twice (two sections, say)
        elif not first["email"]:
            first["email"] = email
            result.repeated.append(display_name)
        else:
            # Two students with one name: both stay, told apart by their emails.
            result.same_name.append({"name_key": key, "display_name": display_name, "email": email,
                                     "name_pending": 1 if pending else 0})
        return
    seen.add(key)
    result.students.append({"name_key": key, "display_name": display_name, "email": email,
                            "name_pending": 1 if pending else 0})


SAME_NAME_SUFFIX = re.compile(r" \((\d+)\)$")


def base_key(name_key):
    """"alex kim 2" (the second Alex Kim) -> "alex kim"."""
    return re.sub(r" \d+$", "", name_key)


def numbered(display_name, n):
    return display_name if n == 1 else f"{SAME_NAME_SUFFIX.sub('', display_name)} ({n})"


def _number_same_names(result):
    """Two students with one name both stay on the list: "Alex Kim" and
    "Alex Kim (2)", numbered in email order so the same file always numbers
    them the same way. Each signs in with a code sent to their own email."""
    if not result.same_name:
        return
    final = []
    for s in result.students:
        group = [s] + [e for e in result.same_name if e["name_key"] == s["name_key"]]
        if len(group) == 1:
            final.append(s)
            continue
        group.sort(key=lambda e: e["email"])
        # One spelling for all of them, preferring one that isn't all lower
        # or upper case.
        spelling = max((e["display_name"] for e in group),
                       key=lambda name: name not in (name.lower(), name.upper()))
        for n, e in enumerate(group, start=1):
            name = numbered(spelling, n)
            final.append({"name_key": normalize_name(name), "display_name": name, "email": e["email"],
                          "name_pending": e.get("name_pending", 0)})
            if n > 1:
                result.numbered.append(name)
    result.students = final
    result.same_name = []


def _parse_table(header, data_rows, name_cols, result):
    headers = [_norm_header(h) for h in header]
    kind, indexes = name_cols
    email_index = _email_column(headers, data_rows)
    role_index = _role_column(headers, data_rows)
    section_index = _find(headers, SECTION_HEADERS)
    used = set(indexes) | {i for i in (email_index, role_index) if i is not None}
    result.name_column = " + ".join(str(header[i]).strip() for i in indexes)
    result.email_column = str(header[email_index]).strip() if email_index is not None else ""
    result.ignored_columns = [str(h).strip() for i, h in enumerate(header) if i not in used and str(h).strip()]
    if len(data_rows) > MAX_ROWS:
        result.error = f"That has more than {MAX_ROWS:,} rows — is it the right file?"
        return
    if kind == "full" and _mostly(data_rows, indexes[0], _is_last_first):
        kind = "last_first"
    result.flipped = kind == "last_first" and any("," in _cell(r, indexes[0]) for r in data_rows)
    seen = set()
    for r in data_rows:
        if role_index is not None and "student" not in _cell(r, role_index).lower():
            result.non_students += 1
            continue
        if kind == "first_last":
            raw = f"{_cell(r, indexes[0])} {_cell(r, indexes[1])}".strip()
        elif kind == "last_first":
            value = _cell(r, indexes[0])
            raw = flip_last_first(value) if _is_last_first(value) else value
        else:
            raw = _cell(r, indexes[0])
        before = len(result.students)
        _add(result, raw, _cell(r, email_index), seen)
        if section_index is not None and len(result.students) > before and _cell(r, section_index):
            result.sections[_cell(r, section_index)] += 1


def _parse_table_without_header(rows, result):
    if len(rows) > MAX_ROWS:
        result.error = f"That has more than {MAX_ROWS:,} rows — is it the right file?"
        return
    names, emails, roles = _people_page_columns(rows)
    if names is None:
        result.error = NO_NAME_COLUMN
        return
    seen = set()
    for r in rows:
        if roles is not None and "student" not in _cell(r, roles).lower():
            result.non_students += 1
            continue
        value = _cell(r, names)
        raw = flip_last_first(value) if _is_last_first(value) else value
        _add(result, raw, _cell(r, emails), seen)


ADDRESS = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")


def _one_address_per_line(lines):
    """Lines as pasted, but a line with several addresses on it (as copied
    from an email's To: line, "Alex Kim <alex@x.edu>, sam@x.edu") as one
    line per address, each with the name written before it."""
    for line in lines:
        found = list(ADDRESS.finditer(line))
        if len(found) < 2:
            yield line
            continue
        start = 0
        for match in found:
            yield line[start:match.end()]
            start = match.end()


def _parse_lines(lines, result):
    """One student per line: a name, maybe an email, separated by anything —
    "Alex Johnson, alex@school.edu", "Lee, Sam", "Maria Diaz; maria@x.edu"."""
    if len(lines) > MAX_ROWS:
        result.error = f"That has more than {MAX_ROWS:,} lines — is it the right list?"
        return
    seen = set()
    for line in _one_address_per_line(lines):
        email = find_email(line)
        rest = line
        if email:
            rest = re.sub(re.escape(email), " ", rest, flags=re.I)
        else:
            # Meant as an address but incomplete ("sam.lee@example"): keep the
            # student, and report the address so it can be fixed.
            partial = re.search(r"[^\s,;<>()\[\]\"']*@[^\s,;<>()\[\]\"']*", line)
            if partial:
                email = partial.group(0)
                rest = rest.replace(email, " ")
        rest = re.sub(r"[<>()\[\]\"]", " ", rest).strip().strip(",;\t ").strip()
        rest = re.sub(r"\s*;\s*", " ", rest)
        if _is_last_first(rest):
            rest = flip_last_first(rest)
            result.flipped = True
        _add(result, rest, email, seen)


def _check_emails(result):
    """Spot likely typos: an address whose domain is a near-miss of the one
    most of the class uses ("exmaple.edu" among "example.edu")."""
    domains = Counter(s["email"].split("@")[1] for s in result.students if s["email"])
    if len(domains) < 2:
        return
    common, count = domains.most_common(1)[0]
    if count < 3:
        return
    for s in result.students:
        if not s["email"]:
            continue
        domain = s["email"].split("@")[1]
        if domain != common and _close(domain, common):
            result.suspicious.append((s["display_name"], s["email"], common))


def _close(a, b):
    """Edit distance of at most 2."""
    if abs(len(a) - len(b)) > 2:
        return False
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1] <= 2


def describe(result, source=""):
    """A plain-English summary for the instructor: (message, [tips])."""
    n = len(result.students)
    origin = f" from “{source}”" if source else ""
    message = f"Added {n} student{'s' if n != 1 else ''}{origin}"
    if result.with_email == n:
        message += ", all with email addresses."
    elif result.with_email:
        message += f", {result.with_email} with email addresses."
    else:
        message += "."
    if result.ignored_columns:
        count = len(result.ignored_columns)
        shown = ", ".join(f"“{c}”" for c in result.ignored_columns[:4]) + (" …" if count > 4 else "")
        message += (
            f" We ignored {count} other column{'s' if count != 1 else ''} ({shown}) — none of that was saved."
        )

    tips = []
    if not result.with_email:
        tips.append(
            "No email addresses were found. Students will sign in by typing their name and "
            "choosing a PIN — but whoever types a name first gets it. To have students confirm "
            "by email instead, use Canvas's Class Roster report (see “Where do I find this in Canvas?”)."
        )
    elif result.with_email < n:
        missing = n - result.with_email
        tips.append(
            f"{missing} student{'s' if missing != 1 else ''} had no email address and will sign in "
            "with a name and a PIN instead of an emailed code."
        )
    for name, email, common in result.suspicious[:5]:
        tips.append(f"{name}'s email ends in “{email.split('@')[1]}” — did you mean “{common}”? "
                    "If it's a typo, fix it under “See or change the list”.")
    if result.flipped:
        tips.append("Names were listed “Last, First”, so we turned them around to “First Last” — "
                    "the way students type them.")
    if result.non_students:
        tips.append(f"Left out {result.non_students} teacher/TA row{'s' if result.non_students != 1 else ''}.")
    if result.canvas_rows:
        tips.append("Left out Canvas's “Points Possible” and “Test Student” rows.")
    if result.skipped:
        tips.append(f"Skipped {result.skipped} row{'s' if result.skipped != 1 else ''} with no name.")
    if result.email_only:
        n_only = len(result.email_only)
        tips.append(f"{n_only} student{'s were' if n_only != 1 else ' was'} added by email address alone. "
                    "Until they sign in and type their name, the list shows a name made from the address.")
    for name, value in result.bad_emails[:5]:
        tips.append(f"{name}'s email “{value}” looks incomplete (it needs an ending like .edu), so it was left "
                    "blank — they'll use a PIN until you fix it under “See or change the list”.")
    if len(result.bad_emails) > 5:
        tips.append(f"…and {len(result.bad_emails) - 5} more incomplete email addresses.")
    if result.tab > 1:
        tips.append(f"The class list was on tab {result.tab} of your Excel file, so that's the one we used.")
    if result.repeated:
        shown = ", ".join(result.repeated[:5])
        tips.append(f"{shown} {'was' if len(result.repeated) == 1 else 'were'} listed twice; we kept one of each.")
    for name in result.numbered[:5]:
        first = SAME_NAME_SUFFIX.sub("", name)
        tips.append(f"Two students are named {first}, so they're listed as “{first}” and “{name}”. Each one signs "
                    "in with a code sent to their own email, so they can't mix them up.")
    if result.duplicates:
        shown = ", ".join(f"{name}{f' ({email})' if email else ''}" for name, email in result.duplicates[:5])
        first = result.duplicates[0][0].split()
        example = f"{first[0]} X. {' '.join(first[1:])}" if len(first) > 1 else f"{first[0]} X."
        tips.append(
            f"Two different students can't have the same name here, so these were left out: {shown}. "
            f"Add them back with a middle initial (like “{example}”) using “Add a student who joined late”."
        )
    if result.garbled:
        tips.append(
            "Some accented letters may not have come through (" + ", ".join(result.garbled[:3])
            + "). If they look wrong, " + SAVE_AS_CSV
        )
    if n > 150:
        tips.append(f"{n} students is a big class — make sure this is the right course's list.")
    return message, tips


# ---------------------------------------------------------------------------
# Finding a student on the class list from what they typed
# ---------------------------------------------------------------------------
def match_name(typed, rows):
    """Look a typed name up on the class list (rows of name_key,
    display_name, email). Returns (row, suggestions): the row when it's
    clearly them — the same name ignoring case, accents and punctuation, the
    same words in another order ("Lee, Sam"), or their email address — and
    otherwise up to four close names to ask "Did you mean…?" about."""
    from difflib import SequenceMatcher

    key = fold(typed)
    if not key and "@" not in (typed or ""):
        return None, []
    by_key = {r["name_key"]: r for r in rows}
    if key in by_key:
        return by_key[key], []
    email = find_email(typed)
    if email:
        for r in rows:
            if r["email"] and r["email"].lower() == email:
                return r, []
    if _is_last_first(typed or ""):
        flipped = fold(flip_last_first(typed))
        if flipped in by_key:
            return by_key[flipped], []
    words = key.split()
    same_words = [r for r in rows if sorted(r["name_key"].split()) == sorted(words)]
    if len(same_words) == 1:
        return same_words[0], []

    scored = []
    for r in rows:
        theirs = r["name_key"].split()
        score = max(
            SequenceMatcher(None, key, r["name_key"]).ratio(),
            SequenceMatcher(None, " ".join(reversed(words)), r["name_key"]).ratio(),
        )
        if len(words) == 1 and words[0] in theirs:
            score = max(score, 0.9)  # just a first or last name
        if words and all(any(w.startswith(t) for w in theirs) for t in words):
            score = max(score, 0.85)  # the start of each name ("Jon Mull")
        if len(words) >= 2 and len(theirs) >= 2 and words[-1] == theirs[-1]:
            score = max(score, 0.75)  # same last name, different first ("Bill" for "William")
        if score >= 0.75:
            scored.append((-score, r["display_name"].lower(), r))
    scored.sort(key=lambda item: item[:2])
    return None, [r for _, _, r in scored[:4]]
