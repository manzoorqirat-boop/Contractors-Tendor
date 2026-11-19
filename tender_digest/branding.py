"""The company's logo picture, shown on the sign-in screen, in the menu and as the browser-tab icon.

The picture is checked by reading the file itself, never by trusting its name or the type the browser claims: only PNG, JPEG and WebP are accepted.
SVG is refused on purpose (an SVG can carry scripts), and so are GIFs and anything that merely has an image's name. Size is capped so a huge
picture cannot slow every sign-in, and the dimensions are read from the file header so a tiny file that expands into a giant image is refused too.
"""
from __future__ import annotations

import struct

MAX_BYTES = 400 * 1024
MAX_SIDE = 2000
MIN_SIDE = 16
TYPES = ("image/png", "image/jpeg", "image/webp")


class LogoError(ValueError):
    pass


def _png(d: bytes) -> tuple[int, int]:
    if len(d) < 24 or d[12:16] != b"IHDR":
        raise LogoError("This PNG file is damaged")
    return struct.unpack(">II", d[16:24])


def _jpeg(d: bytes) -> tuple[int, int]:
    i = 2
    while i + 9 < len(d):
        if d[i] != 0xFF:
            i += 1
            continue
        marker = d[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        (length,) = struct.unpack(">H", d[i + 2:i + 4])
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h, w = struct.unpack(">HH", d[i + 5:i + 9])
            return w, h
        i += 2 + length
    raise LogoError("This JPEG file is damaged")


def _webp(d: bytes) -> tuple[int, int]:
    kind = d[12:16]
    if kind == b"VP8 " and len(d) >= 30:
        w, h = struct.unpack("<HH", d[26:30])
        return w & 0x3FFF, h & 0x3FFF
    if kind == b"VP8L" and len(d) >= 25:
        bits = struct.unpack("<I", d[21:25])[0]
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if kind == b"VP8X" and len(d) >= 30:
        return int.from_bytes(d[24:27], "little") + 1, int.from_bytes(d[27:30], "little") + 1
    raise LogoError("This WebP file is damaged")


def check(data: bytes) -> tuple[str, int, int]:
    """(content type, width, height) of a logo file, or LogoError saying what is wrong in words a person can act on."""
    if not data:
        raise LogoError("Choose a picture file first")
    if len(data) > MAX_BYTES:
        raise LogoError(f"The picture is {len(data) // 1024} KB. Use one under {MAX_BYTES // 1024} KB (a logo rarely needs more; save it smaller)")
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        mime, (w, h) = "image/png", _png(data)
    elif data[:3] == b"\xff\xd8\xff":
        mime, (w, h) = "image/jpeg", _jpeg(data)
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        mime, (w, h) = "image/webp", _webp(data)
    else:
        raise LogoError("Use a PNG, JPEG or WebP picture. SVG, GIF and other formats are not accepted")
    if w < MIN_SIDE or h < MIN_SIDE:
        raise LogoError(f"The picture is only {w} x {h} pixels. Use one at least {MIN_SIDE} x {MIN_SIDE}, ideally 200 pixels or more")
    if w > MAX_SIDE or h > MAX_SIDE:
        raise LogoError(f"The picture is {w} x {h} pixels. Use one no larger than {MAX_SIDE} x {MAX_SIDE}")
    return mime, w, h
