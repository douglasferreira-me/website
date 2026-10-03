"""Offline editorial translation tests. No API requests or real content writes."""
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import site_content
import translate_pt_to_en as translate


class BlogTranslationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.item = site_content.ContentItem(path=self.root / "content/pt/blog/post.md", lang="pt-BR", section="blog", slug="post", permalink="https://example.org/pt-br/blog/post/", front_matter={"title": "Título", "date": dt.datetime(2026, 10, 3), "draft": False, "post_kind": "blogpost", "silverbullet_id": "fixture", "federate": False}, body="Texto português")
        self.target = self.root / "content/en/blog/post.md"
        self.patches = [patch.object(translate, "ROOT", self.root), patch.object(site_content, "ROOT", self.root), patch.object(translate, "collect_content", return_value=[self.item]), patch.dict("os.environ", {"OPENAI_API_KEY": "fixture"}), patch("sys.argv", ["translate_pt_to_en.py"])]
        for p in self.patches: p.start()
        self.api = patch.object(translate, "openai_translate", return_value={"title": "Title", "description": "Summary", "body": "English text"}).start()

    def tearDown(self):
        patch.stopall()
        self.temp.cleanup()

    def test_revisions_retranslate_without_duplicate_calls(self):
        translate.main(); translate.main()
        self.assertEqual(self.api.call_count, 1)
        front, body, _ = site_content.split_front_matter(self.target)
        self.assertFalse(front["federate"])
        self.assertEqual(front["auto_generated_hash"], translate.generated_hash(front, body))
        self.item.body += " revisado"
        translate.main()
        self.assertEqual(self.api.call_count, 2)

    def test_manual_english_is_protected(self):
        translate.main()
        front, _, _ = site_content.split_front_matter(self.target)
        site_content.write_toml_markdown(self.target, front, "Manual English revision")
        self.item.body += " revised"
        translate.main(); translate.main()
        front, body, _ = site_content.split_front_matter(self.target)
        self.assertFalse(front["auto_translated"])
        self.assertIn("Manual English revision", body)
        self.assertEqual(self.api.call_count, 1)

    def test_draft_hides_automatic_english(self):
        translate.main()
        self.item.front_matter["draft"] = True
        translate.main()
        front, _, _ = site_content.split_front_matter(self.target)
        self.assertTrue(front["draft"])
        self.assertEqual(self.api.call_count, 1)
        self.item.front_matter["draft"] = False
        translate.main()
        front, _, _ = site_content.split_front_matter(self.target)
        self.assertFalse(front["draft"])


if __name__ == "__main__": unittest.main()
