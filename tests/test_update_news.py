import json
import tempfile
import unittest
from pathlib import Path

from scripts.update_news import build_feed, clean_text, parse_rss, suitable_positive_story


SAMPLE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><item>
<title>Vijay announces student education awards</title>
<link>https://example.com/vijay-story</link>
<description>TVK leader Vijay felicitated students.</description>
<pubDate>Fri, 23 Oct 2026 02:00:00 GMT</pubDate>
<source url="https://example.com">Example News</source>
</item><item>
<title>Weather update</title>
<link>https://example.com/weather</link>
<description>Weather report.</description>
<pubDate>Fri, 23 Oct 2026 01:00:00 GMT</pubDate>
</item></channel></rss>"""


class NewsPipelineTests(unittest.TestCase):
    def test_parse_filters_irrelevant_and_keeps_source(self):
        items = parse_rss(SAMPLE_RSS)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Vijay announces a public event")
        self.assertEqual(items[0]["sourceName"], "Example News")
        self.assertTrue(items[0]["sourceUrl"].startswith("https://"))

    def test_clean_text_removes_html(self):
        self.assertEqual(clean_text("<b>Hello</b>  world"), "Hello world")

    def test_build_feed_preserves_existing_and_adds_summary(self):
        articles = [{
            "id": "abc123", "title": "TVK Vijay student education awards", "sourceUrl": "https://example.com/a",
            "sourceName": "Example News", "publishedAt": "2026-10-23T02:00:00Z",
            "language": "en", "sourceSnippet": "A factual snippet",
        }]
        summaries = {"abc123": {"summary": "TVK leader Vijay felicitated students.", "category": "TVK", "language": "en"}}
        existing = [{"id": "old", "title": "Old story", "content": "Previously published", "date": "2026-10-22"}]
        result = build_feed(articles, summaries, existing, "2026-10-23T03:00:00Z")
        self.assertEqual(len(result["updates"]), 1)
        fresh = next(item for item in result["updates"] if item["id"] == "abc123")
        self.assertEqual(fresh["content"], "TVK leader Vijay felicitated students.")
        self.assertEqual(fresh["sourceUrl"], "https://example.com/a")
        self.assertEqual(fresh["status"], "SOURCE_LINKED")

    def test_editorial_gate_blocks_conflict_and_unsupported_headlines(self):
        self.assertFalse(suitable_positive_story("TVK Vijay accused in dispute", "Student event"))
        self.assertFalse(suitable_positive_story("TVK Vijay attacks opposition", "Education speech"))
        self.assertFalse(suitable_positive_story("Vijay announces election victory", "Political claims"))
        self.assertTrue(suitable_positive_story("TVK Vijay felicitates students", "Education awards event"))

    def test_old_conflict_stories_are_removed(self):
        old = [
            {"id": "bad", "title": "TVK accused in poaching allegations", "content": "Political row", "sourceUrl": "https://example.com/b"},
            {"id": "good", "title": "TVK Vijay education awards", "content": "Students felicitated", "sourceUrl": "https://example.com/g"},
        ]
        feed = build_feed([], {}, old, "2026-10-23T03:00:00Z")
        self.assertEqual([x["id"] for x in feed["updates"]], ["good"])

    def test_build_feed_caps_items_at_100(self):
        old = [{"id": str(i), "title": "Vijay student education awards", "content": "TVK felicitates students", "sourceUrl": "https://example.com/a", "date": f"2026-10-{(i % 28) + 1:02d}"} for i in range(110)]
        result = build_feed([], {}, old, "2026-10-23T03:00:00Z")
        self.assertEqual(len(result["updates"]), 100)


if __name__ == "__main__":
    unittest.main()
