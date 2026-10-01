"""Bundles web/ into one self-contained HTML file (inline CSS + JS + demo snapshot).

Useful for previews and for handing the app to someone on a USB stick / tablet with
no server: the bundled snapshot renders read-only, and it upgrades to live data the
moment it is opened from the FastAPI server (same origin as the API).

    python3 -m tools.build_standalone
"""
from __future__ import annotations
import os, re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(BASE, "web")
OUT = os.path.join(WEB, "smart-mb-standalone.html")


def build() -> str:
    html = open(os.path.join(WEB, "index.html"), encoding="utf-8").read()
    css = open(os.path.join(WEB, "style.css"), encoding="utf-8").read()
    js = open(os.path.join(WEB, "app.js"), encoding="utf-8").read()

    html = html.replace('<link rel="stylesheet" href="style.css">', f"<style>\n{css}\n</style>")
    html = html.replace('<script src="app.js"></script>', f"<script>\n{js}\n</script>")
    html = html.replace("<title>Smart-MB", "<title>Smart-MB (standalone build)")
    assert "style.css" not in html and "app.js" not in html, "external references remain"
    open(OUT, "w", encoding="utf-8").write(html)
    return OUT


if __name__ == "__main__":
    path = build()
    print(f"standalone build: {os.path.getsize(path)/1024:.0f} KiB -> {path}")
