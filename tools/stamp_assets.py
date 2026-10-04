"""Stamp style.css / app.js with a content hash and publish the same hash as
`<meta name="smartmb-build">`.

Why: phones cache the bundle hard.  Without a version in the URL an engineer keeps
running the build they first loaded, so a fix deployed in the morning never reaches
the site in the afternoon.  The meta tag lets the running app notice a newer build on
the server and refresh itself.
"""
from __future__ import annotations

import hashlib
import pathlib
import re
import sys

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"


def file_hash(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:10]


def build_id(web_dir: pathlib.Path | None = None) -> str:
    """css-hash(5) + js-hash(5) — the server computes the same id for /api/health."""
    d = web_dir or WEB
    return file_hash(d / "style.css")[:5] + file_hash(d / "app.js")[:5]


def stamp(html_path: pathlib.Path | None = None) -> str:
    html_path = html_path or (WEB / "index.html")
    build = build_id()
    html = html_path.read_text()
    html = re.sub(r'(href="style\.css)(\?v=[0-9a-f]+)?(")', rf"\1?v={build}\3", html)
    html = re.sub(r'(src="app\.js)(\?v=[0-9a-f]+)?(")', rf"\1?v={build}\3", html)
    if 'name="smartmb-build"' in html:
        html = re.sub(r'(<meta name="smartmb-build" content=")[^"]*(")', rf"\g<1>{build}\2", html)
    else:
        html = html.replace("<head>", f'<head>\n  <meta name="smartmb-build" content="{build}">', 1)
    html_path.write_text(html)
    return build


if __name__ == "__main__":
    build = stamp()
    print(f"stamped build {build} -> {WEB / 'index.html'}")
    sys.exit(0)
