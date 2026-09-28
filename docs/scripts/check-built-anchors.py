#!/usr/bin/env python3
"""Check local section links against IDs in built VitePress HTML, not guessed slugs.

Run after docs:build. DOCS_BASE must match the build's base path.
External URLs, text fragments, and non-HTML assets are outside this check.
"""

import argparse
import os
import posixpath
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


class Page(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.ids = set()
        self.links = []
        self.feed(source)
        self.close()

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if attributes.get("id") is not None:
            self.ids.add(attributes["id"])
        if tag == "a" and attributes.get("href") is not None:
            self.links.append(attributes["href"])

    handle_startendtag = handle_starttag


def check(dist, base):
    pages = {
        file.relative_to(dist).as_posix(): Page(file.read_text(encoding="utf-8"))
        for file in sorted(dist.rglob("*.html"))
    }
    if not pages:
        print(f"No built HTML found in {dist}. Run npm run docs:build first.")
        return 1
    base = "/" + base.strip("/") + "/" if base.strip("/") else "/"
    checked = 0
    errors = []
    for filename, page in pages.items():
        for href in page.links:
            url = urlsplit(href)
            if url.scheme or url.netloc or not url.fragment:
                continue
            # Browser text fragments do not refer to element IDs. A preceding
            # element fragment still needs to exist (e.g. #heading:~:text=...).
            fragment = unquote(url.fragment.split(":~:text=", 1)[0])
            if not fragment:
                continue
            target = unquote(url.path)
            if target.startswith("/"):
                if not target.startswith(base):
                    # Root-relative links outside a mounted site's base are
                    # other applications, not part of this generated site.
                    continue
                target = target[len(base):]
            elif target:
                target = posixpath.join(posixpath.dirname(filename), target)
            else:
                target = filename
            target = posixpath.normpath(target)
            if Path(target).suffix not in ("", ".html"):
                continue
            candidates = [target, target + ".html", posixpath.normpath(posixpath.join(target, "index.html"))]
            resolved = next((candidate for candidate in candidates if candidate in pages), None)
            checked += 1
            if resolved is None:
                errors.append(f"{filename}: {href} (target HTML not found)")
            elif fragment not in pages[resolved].ids:
                errors.append(f"{filename}: {href} (ID {fragment!r} missing in {resolved})")
    print(f"Checked {checked} local section links across {len(pages)} built HTML pages.")
    if errors:
        print("\n".join(errors))
        print(f"Found {len(errors)} broken section links.")
        return 1
    print("Built section-link checks passed.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path(__file__).resolve().parents[1] / ".vitepress/dist")
    parser.add_argument("--base", default=os.environ.get("DOCS_BASE", "/"))
    args = parser.parse_args()
    return check(args.dist, args.base)


if __name__ == "__main__":
    raise SystemExit(main())
