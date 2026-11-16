"""The installable-app files: a valid manifest whose icons exist, and a service worker that never stores API data."""
import json
import struct
from pathlib import Path

PUBLIC = Path(__file__).resolve().parent.parent / "web" / "public"


def png_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"
    return struct.unpack(">II", head[16:24])


def test_manifest_is_complete_and_its_icons_exist_at_the_stated_sizes():
    m = json.loads((PUBLIC / "manifest.webmanifest").read_text())
    assert m["display"] == "standalone" and m["start_url"] == "/" and m["name"] and m["short_name"] and m["theme_color"].startswith("#")
    sizes = {(i["sizes"], i["purpose"]) for i in m["icons"]}
    assert ("192x192", "any") in sizes and ("512x512", "any") in sizes and ("512x512", "maskable") in sizes      # what browsers need to offer an install
    for icon in m["icons"]:
        w, h = icon["sizes"].split("x")
        assert png_size(PUBLIC / icon["src"].lstrip("/")) == (int(w), int(h))
    assert png_size(PUBLIC / "icons" / "apple-touch-icon.png") == (180, 180)


def test_service_worker_leaves_the_api_alone_and_only_stores_the_shell_and_hashed_files():
    sw = (PUBLIC / "sw.js").read_text()
    assert "startsWith('/api/')" in sw and "req.method !== 'GET'" in sw          # API and writes go straight to the network, untouched
    assert "/assets/" in sw
    stored_paths = [line for line in sw.splitlines() if "caches.open" in line or "c.put" in line]
    assert stored_paths and not any("/api" in line for line in stored_paths)


def test_the_page_links_the_manifest():
    html = (PUBLIC.parent / "index.html").read_text()
    assert 'rel="manifest"' in html and 'name="theme-color"' in html and "apple-touch-icon" in html
