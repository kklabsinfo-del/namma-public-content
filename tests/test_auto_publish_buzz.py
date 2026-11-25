"""Offline, zero-cost tests for guarded daily publication and review queue."""
import datetime as dt
import unittest

from scripts.auto_publish_buzz import (
    MAX_NEW_PER_DAY, MAX_PENDING, publish, topic_for
)

NOW = dt.datetime(2026, 11, 25, 1, 0, tzinfo=dt.timezone.utc)


def story(i=1, **changes):
    entry = {
        "id": f"{i:016x}", "title": "TVK Vijay volunteers distribute scholarships to students",
        "content": "Publisher wording must not be copied into the app.",
        "sourceName": "The Hindu",
        "sourceUrl": f"https://news.google.com/rss/articles/item{i}",
        "publishedAt": "2026-11-24T19:00:00Z",
        "language": "en", "category": "TVK", "status": "PENDING"
    }
    entry.update(changes)
    return entry


def run(items, existing=None, enabled=True):
    return publish({"updates": items}, {"updates": existing or []},
                   {"ids": []}, {"autoPublishEnabled": enabled}, NOW)


class AutoPublishBuzzTests(unittest.TestCase):
    def test_safe_welfare_item_publishes_with_original_topic_copy(self):
        p, published, n = run([story()])
        self.assertEqual(n, 1)
        self.assertEqual(p["updates"], [])
        article = published["updates"][0]
        self.assertEqual(article["sourceUrl"], story()["sourceUrl"])
        self.assertEqual(article["status"], "AUTO_SOURCE_LINKED")
        self.assertEqual(article["summaryMethod"], "ORIGINAL_TOPIC_TEMPLATE")
        self.assertNotIn("Publisher wording must not be copied", article["content"])
        self.assertNotIn("imageUrl", article)

    def test_contested_or_political_items_stay_unpublished(self):
        self.assertIsNone(topic_for(story(title="TVK Vijay MLA awards school students"), NOW))
        self.assertIsNone(topic_for(story(title="TVK Vijay faces court dispute after awards"), NOW))
        self.assertIsNone(topic_for(story(title="TVK Vijay party election vote welfare"), NOW))
        self.assertIsNone(topic_for(story(title="TVK Vijay announces new government"), NOW))

    def test_unknown_social_and_fake_publishers_are_not_trusted(self):
        for source in ["Facebook", "Instagram", "Unverified News", "", "Example News"]:
            self.assertIsNone(topic_for(story(sourceName=source), NOW))

    def test_old_future_and_malformed_sources_are_ineligible(self):
        self.assertIsNone(topic_for(story(publishedAt="2026-11-15T00:00:00Z"), NOW))
        self.assertIsNone(topic_for(story(publishedAt="2026-11-30T00:00:00Z"), NOW))
        self.assertIsNone(topic_for(story(sourceUrl="http://news.google.com/a"), NOW))
        self.assertIsNone(topic_for(story(sourceUrl="https://user:pass@news.google.com/a"), NOW))

    def test_pause_keeps_all_recent_items_out_of_public_feed(self):
        p, pub, n = run([story()], enabled=False)
        self.assertEqual(n, 0)
        self.assertEqual(len(p["updates"]), 1)
        self.assertEqual(pub["updates"], [])

    def test_daily_limit_and_repeat_run_idempotency(self):
        entries = [story(i, title=f"TVK Vijay volunteers distribute scholarships to students in district {i}")
                   for i in range(1, MAX_NEW_PER_DAY + 6)]
        p, pub, n = run(entries)
        self.assertEqual(n, MAX_NEW_PER_DAY)
        self.assertEqual(len(pub["updates"]), MAX_NEW_PER_DAY)
        p2, pub2, n2 = run(p["updates"], existing=pub["updates"])
        self.assertEqual(n2, 0)
        self.assertEqual(pub2["updates"], pub["updates"])

    def test_duplicate_headline_is_never_published_twice(self):
        entries = [story(1), story(2)]
        p, pub, n = run(entries)
        self.assertEqual(n, 1)
        self.assertEqual(len(pub["updates"]), 1)
        self.assertEqual(len(p["updates"]), 1)

    def test_tamil_is_preserved_and_no_unlicensed_images(self):
        item = story(
            title="விஜய் தவெக சார்பில் மாணவர்களுக்கு கல்வி உதவி வழங்கினார்",
            language="ta", imageUrl="https://example.org/publisher-photo.jpg"
        )
        p, pub, n = run([item])
        self.assertEqual(n, 1)
        self.assertIn("மூல", pub["updates"][0]["content"])
        self.assertNotIn("imageUrl", pub["updates"][0])

    def test_queue_caps_and_expires_without_deleting_review_ledger(self):
        entries = [story(i, sourceName="Unverified News") for i in range(1, 130)]
        p, pub, n = run(entries)
        self.assertEqual(n, 0)
        self.assertEqual(len(p["updates"]), MAX_PENDING)
        self.assertEqual(pub["updates"], [])

    def test_existing_published_stories_are_preserved(self):
        prev = [{"id": "previous-real-update", "title": "Previously published",
                 "content": "Do not erase or rewrite this verified entry",
                 "sourceUrl": "https://example.org/a", "status": "MANUAL_SOURCE_LINKED"}]
        _, pub, n = run([], existing=prev)
        self.assertEqual(n, 0)
        self.assertEqual(pub["updates"], prev)


if __name__ == "__main__":
    unittest.main()
