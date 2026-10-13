"""
Builds the Scheduler Helper Chrome extension (extension/) for

    .venv/bin/python tools/build_extension.py store   # dist/scheduler-helper.zip, for the Chrome Web Store (reproducible)
    .venv/bin/python tools/build_extension.py dev     # dist/scheduler-helper-dev/, for trying it locally

The dev build also reads the stand-in Canvas (tools/fake_canvas.py, on
127.0.0.1:5077) and answers Scheduler running locally (127.0.0.1:5050 or
localhost:5050), and has a fixed id (tools/extension_dev_key.txt is the
public half of a throwaway key), so a local Scheduler can be told its id:

    CANVAS_HELPER_IDS=<that id> .venv/bin/python app.py

An earlier helper, to check the site still works with it (the store's
copy, until an update is approved): EXTENSION_SOURCE=<its extension/
folder> DEV_NAME=<a folder name in dist/> ... dev.

The icons are drawn here, so there are no image files to keep.
"""

import base64
import hashlib
import json
import os
import re
import shutil
import struct
import sys
import zipfile
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "extension")
DIST = os.path.join(ROOT, "dist")
DEV_KEY = os.path.join(ROOT, "tools", "extension_dev_key.txt")


def extension_id(public_key_b64):
    digest = hashlib.sha256(base64.b64decode(public_key_b64)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


def _png(size):
    """The icon: the site's logo (public/static/logo.svg), a white calendar
    with a check on a rounded brown square, drawn here on its 32-unit grid
    (small sizes drawn 4x and scaled down, for smooth edges)."""
    scale = 4 if size <= 128 else 1
    big = size * scale
    top, bottom = (154, 85, 32), (125, 67, 23)  # the logo's gradient, #9a5520 to #7d4317
    white = (255, 255, 255)
    half = 1.0  # half the stroke (2 units)

    def segment(px, py, ax, ay, bx, by):
        dx, dy = bx - ax, by - ay
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        return ((px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2) ** 0.5

    def rounded(px, py, x0, y0, x1, y1, r):  # distance from a rounded rectangle's edge
        cx, cy = min(max(px, x0 + r), x1 - r), min(max(py, y0 + r), y1 - r)
        inside = x0 + r <= px <= x1 - r or y0 + r <= py <= y1 - r
        if inside and x0 <= px <= x1 and y0 <= py <= y1:
            return min(px - x0, x1 - px, py - y0, y1 - py)
        return abs(((px - cx) ** 2 + (py - cy) ** 2) ** 0.5 - r)

    lines = [(7, 13, 25, 13), (12, 6.5, 12, 10.5), (20, 6.5, 20, 10.5),
             (12, 18.5, 14.6, 21.1), (14.6, 21.1, 20, 15.8)]

    def color(x, y):
        u, v = (x + 0.5) / big * 32, (y + 0.5) / big * 32
        r = 8  # the square's corners
        cx, cy = min(max(u, r), 32 - r), min(max(v, r), 32 - r)
        if (u - cx) ** 2 + (v - cy) ** 2 > r * r:
            return None
        if rounded(u, v, 7, 8.5, 25, 24.5, 2.5) <= half or any(segment(u, v, *ln) <= half for ln in lines):
            return white
        mix = (u + v) / 64  # (top left to bottom right)
        return tuple(round(a + (b - a) * mix) for a, b in zip(top, bottom))

    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(scale):
                for sx in range(scale):
                    c = color(x * scale + sx, y * scale + sy)
                    if c:
                        acc[0] += c[0]; acc[1] += c[1]; acc[2] += c[2]; acc[3] += 255
            n = scale * scale
            alpha = acc[3] // n
            if alpha:
                row += bytes([acc[0] * 255 // acc[3], acc[1] * 255 // acc[3], acc[2] * 255 // acc[3], alpha])
            else:
                row += bytes([0, 0, 0, 0])
        rows.append(bytes(row))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def build(target):
    if target not in ("store", "dev"):
        sys.exit("build_extension.py: store or dev")
    # (The store's zip is always the repo's own extension/.)
    source = os.environ.get("EXTENSION_SOURCE") or SOURCE if target == "dev" else SOURCE
    files = ["background.js", "popup.html", "popup.js", "allow.html", "allow.js"]
    if target == "dev":  # (an earlier helper, EXTENSION_SOURCE: 1.0.0 has no popup, 1.0.6 no allow page)
        files = [f for f in files if os.path.exists(os.path.join(source, f))]
    missing = [f for f in files if not os.path.exists(os.path.join(source, f))]
    if missing:
        sys.exit("build_extension.py: missing " + ", ".join(missing))
    with open(os.path.join(source, "manifest.json"), encoding="utf-8") as handle:
        manifest = json.load(handle)
    name = "scheduler-helper" if target == "store" else os.environ.get("DEV_NAME") or "scheduler-helper-dev"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,60}", name) or ".." in name:
        sys.exit("build_extension.py: DEV_NAME is a plain folder name, like helper-mine")
    out = os.path.join(DIST, name)
    assert os.path.dirname(os.path.abspath(out)) == os.path.abspath(DIST)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    if target == "dev":
        with open(DEV_KEY, encoding="utf-8") as handle:
            manifest["key"] = handle.read().strip()
        manifest["name"] += " (dev)"
        canvases = os.environ.get("DEV_CANVAS_PORTS", "5077").split(",")  # (another stand-in Canvas: FAKE_CANVAS_PORT)
        manifest["host_permissions"] = manifest.get("host_permissions", []) + [
            f"http://127.0.0.1:{p.strip()}/*" for p in canvases]
        # Any other stand-in Canvas on this computer is one it must be allowed to read first (as another school's).
        if "optional_host_permissions" in manifest:
            manifest["optional_host_permissions"] += ["http://127.0.0.1/*"]
        ports = os.environ.get("DEV_SITE_PORTS", "5050").split(",")  # (another local Scheduler: DEV_SITE_PORTS=5050,5066)
        manifest["externally_connectable"]["matches"] += [f"http://{h}:{p.strip()}/*" for p in ports
                                                          for h in ("127.0.0.1", "localhost")]
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
    for file in files:
        shutil.copy(os.path.join(source, file), out)
    for page_name in ("popup.html", "allow.html"):
        page_path = os.path.join(out, page_name)
        if os.path.exists(page_path):  # (its code link: this version's tag, helper-v<version>)
            with open(page_path, encoding="utf-8") as handle:
                page = handle.read().replace("/tree/main/extension", f"/tree/helper-v{manifest['version']}/extension")
            with open(page_path, "w", encoding="utf-8") as handle:
                handle.write(page)
    for size in sorted(int(k) for k in manifest["icons"]):
        with open(os.path.join(out, f"icon-{size}.png"), "wb") as handle:
            handle.write(_png(size))
    if target == "store":
        # Reproducible: the same code always makes the same zip, byte for byte
        # (fixed dates and order), so anyone can check the store's copy
        # against this repo (tools/verify_extension.py).
        archive = os.path.join(DIST, name + ".zip")
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            for file in sorted(os.listdir(out)):
                info = zipfile.ZipInfo(file, date_time=(1980, 1, 1, 0, 0, 0))
                info.external_attr = 0o644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                with open(os.path.join(out, file), "rb") as handle:
                    z.writestr(info, handle.read(), compresslevel=9)
        print(archive)
        with open(archive, "rb") as handle:
            print("sha256:", hashlib.sha256(handle.read()).hexdigest())
    else:
        print(out)
        print("id:", extension_id(manifest["key"]))


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "dev")
