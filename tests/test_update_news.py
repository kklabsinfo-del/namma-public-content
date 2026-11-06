import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import update_news
from scripts.update_news import (
    build_feed, clean_text, eligible_publisher, parse_rss, suitable_positive_story,
)


SAMPLE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><item>
<title>Vijay announces student education awards</title>
<link>https://example.com/vijay-story</link>
<description>&lt;b&gt;TVK leader Vijay felicitated students &amp;amp; teachers.&lt;/b&gt;</description>
<pubDate>Fri, 23 Oct 2026 02:00:00 GMT</pubDate>
<source url="https://example.com">Example News</source>
</item><item>
<title>Weather update</title>
<link>https://example.com/weather</link>
<description>Weather report.</description>
<pubDate>Fri, 23 Oct 2026 01:00:00 GMT</pubDate>
</item></channel></rss>"""


def rss_article(**overrides):
    article = {
        "id": "abc123",
        "title": "TVK Vijay student education awards",
        "sourceUrl": "https://example.com/a",
        "sourceName": "Example News",
        "publishedAt": "2026-10-23T02:00:00Z",
        "language": "en",
        "sourceSnippet": "TVK leader Vijay felicitated students.",
    }
    return {**article, **overrides}


def published_rss_story(**overrides):
    article = rss_article()
    record = build_feed([article], [], "2026-10-23T03:00:00Z")["updates"][0]
    return {**record, **overrides}


class NewsPipelineTests(unittest.TestCase):
    def test_parse_preserves_source_headline_and_rss_description(self):
        items = parse_rss(SAMPLE_RSS)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Vijay announces student education awards")
        self.assertEqual(items[0]["sourceSnippet"], "TVK leader Vijay felicitated students & teachers.")
        self.assertEqual(items[0]["sourceName"], "Example News")
        self.assertEqual(items[0]["sourceUrl"], "https://example.com/vijay-story")

    def test_clean_text_only_removes_markup_and_decodes_entities(self):
        self.assertEqual(clean_text("<b>Hello &amp; welcome</b>  world"), "Hello & welcome world")

    def test_published_feed_uses_source_text_without_ai_rewriting(self):
        article = rss_article()
        feed = build_feed([article], [], "2026-10-23T03:00:00Z")
        self.assertEqual(len(feed["updates"]), 1)
        story = feed["updates"][0]
        self.assertEqual(story["title"], article["title"])
        self.assertEqual(story["content"], article["sourceSnippet"])
        self.assertEqual(story["sourceName"], article["sourceName"])
        self.assertEqual(story["sourceUrl"], article["sourceUrl"])
        self.assertEqual(story["status"], "RSS_EXCERPT")

    def test_missing_or_duplicate_description_is_not_invented(self):
        article = rss_article(sourceSnippet="TVK Vijay student education awards")
        feed = build_feed([article], [], "2026-10-23T03:00:00Z")
        self.assertEqual(feed["updates"][0]["content"], "")
        empty = rss_article(sourceSnippet="")
        self.assertEqual(build_feed([empty], [], "2026-10-23T03:00:00Z")["updates"][0]["content"], "")

    def test_editorial_filter_rejects_conflict_and_unrelated_stories(self):
        self.assertFalse(suitable_positive_story("TVK Vijay accused in dispute", "Student event"))
        self.assertFalse(suitable_positive_story("TVK Vijay attacks opposition", "Education speech"))
        self.assertFalse(suitable_positive_story("Vijay announces election victory", "Political claims"))
        self.assertTrue(suitable_positive_story("TVK Vijay felicitates students", "Education awards event"))
        self.assertFalse(eligible_publisher("Instagram"))
        self.assertTrue(eligible_publisher("Example News"))

    def test_prior_ai_summaries_are_removed_on_next_valid_rss_refresh(self):
        old = [
            {"id": "old-ai", "title": "TVK Vijay education awards",
             "content": "AI-written description", "sourceName": "Example News",
             "sourceUrl": "https://example.com/old", "status": "SOURCE_LINKED"},
            published_rss_story(id="old-rss"),
        ]
        feed = build_feed([rss_article(id="new-rss")], old, "2026-10-24T03:00:00Z")
        self.assertEqual({x["id"] for x in feed["updates"]}, {"old-rss", "new-rss"})

    def test_old_conflict_and_untrusted_entries_are_not_republished(self):
        old = [
            published_rss_story(id="bad", title="TVK accused in poaching allegations"),
            published_rss_story(id="social", sourceName="Instagram"),
            published_rss_story(id="good"),
        ]
        feed = build_feed([], old, "2026-10-23T03:00:00Z")
        self.assertEqual([x["id"] for x in feed["updates"]], ["good"])

    def test_build_feed_caps_items_at_100(self):
        old = [
            published_rss_story(
                id=str(i), sourceUrl=f"https://example.com/{i}",
                publishedAt=f"2026-10-{(i % 28) + 1:02d}T03:00:00Z",
            )
            for i in range(110)
        ]
        result = build_feed([], old, "2026-10-23T03:00:00Z")
        self.assertEqual(len(result["updates"]), 100)

    def test_unchanged_rss_story_preserves_retrieval_timestamp(self):
        old = published_rss_story()
        new_feed = build_feed([rss_article()], [old], "2026-11-06T03:00:00Z")
        self.assertEqual(new_feed["updates"], [old])

    def test_no_eligible_rss_stories_leaves_existing_file_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "updates.json"
            original = '{"updates":[{"id":"old"}],"generatedAt":"old"}'
            output.write_text(original, encoding="utf-8")
            with (
                patch.object(update_news, "OUTPUT", output),
                patch.object(update_news, "collect_articles", return_value=[]),
            ):
                self.assertEqual(update_news.main(), 0)
            self.assertEqual(output.read_text(encoding="utf-8"), original)

    def test_valid_rss_update_replaces_legacy_ai_text(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "updates.json"
            legacy = {
                "updates": [{
                    "id": "old-ai", "title": "TVK Vijay student education awards",
                    "content": "An AI-written summary.", "status": "SOURCE_LINKED",
                    "sourceName": "Example News", "sourceUrl": "https://example.com/old"
                }],
                "generatedAt": "2026-10-23T03:00:00Z",
            }
            output.write_text(json.dumps(legacy), encoding="utf-8")
            with (
                patch.object(update_news, "OUTPUT", output),
                patch.object(update_news, "collect_articles", return_value=[rss_article()]),
            ):
                self.assertEqual(update_news.main(), 0)
            data = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(len(data["updates"]), 1)
            self.assertEqual(data["updates"][0]["content"], "TVK leader Vijay felicitated students.")
            self.assertEqual(data["updates"][0]["status"], "RSS_EXCERPT")

    def test_unchanged_eligible_feed_does_not_make_cosmetic_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "updates.json"
            old = published_rss_story()
            original = json.dumps({
                "updates": [old], "generatedAt": "2026-10-23T03:00:00Z",
            })
            output.write_text(original, encoding="utf-8")
            with (
                patch.object(update_news, "OUTPUT", output),
                patch.object(update_news, "collect_articles", return_value=[rss_article()]),
            ):
                self.assertEqual(update_news.main(), 0)
            self.assertEqual(output.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
