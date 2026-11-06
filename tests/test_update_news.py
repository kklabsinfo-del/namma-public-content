import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from scripts import update_news
from scripts.update_news import (
    TemporaryGeminiUnavailable, build_feed, clean_text, gemini_summarize,
    parse_rss, suitable_positive_story,
)


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
        self.assertEqual(items[0]["title"], "Vijay announces student education awards")
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
            {"id": "bad", "title": "TVK accused in poaching allegations", "content": "Political row", "sourceUrl": "https://example.com/b", "sourceName": "Example News"},
            {"id": "good", "title": "TVK Vijay education awards", "content": "Students felicitated", "sourceUrl": "https://example.com/g", "sourceName": "Example News"},
        ]
        feed = build_feed([], {}, old, "2026-10-23T03:00:00Z")
        self.assertEqual([x["id"] for x in feed["updates"]], ["good"])

    def test_old_unattributed_or_untrusted_stories_are_not_republished(self):
        old = [
            {"id": "unknown", "title": "TVK Vijay education awards", "content": "Students felicitated",
             "sourceUrl": "https://example.com/a"},
            {"id": "social", "title": "TVK Vijay education awards", "content": "Students felicitated",
             "sourceUrl": "https://example.com/b", "sourceName": "Instagram"},
            {"id": "approved", "title": "TVK Vijay education awards", "content": "Students felicitated",
             "sourceUrl": "https://example.com/c", "sourceName": "Example News"},
        ]
        result = build_feed([], {}, old, "2026-10-23T03:00:00Z")
        self.assertEqual([entry["id"] for entry in result["updates"]], ["approved"])

    def test_temporary_gemini_503_retries_then_defers(self):
        article = {
            "id": "abc", "title": "TVK Vijay education awards",
            "sourceName": "Example News", "sourceSnippet": "Students felicitated",
            "language": "en",
        }
        error = urllib.error.HTTPError(
            "https://example.org", 503, "Service Unavailable", {},
            io.BytesIO(b'{"error":{"status":"UNAVAILABLE"}}'),
        )
        with (
            patch.object(update_news.urllib.request, "urlopen", side_effect=error) as fetch,
            patch.object(update_news.time, "sleep") as sleep,
        ):
            with self.assertRaises(TemporaryGeminiUnavailable):
                gemini_summarize([article], "fake-test-key", "test-model")
            self.assertEqual(fetch.call_count, 3)
            self.assertEqual(sleep.call_count, 2)

    def test_deferred_refresh_keeps_existing_file_unchanged(self):
        with tempfile.TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "updates.json"
            original = '{"updates":[{"id":"old"}],"generatedAt":"old"}'
            target.write_text(original, encoding="utf-8")
            article = {"id": "new", "title": "TVK Vijay education awards"}
            with (
                patch.object(update_news, "OUTPUT", target),
                patch.object(update_news, "load_existing", return_value=[]),
                patch.object(update_news, "collect_articles", return_value=[article]),
                patch.object(update_news, "gemini_summarize",
                             side_effect=TemporaryGeminiUnavailable("HTTP 503")),
                patch.dict(os.environ, {"GEMINI_API_KEY": "fake-test-key"}),
            ):
                self.assertEqual(update_news.main(), 0)
            self.assertEqual(target.read_text(encoding="utf-8"), original)

    def test_unchanged_eligible_feed_does_not_make_cosmetic_commit(self):
        with tempfile.TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "updates.json"
            old = {
                "id": "one", "title": "TVK Vijay education awards",
                "content": "Students felicitated", "sourceName": "Example News",
                "sourceUrl": "https://example.org/story",
                "publishedAt": "2026-10-23T03:00:00Z",
            }
            original = json.dumps({"updates": [old], "generatedAt": "2026-10-23T03:00:00Z"})
            target.write_text(original, encoding="utf-8")
            with (
                patch.object(update_news, "OUTPUT", target),
                patch.object(update_news, "load_existing", return_value=[old]),
                patch.object(update_news, "collect_articles", return_value=[]),
                patch.dict(os.environ, {"GEMINI_API_KEY": "fake-test-key"}),
            ):
                self.assertEqual(update_news.main(), 0)
            self.assertEqual(target.read_text(encoding="utf-8"), original)

    def test_build_feed_caps_items_at_100(self):
        old = [{"id": str(i), "title": "Vijay student education awards", "content": "TVK felicitates students", "sourceUrl": "https://example.com/a", "sourceName": "Example News", "date": f"2026-10-{(i % 28) + 1:02d}"} for i in range(110)]
        result = build_feed([], {}, old, "2026-10-23T03:00:00Z")
        self.assertEqual(len(result["updates"]), 100)


if __name__ == "__main__":
    unittest.main()
