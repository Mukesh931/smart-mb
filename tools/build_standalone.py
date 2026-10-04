"""Bundles web/ into one self-contained HTML file (inline CSS + JS + demo snapshot).

Useful for previews and for handing the app to someone on a USB stick / tablet with
no server: the bundled snapshot renders read-only, and it upgrades to live data the
moment it is opened from the FastAPI server (same origin as the API).

    python3 -m tools.build_standalone
"""
from __future__ import annotations
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(BASE, "web")
OUT = os.path.join(WEB, "smart-mb-standalone.html")


def build() -> str:
    html = open(os.path.join(WEB, "index.html"), encoding="utf-8").read()
    css = open(os.path.join(WEB, "style.css"), encoding="utf-8").read()
    js = open(os.path.join(WEB, "app.js"), encoding="utf-8").read()

    # the stamp tool adds ?v=<hash> to both references — accept either form
    html = re.sub(r'<link rel="stylesheet" href="style\.css(\?v=[0-9a-f]+)?">',
                  lambda m: f"<style>\n{css}\n</style>", html)
    html = re.sub(r'<script src="app\.js(\?v=[0-9a-f]+)?"></script>',
                  lambda m: f"<script>\n{js}\n</script>", html)
    html = html.replace('<link rel="manifest" href="manifest.json">',
                        '<!-- manifest omitted in the single-file build -->')
    html = html.replace("<title>Smart-MB", "<title>Smart-MB (standalone build)")
    assert 'href="style.css' not in html and 'src="app.js' not in html, "external references remain"
    open(OUT, "w", encoding="utf-8").write(html)
    return OUT


if __name__ == "__main__":
    path = build()
    print(f"standalone build: {os.path.getsize(path)/1024:.0f} KiB -> {path}")
