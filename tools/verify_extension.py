"""
Checks that a copy of Scheduler Helper (the Chrome Web Store's, or any zip)
holds exactly what this repo builds: every file the same, nothing added.

    .venv/bin/python tools/verify_extension.py scheduler-helper.crx

To get the store's copy (Chrome's own update address for the helper):

    curl -L -o scheduler-helper.crx "https://clients2.google.com/service/update2/crx?response=redirect&prodversion=141.0&acceptformat=crx2,crx3&x=id%3Dhafmidgmpikkkjphmcbnggidbaeoiapd%26uc"

A .crx is a zip with a signature in front, which Python's zipfile reads
as it is. The store adds two things of its own, which are left out of the
comparison: its signature folder (_metadata/) and the manifest's
"update_url" (and "key"). Check out the commit of the version you're
comparing (git log -- extension/manifest.json) before running this.
"""

import importlib.util
import json
import os
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE_ONLY_KEYS = ("update_url", "key")


def _build(into):
    spec = importlib.util.spec_from_file_location("build_extension", os.path.join(ROOT, "tools", "build_extension.py"))
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    build.DIST = into
    build.build("store")
    with zipfile.ZipFile(os.path.join(into, "scheduler-helper.zip")) as z:
        return {name: z.read(name) for name in z.namelist()}


def _pixels(data):
    """A PNG's size and pixels (compression can differ from one zlib to the next)."""
    import struct
    import zlib

    pos, header, raw = 8, b"", b""
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + length]
        header = body if kind == b"IHDR" else header
        raw += body if kind == b"IDAT" else b""
        pos += 12 + length
    return header, zlib.decompress(raw)


def _manifest(data):
    manifest = json.loads(data.decode("utf-8-sig"))
    for key in STORE_ONLY_KEYS:
        manifest.pop(key, None)
    return manifest


def main(path):
    with zipfile.ZipFile(path) as z:
        theirs = {name: z.read(name) for name in z.namelist()
                  if not name.startswith("_metadata/") and not name.endswith("/")}
    with tempfile.TemporaryDirectory() as into:
        ours = _build(into)
    same = True
    for name in sorted(set(ours) | set(theirs)):
        if name not in theirs:
            print(f"MISSING  {name} (built here, not in the copy)")
            same = False
        elif name not in ours:
            print(f"EXTRA    {name} (in the copy, not built from this repo)")
            same = False
        elif name == "manifest.json" and _manifest(ours[name]) == _manifest(theirs[name]):
            print(f"same     {name} (apart from the store's update address)")
        elif ours[name] == theirs[name]:
            print(f"same     {name}")
        elif name.endswith(".png") and _pixels(ours[name]) == _pixels(theirs[name]):
            print(f"same     {name} (the same picture)")
        else:
            print(f"DIFFERS  {name}")
            same = False
    print("The copy matches this repo." if same else "The copy does NOT match this repo (or this checkout's version).")
    return 0 if same else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
