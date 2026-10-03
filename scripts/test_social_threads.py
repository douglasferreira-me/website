"""Offline regression tests: never contact or publish to social networks."""
import datetime as dt
import unittest
from pathlib import Path
from unittest.mock import patch
import social_publish as social
import translate_pt_to_en as translate
from site_content import ContentItem


def part(index):
    return ContentItem(path=social.ROOT / f"content/pt/microposts/test-{index:02d}.md", lang="pt-BR", section="microposts", slug=f"test-{index}", permalink=f"https://example.org/{index}",
        front_matter={"date": dt.datetime(2026, 10, 3), "lang": "pt-BR", "post_kind": "micropost", "thread_id": "fixture", "thread_index": index, "thread_total": 2, "syndicate_bluesky": True, "syndicate_mastodon": True}, body=f"Parte {index} ({index}/2)")


class ThreadTests(unittest.TestCase):
    def test_thread_metadata_translated(self):
        fm = translate.build_front_matter(part(1), {"title": "", "description": "", "body": "Part 1 (1/2)"})
        self.assertEqual(fm["thread_id"], "fixture")
        self.assertEqual(fm["thread_index"], 1)
        self.assertEqual(fm["thread_total"], 2)

    def test_order_and_parent(self):
        items = [part(2), part(1)]
        state, bsky_calls, masto_calls = {}, [], []
        def bsky(message, identity, reply):
            bsky_calls.append(reply)
            return {"uri": "at://test/" + identity, "cid": "cid-" + identity, "url": "https://example.org"}
        def masto(message, lang, identity, parent):
            masto_calls.append(parent)
            return {"id": identity, "url": "https://example.org"}
        with patch.object(social, "collect_content", return_value=items), patch.object(social, "load_state", return_value=state), patch.object(social, "save_state"), patch.object(social, "missing_env", return_value=[]), patch.object(social, "post_bluesky", side_effect=bsky), patch.object(social, "post_mastodon", side_effect=masto), patch("sys.argv", ["social_publish.py"]):
            social.main()
            self.assertIsNone(bsky_calls[0])
            self.assertEqual(bsky_calls[1]["parent"]["uri"], "at://test/" + part(1).key)
            self.assertEqual(masto_calls, ["", part(1).key])
            social.main()  # Resuming an already-published chain must skip every target.
            self.assertEqual(len(bsky_calls), 2)


if __name__ == "__main__":
    unittest.main()
