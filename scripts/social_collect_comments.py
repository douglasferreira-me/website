#!/usr/bin/env python3
"""Collect public replies from syndicated Mastodon and Bluesky posts."""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.parse
import urllib.request
from typing import Any

from site_content import ROOT, clean_markdown, utc_now
from social_publish import STATE_PATH, load_state, save_state


TAG_RE = re.compile(r"<[^>]+>")


def get_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    req = urllib.request.Request(url)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def html_to_text(html: str) -> str:
    return clean_markdown(TAG_RE.sub(" ", html))


def mastodon_replies(target: dict[str, str]) -> list[dict[str, str]]:
    instance = os.environ.get("MASTODON_INSTANCE", "").strip().rstrip("/")
    token = os.environ.get("MASTODON_ACCESS_TOKEN", "").strip()
    status_id = target.get("id")
    if not instance or not token or not status_id:
        return []
    data = get_json(f"{instance}/api/v1/statuses/{status_id}/context", {"Authorization": f"Bearer {token}"})
    replies: list[dict[str, str]] = []
    for item in data.get("descendants", []):
        account = item.get("account") or {}
        replies.append(
            {
                "source": "mastodon",
                "author": account.get("display_name") or account.get("acct") or "Mastodon user",
                "url": item.get("url") or item.get("uri") or "",
                "published": item.get("created_at") or "",
                "text": html_to_text(item.get("content") or ""),
            }
        )
    return replies


def bluesky_replies(target: dict[str, str]) -> list[dict[str, str]]:
    uri = target.get("uri")
    if not uri:
        return []
    params = urllib.parse.urlencode({"uri": uri, "depth": "2"})
    data = get_json(f"https://public.api.bsky.app/xrpc/app.bsky.feed.getPostThread?{params}")
    replies: list[dict[str, str]] = []
    for reply in (data.get("thread") or {}).get("replies") or []:
        post = reply.get("post") or {}
        record = post.get("record") or {}
        author = post.get("author") or {}
        handle = author.get("handle") or "Bluesky user"
        rkey = (post.get("uri") or "").rsplit("/", 1)[-1]
        replies.append(
            {
                "source": "bluesky",
                "author": author.get("displayName") or handle,
                "url": f"https://bsky.app/profile/{handle}/post/{rkey}" if rkey else "",
                "published": record.get("createdAt") or "",
                "text": record.get("text") or "",
            }
        )
    return replies


def dedupe(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for item in sorted(items, key=lambda entry: entry.get("published", "")):
        key = item.get("url") or json.dumps(item, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not STATE_PATH.exists():
        print("No syndication state found.")
        return

    state = load_state()
    changed = False
    for key, record in state.items():
        targets = record.get("targets") or {}
        comments: list[dict[str, str]] = []
        if targets.get("mastodon"):
            comments.extend(mastodon_replies(targets["mastodon"]))
        if targets.get("bluesky"):
            comments.extend(bluesky_replies(targets["bluesky"]))
        comments = dedupe([item for item in comments if item.get("text") or item.get("url")])
        if comments != record.get("comments", []):
            print(f"Collected {len(comments)} replies for {key}")
            record["comments"] = comments
            record["comments_collected_at"] = utc_now()
            changed = True

    if changed and not args.dry_run:
        save_state(state)


if __name__ == "__main__":
    main()
