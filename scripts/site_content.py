#!/usr/bin/env python3
"""Shared helpers for Hugo content automation."""

from __future__ import annotations

import datetime as dt
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://douglasferreira.me/"
CONTENT_ROOTS = {
    "en": ROOT / "content" / "en",
    "pt-BR": ROOT / "content" / "pt",
}


@dataclass
class ContentItem:
    path: Path
    lang: str
    section: str
    slug: str
    permalink: str
    front_matter: dict[str, Any]
    body: str

    @property
    def key(self) -> str:
        return self.path.relative_to(ROOT).as_posix()

    @property
    def draft(self) -> bool:
        return bool(self.front_matter.get("draft", False))

    @property
    def title(self) -> str:
        return str(self.front_matter.get("title") or self.path.stem)

    @property
    def post_kind(self) -> str:
        if self.section == "microposts":
            return "micropost"
        return str(self.front_matter.get("post_kind") or "blogpost")


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


def split_front_matter(path: Path) -> tuple[dict[str, Any], str, str]:
    text = path.read_text(encoding="utf-8")
    if text.startswith("+++"):
        end = text.find("\n+++", 3)
        if end == -1:
            return {}, text, "toml"
        return tomllib.loads(text[3:end]), text[end + 4 :].lstrip("\n"), "toml"
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end == -1:
            return {}, text, "yaml"
        return parse_simple_yaml(text[3:end]), text[end + 4 :].lstrip("\n"), "yaml"
    return {}, text, "none"


def clean_markdown(markdown: str) -> str:
    text = re.sub(r"```.*?```", "", markdown, flags=re.S)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"[*_~>#-]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def slug_for(path: Path, language_root: Path) -> str:
    rel = path.relative_to(language_root).with_suffix("")
    parts = rel.parts
    if parts[-1] == "_index":
        parts = parts[:-1]
    return "/".join(parts)


def permalink_for(path: Path, language_root: Path) -> str:
    slug = slug_for(path, language_root).strip("/")
    if not slug:
        return BASE_URL
    if language_root.name == "pt":
        return f"{BASE_URL}pt-br/{slug}/"
    return f"{BASE_URL}{slug}/"


def collect_content(sections: set[str] | None = None) -> list[ContentItem]:
    items: list[ContentItem] = []
    for lang, root in CONTENT_ROOTS.items():
        if not root.exists():
            continue
        for path in root.rglob("*.md"):
            if path.name == "_index.md":
                continue
            slug = slug_for(path, root)
            section = slug.split("/", 1)[0]
            if sections and section not in sections:
                continue
            fm, body, _ = split_front_matter(path)
            items.append(
                ContentItem(
                    path=path,
                    lang=str(fm.get("lang") or lang),
                    section=section,
                    slug=slug,
                    permalink=permalink_for(path, root),
                    front_matter=fm,
                    body=body,
                )
            )
    return items


def toml_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    if value is None:
        return '""'
    return toml_quote(str(value))


def write_toml_markdown(path: Path, front_matter: dict[str, Any], body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["+++"]
    for key, value in front_matter.items():
        lines.append(f"{key} = {toml_value(value)}")
    lines.append("+++")
    lines.append("")
    lines.append(body.strip() + "\n")
    path.write_text("\n".join(lines), encoding="utf-8")


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
