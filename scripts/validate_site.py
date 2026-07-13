#!/usr/bin/env python3
"""Lightweight validation for the generated Hugo site."""

from __future__ import annotations

import argparse
import html.parser
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from site_content import clean_markdown, collect_content


ROOT = Path(__file__).resolve().parents[1]
CONTENT_DIRS = [ROOT / "content" / "en", ROOT / "content" / "pt"]
BASE_URL = "https://douglasferreira.me/"


@dataclass
class Post:
    path: Path
    permalink: str
    draft: bool = False
    tags: list[str] = field(default_factory=list)
    federate: bool = False


class ClassParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.classes: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name == "class" and value:
                self.classes.update(value.split())


class FeedLinkParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_item = False
        self.in_link = False
        self.links: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "item":
            self.in_item = True
        elif tag == "link" and self.in_item:
            self.in_link = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "item":
            self.in_item = False
        elif tag == "link":
            self.in_link = False

    def handle_data(self, data: str) -> None:
        if self.in_item and self.in_link and data.strip():
            self.links.add(data.strip())


def parse_simple_yaml(text: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    current_key: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if line.startswith("  - ") and current_key:
            data.setdefault(current_key, []).append(line[4:].strip().strip('"\''))
            continue
        current_key = None
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        current_key = key
        if value == "[]":
            data[key] = []
        elif value in {"true", "false"}:
            data[key] = value == "true"
        elif value.startswith("[") and value.endswith("]"):
            data[key] = [part.strip().strip('"\'') for part in value[1:-1].split(",") if part.strip()]
        elif value:
            data[key] = value.strip('"\'')
        else:
            data[key] = []
    return data


def read_front_matter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if text.startswith("+++"):
        end = text.find("\n+++", 3)
        if end == -1:
            return {}
        return tomllib.loads(text[3:end])
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end == -1:
            return {}
        return parse_simple_yaml(text[3:end])
    return {}


def slug_for(path: Path, language_root: Path) -> str:
    rel = path.relative_to(language_root).with_suffix("")
    parts = rel.parts
    if parts[-1] == "_index":
      parts = parts[:-1]
    return "/".join(parts)


def permalink_for(path: Path, language_root: Path) -> str:
    slug = slug_for(path, language_root)
    if not slug:
        return BASE_URL
    if language_root.name == "pt":
        return BASE_URL + "pt-br/" + slug.strip("/") + "/"
    return BASE_URL + slug.strip("/") + "/"


def collect_posts() -> list[Post]:
    posts: list[Post] = []
    for content_dir in CONTENT_DIRS:
        if not content_dir.exists():
            continue
        for path in content_dir.rglob("*.md"):
            if path.name == "_index.md":
                continue
            fm = read_front_matter(path)
            if slug_for(path, content_dir).split("/")[0] not in {"blog", "posts", "microposts"}:
                continue
            tags = fm.get("tags") or []
            if isinstance(tags, str):
                tags = [tags]
            posts.append(
                Post(
                    path=path,
                    permalink=permalink_for(path, content_dir),
                    draft=bool(fm.get("draft", False)),
                    tags=[str(tag) for tag in tags],
                    federate=bool(fm.get("federate", False)),
                )
            )
    return posts


def parse_classes(path: Path) -> set[str]:
    parser = ClassParser()
    parser.feed(path.read_text(encoding="utf-8", errors="ignore"))
    return parser.classes


def parse_feed_urls(path: Path) -> set[str]:
    try:
        import xml.etree.ElementTree as ET

        root = ET.parse(path).getroot()
        return {item.findtext("link") or "" for item in root.findall("./channel/item")}
    except ImportError:
        # Some local Python/macOS combinations can have a broken pyexpat module.
        # Keep CI strict via ElementTree, but allow local validation to continue.
        text = path.read_text(encoding="utf-8")
        if not text.lstrip().startswith("<?xml") or "<rss" not in text or "</rss>" not in text:
            fail("newsletter feed does not look like RSS XML")
        parser = FeedLinkParser()
        parser.feed(text)
        return parser.links
    except ET.ParseError as exc:
        fail(f"newsletter feed is not well-formed XML: {exc}")


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-dir", default="public", help="Generated Hugo output directory")
    args = parser.parse_args()

    site_dir = Path(args.site_dir).resolve()
    if not site_dir.exists():
        fail(f"site directory does not exist: {site_dir}")

    required_pages = [
        "microposts/index.html",
        "updates/index.html",
        "books/index.html",
        "photos/index.html",
        "media/index.html",
        "papers/index.html",
        "pt-br/microposts/index.html",
        "pt-br/updates/index.html",
        "pt-br/books/index.html",
        "pt-br/photos/index.html",
        "pt-br/media/index.html",
        "pt-br/papers/index.html",
    ]
    for rel_path in required_pages:
        if not (site_dir / rel_path).exists():
            fail(f"required page was not generated: /{rel_path}")

    newsletter_feed = site_dir / "newsletter" / "index.xml"
    if not newsletter_feed.exists():
        fail("/newsletter/index.xml was not generated")

    feed_urls = parse_feed_urls(newsletter_feed)

    posts = collect_posts()
    for item in collect_content({"blog", "microposts"}):
        if item.post_kind == "micropost" and not item.draft:
            text = clean_markdown(item.body)
            if len(text) > 300:
                fail(f"micropost exceeds 300 characters: {item.key} ({len(text)})")

    newsletter_urls = {post.permalink for post in posts if not post.draft and "newsletter" in post.tags}
    draft_urls = {post.permalink for post in posts if post.draft}
    unexpected = feed_urls - newsletter_urls
    if unexpected:
        fail("newsletter feed contains non-newsletter or draft posts: " + ", ".join(sorted(unexpected)))

    drafts_in_feed = feed_urls & draft_urls
    if drafts_in_feed:
        fail("newsletter feed contains drafts: " + ", ".join(sorted(drafts_in_feed)))

    bad_patterns = ["/website/", "douglasferreira-me.github.io"]
    for path in site_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".html", ".xml", ".json", ".js", ".css", ".txt"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
            for pattern in bad_patterns:
                if pattern in text:
                    fail(f"found forbidden URL fragment {pattern!r} in {path.relative_to(site_dir)}")

    for post in posts:
        output = site_dir / post.permalink.removeprefix(BASE_URL) / "index.html"
        if not output.exists():
            continue
        html = output.read_text(encoding="utf-8", errors="ignore")
        has_bridgy = "u-bridgy-fed" in html
        if not post.federate and has_bridgy:
            fail(f"non-federated post contains u-bridgy-fed: {post.path}")
        if post.federate and not post.draft:
            if not has_bridgy:
                fail(f"federated post missing u-bridgy-fed: {post.path}")
            classes = parse_classes(output)
            for required in {"h-entry", "u-url", "dt-published", "e-content"}:
                if required not in classes:
                    fail(f"federated post missing {required}: {post.path}")

    print("Site validation passed.")


if __name__ == "__main__":
    main()
