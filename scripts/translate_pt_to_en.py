#!/usr/bin/env python3
"""Translate published Portuguese posts to English using the OpenAI Responses API."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import urllib.request
from collections import OrderedDict
from pathlib import Path
from typing import Any

from site_content import ROOT, clean_markdown, collect_content, split_front_matter, write_toml_markdown


OPENAI_URL = "https://api.openai.com/v1/responses"


def source_hash(item: Any) -> str:
    return hashlib.sha256(json.dumps({"meta": item.front_matter, "body": item.body}, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def generated_hash(front: dict, body: str) -> str:
    # TOML writer serializes None as an empty string. Hash the persisted form.
    public = {k: ("" if v is None else v) for k, v in front.items() if k not in ("auto_generated_hash", "translation_source_hash")}
    return hashlib.sha256(json.dumps({"meta": public, "body": body.strip()}, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "translated-post"


def extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    return json.loads(text)


def openai_translate(title: str, description: str, body: str, micropost: bool = False) -> dict[str, str]:
    api_key = os.environ["OPENAI_API_KEY"]
    model = os.environ.get("OPENAI_MODEL", "gpt-5.6")
    instructions = (
        "Translate Brazilian Portuguese blog content into natural English. "
        "Preserve Markdown structure, links, headings, code blocks, and the author's voice. "
        "Return only JSON with string keys title, description, body."
    )
    if micropost:
        instructions += " This is a micropost: keep the English body at most 300 Unicode code points, including any (N/total) suffix. Preserve that suffix exactly. Condense wording without cutting meaning."
    payload = {
        "model": model,
        "instructions": instructions,
        "input": json.dumps({"title": title, "description": description, "body": body}, ensure_ascii=False),
    }
    req = urllib.request.Request(OPENAI_URL, data=json.dumps(payload).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=120) as response:
        data = json.loads(response.read().decode("utf-8"))
    output_text = data.get("output_text")
    if not output_text:
        parts: list[str] = []
        for item in data.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    parts.append(content.get("text", ""))
        output_text = "\n".join(parts)
    translated = extract_json(output_text or "{}")
    return {
        "title": str(translated.get("title") or title),
        "description": str(translated.get("description") or description),
        "body": str(translated.get("body") or body),
    }


def target_path_for(item: Any, translated_title: str) -> Path:
    return ROOT / "content" / "en" / item.section / f"{item.path.stem}.md"


def can_overwrite(path: Path) -> bool:
    if not path.exists():
        return True
    fm, _, _ = split_front_matter(path)
    return bool(fm.get("auto_translated", False))


def has_tag(item: Any, tag: str) -> bool:
    return any(str(value).casefold() == tag.casefold() for value in (item.front_matter.get("tags") or []))


def build_front_matter(item: Any, translated: dict[str, str]) -> OrderedDict[str, Any]:
    fm = item.front_matter
    is_micropost = (fm.get("post_kind") or item.post_kind) == "micropost"
    result: OrderedDict[str, Any] = OrderedDict()
    result["title"] = "" if is_micropost else translated["title"]
    result["date"] = fm.get("date")
    result["draft"] = False
    result["description"] = translated["description"]
    result["tags"] = fm.get("tags") or []
    result["categories"] = fm.get("categories") or []
    result["lang"] = "en"
    result["translation_of"] = item.permalink
    result["auto_translated"] = True
    result["post_kind"] = fm.get("post_kind") or item.post_kind
    for key in ("silverbullet_id", "thread_id", "thread_index", "thread_total"):
        if key in fm:
            result[key] = fm[key]
    result["federate"] = fm.get("federate", True)
    result["syndicate_bluesky"] = fm.get("syndicate_bluesky", True)
    result["syndicate_mastodon"] = fm.get("syndicate_mastodon", True)
    result["syndicate_threads"] = fm.get("syndicate_threads", fm.get("syndicate_bluesky", True) or fm.get("syndicate_mastodon", True))
    result["syndicate_linkedin"] = fm.get("syndicate_linkedin", False)
    result["social_text"] = ""
    result["social_intro"] = ""
    result["image"] = fm.get("image") or ""
    result["external_image"] = fm.get("external_image") or ""
    result["cover"] = fm.get("cover") or ""
    result["caption"] = fm.get("caption") or ""
    result["photo_caption"] = fm.get("photo_caption") or ""
    result["show_in_updates"] = fm.get("show_in_updates", True)
    result["show_image_in_photos"] = fm.get("show_image_in_photos", False)
    result["author"] = fm.get("author") or ""
    result["status"] = fm.get("status") or ""
    result["started"] = fm.get("started")
    result["finished"] = fm.get("finished")
    result["rating"] = fm.get("rating")
    result["outlet"] = fm.get("outlet") or ""
    result["external_url"] = fm.get("external_url") or ""
    return result


def copy_media_assets(item: Any, target: Path) -> None:
    for key in ("image", "cover"):
        value = str(item.front_matter.get(key) or "")
        if not value or value.startswith(("http://", "https://", "/")):
            continue
        source = item.path.parent / value
        if source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target.parent / value)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        print("Skipping translation: OPENAI_API_KEY is not set.")
        return

    changed = False
    for item in collect_content({"blog", "microposts", "books", "photos", "media"}):
        target = target_path_for(item, item.title)
        is_blog = item.section == "blog" and item.lang.lower().startswith("pt")
        existing, existing_body = {}, ""
        if is_blog and target.exists():
            existing, existing_body, _ = split_front_matter(target)
            if not existing.get("auto_translated"):
                print(f"Preserving manual English post for {item.key}")
                continue
            previous = existing.get("auto_generated_hash")
            if previous and generated_hash(existing, existing_body) != previous:
                print(f"English post manually edited; protecting {item.key}")
                existing["auto_translated"] = False
                if not args.dry_run:
                    write_toml_markdown(target, existing, existing_body)
                    changed = True
                continue
            if item.draft:
                if not existing.get("draft") and not args.dry_run:
                    existing["draft"] = True
                    existing["auto_generated_hash"] = generated_hash(existing, existing_body)
                    write_toml_markdown(target, existing, existing_body)
                    changed = True
                continue
            if existing.get("translation_source_hash") == source_hash(item) and not existing.get("draft"):
                continue
        if item.draft:
            print(f"Skipping {item.key}: draft is true")
            continue
        if not item.lang.lower().startswith("pt"):
            continue
        if has_tag(item, "poesia"):
            print(f"Skipping {item.key}: tag poesia is not auto-translated")
            continue
        target = target_path_for(item, item.title)
        if item.post_kind == "micropost" and target.exists() and item.front_matter.get("silverbullet_id"):
            existing, _, _ = split_front_matter(target)
            if existing.get("auto_translated") and existing.get("silverbullet_id") == item.front_matter["silverbullet_id"]:
                continue  # Published microposts are immutable; avoid translating again.
        translated = openai_translate(item.title, str(item.front_matter.get("description") or ""), item.body, item.post_kind == "micropost")
        if item.post_kind == "micropost":
            for attempt in range(2):
                if len(clean_markdown(translated["body"])) <= 300:
                    break
                translated = openai_translate(item.title, "Condense English translation to fit 300 characters.", item.body, True)
            if len(clean_markdown(translated["body"])) > 300:
                raise ValueError(f"English translation exceeds 300 characters: {item.key}; refusing truncation")
            if item.front_matter.get("thread_id"):
                suffix = f'({item.front_matter["thread_index"]}/{item.front_matter["thread_total"]})'
                if not translated["body"].strip().endswith(suffix):
                    raise ValueError(f"Translation lost thread numbering: {item.key}")
        target = target_path_for(item, translated["title"])
        if not can_overwrite(target):
            print(f"Skipping {target.relative_to(ROOT)}: existing manual English file is not auto_translated.")
            continue
        print(f"Writing translation for {item.key} -> {target.relative_to(ROOT)}")
        if not args.dry_run:
            copy_media_assets(item, target)
            front = build_front_matter(item, translated)
            if is_blog:
                front["translation_source_hash"] = source_hash(item)
                front["auto_generated_hash"] = generated_hash(front, translated["body"])
            write_toml_markdown(target, front, translated["body"])
            changed = True

    if not changed:
        print("No translations changed.")


if __name__ == "__main__":
    try:
        main()
    except json.JSONDecodeError as exc:
        print(f"ERROR: OpenAI translation did not return valid JSON: {exc}", file=sys.stderr)
        raise SystemExit(1)
