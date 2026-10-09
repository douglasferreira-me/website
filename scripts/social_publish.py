#!/usr/bin/env python3
"""Publish selected Hugo posts to social networks and record syndication links."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
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
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            text = response.read().decode("utf-8")
            return (json.loads(text) if text else {}, dict(response.headers.items()))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc


def request_form(url: str, data: dict[str, str], headers: dict[str, str]) -> tuple[dict[str, Any], dict[str, str]]:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            text = response.read().decode("utf-8")
            return (json.loads(text) if text else {}, dict(response.headers.items()))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc


def env_value(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


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


def bluesky_record_key(identity: str) -> str:
    alphabet = "234567abcdefghijklmnopqrstuvwxyz"
    value = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big") & ((1 << 63) - 1)
    return "".join(alphabet[(value >> shift) & 31] for shift in range(60, -1, -5))


def post_bluesky(text: str, identity: str = "", reply: dict | None = None) -> dict[str, str]:
    handle = env_value("BLUESKY_HANDLE").removeprefix("@")
    password = env_value("BLUESKY_APP_PASSWORD")
    pds = env_value("BLUESKY_PDS", "https://bsky.social").rstrip("/")
    session, _ = request_json(f"{pds}/xrpc/com.atproto.server.createSession", {"identifier": handle, "password": password})
    access = session["accessJwt"]
    did = session["did"]
    rkey = bluesky_record_key(identity) if identity else ""
    if rkey:
        query = urllib.parse.urlencode({"repo": did, "collection": "app.bsky.feed.post", "rkey": rkey})
        req = urllib.request.Request(f"{pds}/xrpc/com.atproto.repo.getRecord?{query}", headers={"Authorization": f"Bearer {access}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                existing = json.load(response)
            if existing["value"]["text"] != truncate_for_bluesky(text, text.split()[-1] if text.split() else ""):
                raise RuntimeError("Published record differs; refusing to overwrite")
            return {"url": f"https://bsky.app/profile/{handle}/post/{rkey}", "uri": existing["uri"], "cid": existing["cid"]}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                error = json.loads(detail).get("error")
            except (ValueError, AttributeError):
                error = None
            # Creating with the same deterministic key cannot duplicate a record.
            if exc.code not in (404, 500, 502, 503, 504) and not (exc.code == 400 and error == "RecordNotFound"):
                raise RuntimeError(f"HTTP {exc.code} checking Bluesky record: {detail}") from exc
    record = {
        "$type": "app.bsky.feed.post",
        "text": truncate_for_bluesky(text, text.split()[-1] if text.split() else ""),
        "createdAt": utc_now(),
    }
    facets = link_facets(record["text"])
    if reply:
        record["reply"] = reply
    if facets:
        record["facets"] = facets
    payload = {"repo": did, "collection": "app.bsky.feed.post", "record": record}
    if rkey:
        payload["rkey"] = rkey
    data, _ = request_json(
        f"{pds}/xrpc/com.atproto.repo.createRecord",
        payload,
        {"Authorization": f"Bearer {access}"},
    )
    rkey = data["uri"].rsplit("/", 1)[-1]
    return {"url": f"https://bsky.app/profile/{handle}/post/{rkey}", "uri": data["uri"], "cid": data.get("cid", "")}


def post_mastodon(text: str, lang: str, identity: str = "", parent: str = "") -> dict[str, str]:
    instance = env_value("MASTODON_INSTANCE").rstrip("/")
    token = env_value("MASTODON_ACCESS_TOKEN")
    status, _ = request_form(
        f"{instance}/api/v1/statuses",
        {"status": text, "visibility": "public", "language": "pt" if lang.lower().startswith("pt") else "en", **({"in_reply_to_id": parent} if parent else {})},
        {"Authorization": f"Bearer {token}", "Idempotency-Key": hashlib.sha256((identity or text).encode("utf-8")).hexdigest()},
    )
    return {"url": status.get("url") or status.get("uri") or "", "id": str(status.get("id") or "")}


def post_linkedin(text: str) -> dict[str, str]:
    token = env_value("LINKEDIN_ACCESS_TOKEN")
    author = env_value("LINKEDIN_AUTHOR_URN")
    version = env_value("LINKEDIN_VERSION", "202606")
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
    if service == "threads":
        return bool(item.front_matter.get("syndicate_threads", item.front_matter.get("syndicate_bluesky", False) or item.front_matter.get("syndicate_mastodon", False)))
    return bool(item.front_matter.get(f"syndicate_{service}", False))


def threads_get(path: str, fields: str) -> dict[str, Any]:
    query = urllib.parse.urlencode({"fields": fields})
    req = urllib.request.Request(f"https://graph.threads.net/v1.0/{path}?{query}", headers={"Authorization": f"Bearer {env_value('THREADS_ACCESS_TOKEN')}"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def post_threads(text: str, permalink: str, target: dict[str, Any], persist: Any, parent: str = "") -> dict[str, Any]:
    user = env_value("THREADS_USER_ID")
    headers = {"Authorization": f"Bearer {env_value('THREADS_ACCESS_TOKEN')}"}
    if len(text) > 500:
        suffix = "\n\n" + permalink
        if len(suffix) >= 500:
            raise ValueError("Canonical URL exceeds the Threads text limit")
        text = text[:500 - len(suffix) - 1].rstrip() + "…" + suffix
    fingerprint = hashlib.sha256(text.encode()).hexdigest()
    if target.get("text_hash") and target["text_hash"] != fingerprint:
        raise ValueError("Threads pending content changed; resolve the pending publication before editing")
    if not target.get("container_id"):
        payload = {"media_type": "TEXT", "text": text, "crossreshare_to_ig": "true", **({"reply_to_id": parent} if parent else {})}
        try:
            data, _ = request_form(f"https://graph.threads.net/v1.0/{user}/threads", payload, headers)
            target["instagram_story_requested"] = True
        except RuntimeError as exc:
            if '"code":10' not in str(exc).replace(" ", ""):
                raise
            del payload["crossreshare_to_ig"]
            data, _ = request_form(f"https://graph.threads.net/v1.0/{user}/threads", payload, headers)
            target["instagram_story_requested"] = False
            target["instagram_story_error"] = "Meta denied Instagram sharing permission; reauthorize threads_share_to_instagram"
        target.update(container_id=str(data["id"]), text_hash=fingerprint, started_at=utc_now())
        persist()
    if not target.get("id"):
        status = threads_get(target["container_id"], "status,error_message").get("status")
        for _ in range(10):
            if status != "IN_PROGRESS":
                break
            time.sleep(3)
            status = threads_get(target["container_id"], "status,error_message").get("status")
        if status == "PUBLISHED" or target.get("publish_attempted"):
            candidates = threads_get(f"{user}/threads", "id,text,permalink,timestamp").get("data", [])
            started = dt.datetime.fromisoformat(target["started_at"].replace("Z", "+00:00"))
            matches = [post for post in candidates if post.get("text") == text and post.get("timestamp") and dt.datetime.fromisoformat(post["timestamp"].replace("Z", "+00:00")) >= started]
            if len(matches) != 1:
                raise RuntimeError("Threads publication outcome is ambiguous; refusing to publish again. Check the account and pending container.")
            target.update(id=str(matches[0]["id"]), url=matches[0].get("permalink", ""))
            persist()
        elif status == "FINISHED":
            target["publish_attempted"] = True
            persist()
            time.sleep(3)
            try:
                data, _ = request_form(f"https://graph.threads.net/v1.0/{user}/threads_publish", {"creation_id": target["container_id"]}, headers)
            except RuntimeError as exc:
                if '"code":24' in str(exc).replace(" ", ""):
                    target["publish_attempted"] = False
                    persist()
                raise
            target["id"] = str(data["id"])
            persist()
        else:
            raise RuntimeError(f"Threads container is {status}; retry once processing finishes or inspect its error")
    if not target.get("url"):
        target["url"] = threads_get(target["id"], "permalink")["permalink"]
        persist()
    return target


def missing_env(service: str) -> list[str]:
    required = {
        "bluesky": ["BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD"],
        "mastodon": ["MASTODON_INSTANCE", "MASTODON_ACCESS_TOKEN"],
        "linkedin": ["LINKEDIN_ACCESS_TOKEN", "LINKEDIN_AUTHOR_URN"],
        "threads": ["THREADS_USER_ID", "THREADS_ACCESS_TOKEN"],
    }[service]
    return [name for name in required if not env_value(name)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--lang-prefix", default="", help="Only publish items whose lang starts with this value.")
    parser.add_argument("--only-auto-translated", action="store_true")
    args = parser.parse_args()

    state = load_state()
    changed = False
    failures: list[str] = []
    publishers = {"bluesky": post_bluesky, "mastodon": post_mastodon, "linkedin": post_linkedin, "threads": post_threads}

    items = sorted(collect_content({"blog", "microposts"}), key=lambda item: (str(item.front_matter.get("date", "")), str(item.front_matter.get("thread_id", "")), int(item.front_matter.get("thread_index", 0)), item.key))
    threads = {}
    for item in items:
        if item.front_matter.get("thread_id"):
            threads.setdefault((item.lang, item.front_matter["thread_id"]), []).append(item)
    for item in items:
        if item.draft:
            print(f"Skipping {item.key}: draft is true")
            continue
        if args.lang_prefix and not item.lang.lower().startswith(args.lang_prefix.lower()):
            print(f"Skipping {item.key}: lang {item.lang} does not match {args.lang_prefix}")
            continue
        if args.only_auto_translated and not bool(item.front_matter.get("auto_translated", False)):
            print(f"Skipping {item.key}: not auto_translated")
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
            if record["targets"].get(service, {}).get("url") or (service != "threads" and record["targets"].get(service, {}).get("id")):
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
                root_target, parent_target = {}, {}
                if item.front_matter.get("thread_id"):
                    chain = threads[(item.lang, item.front_matter["thread_id"])]
                    indexes = [int(part.front_matter.get("thread_index", 0)) for part in chain]
                    if indexes != list(range(1, int(item.front_matter["thread_total"]) + 1)):
                        raise ValueError("Thread missing parts or out of order")
                    position = int(item.front_matter["thread_index"])
                    if position > 1:
                        root_target = state.get(chain[0].key, {}).get("targets", {}).get(service, {})
                        parent_target = state.get(chain[position - 2].key, {}).get("targets", {}).get(service, {})
                        required = ("uri", "cid") if service == "bluesky" else ("id",)
                        if not all(parent_target.get(key) and root_target.get(key) for key in required):
                            raise ValueError("Thread parent not published; refusing standalone reply")
                if service == "threads":
                    target = record["targets"].setdefault("threads", {})
                    result = post_threads(message, item.permalink, target, lambda: save_state(state), parent_target.get("id", ""))
                elif service == "mastodon":
                    result = post_mastodon(message, item.lang, item.key, parent_target.get("id", ""))
                elif service == "bluesky":
                    reply = {"root": {key: root_target[key] for key in ("uri", "cid")}, "parent": {key: parent_target[key] for key in ("uri", "cid")}} if parent_target else None
                    result = post_bluesky(message, item.key, reply)
                else:
                    result = publishers[service](message)
                result["published_at"] = utc_now()
                record["targets"][service] = result
                changed = True
                save_state(state)  # Persist each success, even when the next part fails.
                print(f"Published {item.key} to {service}: {result.get('url')}")
                if service == "threads" and result.get("instagram_story_error"):
                    failures.append(f"Instagram Story for {item.key}: {result['instagram_story_error']}; Threads publication succeeded")
            except (RuntimeError, urllib.error.URLError, KeyError, ValueError) as exc:
                failures.append(f"{service} failed for {item.key}: {exc}")

    if changed and not args.dry_run:
        save_state(state)
    if failures:
        for failure in failures:
            print(f"ERROR: {failure}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
