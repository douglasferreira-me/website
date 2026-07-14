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


class HeadMetadataParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.canonical: str | None = None
        self.description: str | None = None
        self.hreflangs: set[str] = set()
        self.json_ld_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        if tag == "link":
            rels = set(values.get("rel", "").split())
            if "canonical" in rels:
                self.canonical = values.get("href") or None
            if "alternate" in rels and values.get("hreflang"):
                self.hreflangs.add(values["hreflang"])
        elif tag == "meta" and values.get("name") == "description":
            self.description = values.get("content") or None
        elif tag == "script" and values.get("type") == "application/ld+json":
            self.json_ld_count += 1


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


def parse_head_metadata(path: Path) -> HeadMetadataParser:
    parser = HeadMetadataParser()
    parser.feed(path.read_text(encoding="utf-8", errors="ignore"))
    return parser


def public_html_pages(site_dir: Path) -> list[Path]:
    skipped = {"404.html"}
    pages: list[Path] = []
    for path in site_dir.rglob("*.html"):
        rel = path.relative_to(site_dir).as_posix()
        if rel in skipped or rel.startswith("admin/"):
            continue
        pages.append(path)
    return pages


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
        "robots.txt",
        "sitemap.xml",
        "llms.txt",
        "llms-full.txt",
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
            fail(f"required file was not generated: /{rel_path}")

    robots_text = (site_dir / "robots.txt").read_text(encoding="utf-8", errors="ignore")
    for required in ["Sitemap: https://douglasferreira.me/sitemap.xml", "LLMs: https://douglasferreira.me/llms.txt", "Disallow: /admin/"]:
        if required not in robots_text:
            fail(f"robots.txt is missing {required!r}")

    for rel_path in ["llms.txt", "llms-full.txt"]:
        text = (site_dir / rel_path).read_text(encoding="utf-8", errors="ignore")
        if "https://douglasferreira.me/" not in text:
            fail(f"/{rel_path} does not include canonical site URLs")

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
        if (
            item.section == "blog"
            and item.path.relative_to(ROOT).parts[:2] == ("content", "pt")
            and not item.draft
            and "poesia" not in {str(tag).casefold() for tag in (item.front_matter.get("tags") or [])}
        ):
            expected = ROOT / "content" / "en" / "blog" / item.path.name
            if not expected.exists():
                fail(f"published Portuguese writing is missing English translation: {item.key}")
        if (
            item.section == "blog"
            and item.path.relative_to(ROOT).parts[:2] == ("content", "en")
            and bool(item.front_matter.get("auto_translated", False))
            and "poesia" in {str(tag).casefold() for tag in (item.front_matter.get("tags") or [])}
        ):
            fail(f"auto-translated poetry must not be published in English: {item.key}")

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

    sitemap_text = (site_dir / "sitemap.xml").read_text(encoding="utf-8", errors="ignore")
    if "/admin/" in sitemap_text:
        fail("/admin/ appears in sitemap.xml")

    metadata_required = [
        "index.html",
        "pt-br/index.html",
        "papers/index.html",
        "newsletter/index.html",
    ]
    for rel_path in metadata_required:
        metadata = parse_head_metadata(site_dir / rel_path)
        if not metadata.canonical:
            fail(f"page is missing canonical link: /{rel_path}")
        if not metadata.description:
            fail(f"page is missing meta description: /{rel_path}")
        if metadata.json_ld_count < 1:
            fail(f"page is missing JSON-LD: /{rel_path}")

    for page in public_html_pages(site_dir):
        html = page.read_text(encoding="utf-8", errors="ignore")
        if "http-equiv=refresh" in html or 'http-equiv="refresh"' in html:
            continue
        if "<script type=application/ld+json>" not in html and '<script type="application/ld+json">' not in html:
            fail(f"public page is missing JSON-LD: /{page.relative_to(site_dir)}")

    translation_pairs = [
        ("index.html", {"en", "pt-br", "x-default"}),
        ("pt-br/index.html", {"en", "pt-br", "x-default"}),
        ("papers/index.html", {"en", "pt-br", "x-default"}),
        ("pt-br/papers/index.html", {"en", "pt-br", "x-default"}),
    ]
    for rel_path, expected_hreflangs in translation_pairs:
        metadata = parse_head_metadata(site_dir / rel_path)
        missing = expected_hreflangs - metadata.hreflangs
        if missing:
            fail(f"page is missing hreflang values {sorted(missing)}: /{rel_path}")

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
