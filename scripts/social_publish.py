#!/usr/bin/env python3
"""Publish selected Hugo posts to social networks and record syndication links."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from site_content import ROOT, clean_markdown, collect_content, utc_now


STATE_PATH = ROOT / "data" / "social" / "syndication.json"
MAX_MICROPOST_CHARS = 300


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    return json.loads(STATE_PATH.read_text(encoding="utf-8") or "{}")


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def request_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> tuple[dict[str, Any], dict[str, str]]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    with urllib.request.urlopen(req, timeout=30) as response:
        text = response.read().decode("utf-8")
        return (json.loads(text) if text else {}, dict(response.headers.items()))


def request_form(url: str, data: dict[str, str], headers: dict[str, str]) -> tuple[dict[str, Any], dict[str, str]]:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    for key, value in headers.items():
        req.add_header(key, value)
    with urllib.request.urlopen(req, timeout=30) as response:
        text = response.read().decode("utf-8")
        return (json.loads(text) if text else {}, dict(response.headers.items()))


def truncate_for_bluesky(text: str, permalink: str) -> str:
    if len(text) <= MAX_MICROPOST_CHARS:
        return text
    suffix = f"…\n\n{permalink}"
    room = MAX_MICROPOST_CHARS - len(suffix)
    return text[: max(room, 0)].rstrip() + suffix


def link_facets(text: str) -> list[dict[str, Any]]:
    facets: list[dict[str, Any]] = []
    for match in re.finditer(r"https?://[^\s]+", text):
        start = len(text[: match.start()].encode("utf-8"))
        end = len(text[: match.end()].encode("utf-8"))
        facets.append(
            {
                "index": {"byteStart": start, "byteEnd": end},
                "features": [{"$type": "app.bsky.richtext.facet#link", "uri": match.group(0)}],
            }
        )
    return facets


def compose_message(item: Any) -> str:
    text = clean_markdown(item.body)
    if item.post_kind == "micropost":
        if len(text) > MAX_MICROPOST_CHARS:
            raise ValueError(f"{item.key} is a micropost with {len(text)} characters; maximum is {MAX_MICROPOST_CHARS}")
        return text

    fm = item.front_matter
    lead = str(fm.get("social_text") or fm.get("description") or item.title).strip()
    intro = str(fm.get("social_intro") or "").strip()
    parts = [part for part in [intro, lead, item.permalink] if part]
    return "\n\n".join(parts)


def post_bluesky(text: str) -> dict[str, str]:
    handle = os.environ["BLUESKY_HANDLE"]
    password = os.environ["BLUESKY_APP_PASSWORD"]
    pds = os.environ.get("BLUESKY_PDS", "https://bsky.social").rstrip("/")
    session, _ = request_json(f"{pds}/xrpc/com.atproto.server.createSession", {"identifier": handle, "password": password})
    access = session["accessJwt"]
    did = session["did"]
    record = {
        "$type": "app.bsky.feed.post",
        "text": truncate_for_bluesky(text, text.split()[-1] if text.split() else ""),
        "createdAt": utc_now(),
    }
    facets = link_facets(record["text"])
    if facets:
        record["facets"] = facets
    payload = {"repo": did, "collection": "app.bsky.feed.post", "record": record}
    data, _ = request_json(
        f"{pds}/xrpc/com.atproto.repo.createRecord",
        payload,
        {"Authorization": f"Bearer {access}"},
    )
    rkey = data["uri"].rsplit("/", 1)[-1]
    return {"url": f"https://bsky.app/profile/{handle}/post/{rkey}", "uri": data["uri"], "cid": data.get("cid", "")}


def post_mastodon(text: str, lang: str) -> dict[str, str]:
    instance = os.environ["MASTODON_INSTANCE"].rstrip("/")
    token = os.environ["MASTODON_ACCESS_TOKEN"]
    status, _ = request_form(
        f"{instance}/api/v1/statuses",
        {"status": text, "visibility": "public", "language": "pt" if lang.lower().startswith("pt") else "en"},
        {"Authorization": f"Bearer {token}", "Idempotency-Key": hashlib.sha256(text.encode("utf-8")).hexdigest()},
    )
    return {"url": status.get("url") or status.get("uri") or "", "id": str(status.get("id") or "")}


def post_linkedin(text: str) -> dict[str, str]:
    token = os.environ["LINKEDIN_ACCESS_TOKEN"]
    author = os.environ["LINKEDIN_AUTHOR_URN"]
    version = os.environ.get("LINKEDIN_VERSION", "202606")
    payload = {
        "author": author,
        "commentary": text,
        "visibility": "PUBLIC",
        "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    _, headers = request_json(
        "https://api.linkedin.com/rest/posts",
        payload,
        {
            "Authorization": f"Bearer {token}",
            "Linkedin-Version": version,
            "X-Restli-Protocol-Version": "2.0.0",
        },
    )
    post_id = headers.get("x-restli-id") or headers.get("X-RestLi-Id") or headers.get("Location", "")
    return {"url": post_id, "id": post_id}


def wants(item: Any, service: str) -> bool:
    return bool(item.front_matter.get(f"syndicate_{service}", False))


def missing_env(service: str) -> list[str]:
    required = {
        "bluesky": ["BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD"],
        "mastodon": ["MASTODON_INSTANCE", "MASTODON_ACCESS_TOKEN"],
        "linkedin": ["LINKEDIN_ACCESS_TOKEN", "LINKEDIN_AUTHOR_URN"],
    }[service]
    return [name for name in required if not os.environ.get(name)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    state = load_state()
    changed = False
    failures: list[str] = []
    publishers = {"bluesky": post_bluesky, "mastodon": post_mastodon, "linkedin": post_linkedin}

    for item in collect_content({"blog", "microposts"}):
        if item.draft:
            print(f"Skipping {item.key}: draft is true")
            continue
        services = [service for service in publishers if wants(item, service)]
        if not services:
            print(f"Skipping {item.key}: no syndicate_* flag is enabled")
            continue
        try:
            message = compose_message(item)
        except ValueError as exc:
            failures.append(str(exc))
            continue

        record = state.setdefault(item.key, {"permalink": item.permalink, "title": item.title, "targets": {}, "comments": []})
        record["permalink"] = item.permalink
        record["title"] = item.title
        record.setdefault("targets", {})

        for service in services:
            if record["targets"].get(service, {}).get("url"):
                print(f"Skipping {service} for {item.key}: already published")
                continue
            missing = missing_env(service)
            if missing:
                print(f"Skipping {service} for {item.key}: missing {', '.join(missing)}")
                continue
            if args.dry_run:
                print(f"Would publish {item.key} to {service}: {message[:80]!r}")
                continue
            try:
                if service == "mastodon":
                    result = post_mastodon(message, item.lang)
                else:
                    result = publishers[service](message)
                result["published_at"] = utc_now()
                record["targets"][service] = result
                changed = True
                print(f"Published {item.key} to {service}: {result.get('url')}")
            except (urllib.error.URLError, KeyError, ValueError) as exc:
                failures.append(f"{service} failed for {item.key}: {exc}")

    if failures:
        for failure in failures:
            print(f"ERROR: {failure}", file=sys.stderr)
        raise SystemExit(1)
    if changed and not args.dry_run:
        save_state(state)


if __name__ == "__main__":
    main()
