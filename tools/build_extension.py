"""
Builds the Scheduler Helper browser extension (extension/) for

    .venv/bin/python tools/build_extension.py store   # dist/scheduler-helper.zip, for the Chrome Web Store
    .venv/bin/python tools/build_extension.py dev     # dist/scheduler-helper-dev/, for trying it locally

The dev build also reads the stand-in Canvas (tools/fake_canvas.py, on
127.0.0.1:5077) and answers Scheduler running locally (127.0.0.1:5050 or
localhost:5050), and has a fixed id (tools/extension_dev_key.txt is the
public half of a throwaway key), so a local Scheduler can be told its id:

    CANVAS_HELPER_IDS=<that id> .venv/bin/python app.py

The icons are drawn here, so there are no image files to keep.
"""

import base64
import hashlib
import json
import os
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
    """The icon: a rounded brown square with a white calendar page."""
    scale = 4
    big = size * scale
    brown, white, ink = (154, 85, 32), (255, 253, 248), (122, 63, 18)

    def color(x, y):
        u, v = (x + 0.5) / big, (y + 0.5) / big
        r = 0.22  # corner radius
        cx, cy = min(max(u, r), 1 - r), min(max(v, r), 1 - r)
        if (u - cx) ** 2 + (v - cy) ** 2 > r * r:
            return None
        if 0.24 <= u <= 0.76 and 0.28 <= v <= 0.78:  # the page
            if v <= 0.40:
                return ink
            if any(abs(u - gx) < 0.045 and abs(v - gy) < 0.045 for gx in (0.36, 0.5, 0.64) for gy in (0.52, 0.66)):
                return brown
            return white
        if (abs(u - 0.38) < 0.03 or abs(u - 0.62) < 0.03) and 0.2 <= v <= 0.33:  # rings
            return white
        return brown

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
    with open(os.path.join(SOURCE, "manifest.json"), encoding="utf-8") as handle:
        manifest = json.load(handle)
    name = "scheduler-helper" if target == "store" else "scheduler-helper-dev"
    out = os.path.join(DIST, name)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    if target == "dev":
        with open(DEV_KEY, encoding="utf-8") as handle:
            manifest["key"] = handle.read().strip()
        manifest["name"] += " (dev)"
        manifest["host_permissions"] += ["http://127.0.0.1:5077/*"]
        manifest["externally_connectable"]["matches"] += ["http://127.0.0.1:5050/*", "http://localhost:5050/*"]
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
    shutil.copy(os.path.join(SOURCE, "background.js"), out)
    for size in (16, 32, 48, 128):
        with open(os.path.join(out, f"icon-{size}.png"), "wb") as handle:
            handle.write(_png(size))
    if target == "store":
        archive = os.path.join(DIST, name + ".zip")
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            for file in sorted(os.listdir(out)):
                z.write(os.path.join(out, file), file)
        print(archive)
    else:
        print(out)
        print("id:", extension_id(manifest["key"]))


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "dev")
