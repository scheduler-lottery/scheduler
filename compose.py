"""
Emails people send from their own email account: the backup when the site's
own sign-in emails can't reach a student. The site writes each message; a
link opens it, addressed and ready, in the sender's own email (Outlook on
the web, Gmail, Apple Mail, ...), and they press Send. The links themselves
are built in the browser (public/static/compose.js).

Which service opens by default comes from the sender's address: well-known
providers by name, and other domains from where their mail goes (the MX
record, or failing that the SPF record), looked up once over DNS-over-HTTPS
and remembered. Only the domain (such as "northwestern.edu") is looked up,
never an address.
"""

import json
import urllib.parse
import urllib.request
from datetime import timedelta

from sqlalchemy import text

import db
from util import iso, now, parse_iso

# In the order the "Emails open in" menu lists them.
SERVICES = ("outlook", "gmail", "app", "outlookcom", "yahoo", "copy")
KNOWN = {
    "gmail.com": "gmail", "googlemail.com": "gmail",
    "outlook.com": "outlookcom", "hotmail.com": "outlookcom", "live.com": "outlookcom", "msn.com": "outlookcom",
    "yahoo.com": "yahoo", "ymail.com": "yahoo", "rocketmail.com": "yahoo",
    "icloud.com": "app", "me.com": "app", "mac.com": "app",
}
DOH_URL = "https://cloudflare-dns.com/dns-query?name={name}&type={kind}"
REMEMBER_DAYS = 180
RETRY_DAYS = 1  # after a lookup that couldn't be made


def lookup(name, kind):
    """The DNS records of one kind for a name, lowercased ([] if there are
    none), or None if the lookup couldn't be made."""
    try:
        ask = urllib.request.Request(
            DOH_URL.format(name=urllib.parse.quote(name), kind=kind), headers={"Accept": "application/dns-json"}
        )
        with urllib.request.urlopen(ask, timeout=2) as answer:
            found = json.load(answer)
        return [str(record.get("data", "")).lower() for record in found.get("Answer") or []]
    except Exception:  # noqa: BLE001 - a default is never worth an error
        return None


def _provider(records):
    found = " ".join(records)
    if "outlook.com" in found or "office365" in found:
        return "outlook"
    if "google.com" in found or "googlemail.com" in found:
        return "gmail"
    if "yahoodns" in found:
        return "yahoo"
    if "icloud.com" in found:
        return "app"
    return ""


def _detect(domain):
    """Look the domain (then its parents: law.school.edu, school.edu) up.
    None if DNS couldn't be asked."""
    labels = domain.split(".")
    for start in range(max(1, len(labels) - 1)):
        name = ".".join(labels[start:])
        mx = lookup(name, "MX")
        if mx is None:
            return None
        found = _provider(mx)
        if not found and mx:
            # A filtering service (Proofpoint, Mimecast, ...) in front of the
            # real mailbox: the SPF record usually names the provider.
            found = _provider([r for r in lookup(name, "TXT") or [] if "v=spf1" in r])
        if found or mx:
            return found or "app"
    return "app"


def service_for(address):
    """The email service that most likely holds this address — one of
    SERVICES. Never raises. Call it before the request writes anything: on
    a first lookup it saves the answer on a connection of its own."""
    domain = (address or "").rpartition("@")[2].strip().lower().rstrip(".")
    if "." not in domain:
        return "app"
    if domain in KNOWN:
        return KNOWN[domain]
    key = "mail-service:" + domain
    try:
        saved = db.scalar("SELECT value FROM app_state WHERE key = :key", key=key) or ""
        service, _, until = saved.partition("|")
        if service in SERVICES and until and parse_iso(until) > now():
            return service
    except Exception:  # noqa: BLE001
        pass
    found = _detect(domain)
    service = found or "app"
    until = now() + timedelta(days=REMEMBER_DAYS if found else RETRY_DAYS)
    try:
        with db.get_engine().begin() as connection:
            connection.execute(
                text("INSERT INTO app_state (key, value) VALUES (:key, :value) "
                     "ON CONFLICT (key) DO UPDATE SET value = excluded.value"),
                {"key": key, "value": f"{service}|{iso(until)}"},
            )
    except Exception:  # noqa: BLE001 - it's only a remembered default
        pass
    return service
