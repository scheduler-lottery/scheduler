"""
Secrets the site's owner saves on /owner (so far, the WorkOS API key), kept
in the database's app_state table encrypted with a key derived from
SECRET_KEY: the database alone never reveals them. If SECRET_KEY changes,
they can no longer be read, and the owner saves them again.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

import db
import settings


def _box():
    secret = settings.SECRET_KEY or ("" if settings.IS_VERCEL else "local development only")
    if not secret:
        return None
    key = hashlib.sha256(b"scheduler/site-secrets|" + secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def save(name, value):
    """Encrypt and store a secret. Commits."""
    box = _box()
    if box is None:
        raise RuntimeError("SECRET_KEY isn't set, so secrets can't be stored safely")
    db.run(
        "INSERT INTO app_state (key, value) VALUES (:key, :value) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        key="secret:" + name, value=box.encrypt(value.encode()).decode("ascii"),
    )
    db.commit()


def load(name):
    """The stored secret, or "" if there isn't one (or it can't be read)."""
    box = _box()
    stored = db.scalar("SELECT value FROM app_state WHERE key = :key", key="secret:" + name)
    if not stored or box is None:
        return ""
    try:
        return box.decrypt(stored.encode("ascii")).decode()
    except (InvalidToken, ValueError):
        return ""


def remove(name):
    """Commits."""
    db.run("DELETE FROM app_state WHERE key = :key", key="secret:" + name)
    db.commit()
