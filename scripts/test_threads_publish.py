import unittest
from unittest.mock import Mock, patch
import social_publish as social
import translate_pt_to_en as translate
from test_social_threads import part


class ThreadsTests(unittest.TestCase):
    def test_publish_and_resume(self):
        for target in ({}, {"container_id": "container"}):
            responses = [({"id": "container"}, {}), ({"id": "post"}, {})] if not target else [({"id": "post"}, {})]
            with patch.object(social, "request_form", side_effect=responses) as post, patch.object(social, "threads_get", side_effect=[{"status": "FINISHED"}, {"permalink": "https://threads.com/post"}]):
                social.post_threads("Hello", "https://example.org", target, Mock(), "parent")
            self.assertEqual(target["id"], "post")
            self.assertEqual(target["url"], "https://threads.com/post")
            self.assertTrue(post.call_args.args[0].endswith("threads_publish"))
            self.assertNotIn("crossreshare_to_ig", post.call_args.args[1])
            if len(responses) == 2:
                self.assertNotIn("crossreshare_to_ig", post.call_args_list[0].args[1])
                self.assertNotIn("instagram_story_requested", target)

    def test_ambiguous_result_does_not_republish(self):
        target = {"container_id": "container", "publish_attempted": True, "started_at": "2026-10-07T00:00:00Z"}
        with patch.object(social, "request_form") as post, patch.object(social, "threads_get", side_effect=[{"status": "FINISHED"}, {"data": []}]):
            with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                social.post_threads("Hello", "https://example.org", target, Mock())
        post.assert_not_called()

    def test_threads_follows_other_networks(self):
        item = part(1)
        data = {"title": "", "description": "", "body": "Hello"}
        self.assertTrue(social.wants(item, "threads"))
        item.front_matter["syndicate_bluesky"] = False
        item.front_matter["syndicate_mastodon"] = False
        self.assertFalse(social.wants(item, "threads"))
        item.front_matter["syndicate_mastodon"] = True
        self.assertTrue(social.wants(item, "threads"))
        self.assertTrue(translate.build_front_matter(item, data)["syndicate_mastodon"])
        item.front_matter["syndicate_threads"] = False
        self.assertFalse(social.wants(item, "threads"))
        self.assertFalse(translate.build_front_matter(item, data)["syndicate_threads"])
        item.front_matter["syndicate_threads"] = True
        self.assertTrue(social.wants(item, "threads"))

    def test_missing_credentials(self):
        with patch.dict(social.os.environ, {}, clear=True):
            self.assertEqual(social.missing_env("threads"), ["THREADS_USER_ID", "THREADS_ACCESS_TOKEN"])

    def test_recover_published_result(self):
        target = {"container_id": "container", "publish_attempted": True, "started_at": "2026-10-07T00:00:00Z"}
        with patch.object(social, "request_form") as post, patch.object(social, "threads_get", side_effect=[{"status": "PUBLISHED"}, {"data": [{"id": "post", "text": "Hello", "timestamp": "2026-10-07T01:00:00+0000", "permalink": "https://threads.com/post"}]}]):
            social.post_threads("Hello", "https://example.org", target, Mock())
        self.assertEqual(target["id"], "post")
        post.assert_not_called()

    def test_blog_length_preserves_url(self):
        with patch.object(social, "request_form", side_effect=[({"id": "container"}, {}), ({"id": "post"}, {})]) as post, patch.object(social, "threads_get", side_effect=[{"status": "FINISHED"}, {"permalink": "https://threads.com/post"}]):
            social.post_threads("Long intro " * 100, "https://example.org/post", {}, Mock())
        message = post.call_args_list[0].args[1]["text"]
        self.assertLessEqual(len(message), 500)
        self.assertTrue(message.endswith("https://example.org/post"))


if __name__ == "__main__":
    unittest.main()
