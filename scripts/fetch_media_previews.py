#!/usr/bin/env python3
"""Populate media preview images from external Open Graph metadata."""

from __future__ import annotations

import argparse
import html
import html.parser
import socket
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from pathlib import Path
from typing import Any

from site_content import ROOT, collect_content, write_toml_markdown


class MetadataParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.images: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "meta":
            return
        values = {key.lower(): (value or "") for key, value in attrs}
        name = (values.get("property") or values.get("name") or "").lower()
        content = values.get("content") or ""
        if name in {"og:image", "og:image:url", "twitter:image", "twitter:image:src"} and content:
            self.images.append(html.unescape(content.strip()))


def fetch_preview_image(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; DouglasFerreiraSitePreview/1.0; +https://douglasferreira.me/)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        content_type = response.headers.get("content-type", "")
        if "html" not in content_type:
            return ""
        body = response.read(400_000).decode(response.headers.get_content_charset() or "utf-8", errors="replace")
    parser = MetadataParser()
    parser.feed(body)
    for image in parser.images:
        absolute = urllib.parse.urljoin(url, image)
        if absolute.startswith(("http://", "https://")):
            return absolute
    return ""


def ordered_front_matter(front_matter: dict[str, Any], key: str, value: str) -> OrderedDict[str, Any]:
    result: OrderedDict[str, Any] = OrderedDict()
    inserted = False
    for existing_key, existing_value in front_matter.items():
        result[existing_key] = existing_value
        if existing_key == "image":
            result[key] = value
            inserted = True
    if not inserted:
        result[key] = value
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    changed = False
    for item in collect_content({"media"}):
        fm = item.front_matter
        if item.draft:
            continue
        if str(fm.get("image") or "").strip() or str(fm.get("external_image") or "").strip():
            continue
        external_url = str(fm.get("external_url") or "").strip()
        if not external_url:
            continue
        try:
            image = fetch_preview_image(external_url)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, socket.timeout, UnicodeDecodeError) as exc:
            print(f"Skipping preview for {item.key}: {exc}")
            continue
        if not image:
            print(f"No preview image found for {item.key}")
            continue
        print(f"Using preview image for {item.key}: {image}")
        if not args.dry_run:
            write_toml_markdown(item.path, ordered_front_matter(fm, "external_image", image), item.body)
            changed = True

    if not changed:
        print("No media previews changed.")


if __name__ == "__main__":
    main()
