#!/usr/bin/env python3
"""Collect source-linked articles for owner review; never publish automatically."""
from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import html
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "pending_articles.json"
MAX_ITEMS = 500
USER_AGENT = "NammaThalapathyNewsBot/1.0 (RSS reader)"
# Two additional focused English queries widen the source pool without AI
# translation, fabricated summaries, paid APIs, or frequent workflow runs.
FEED_QUERIES = [
    ("Vijay TVK Tamil Nadu", "en"),
    ("Tamilaga Vettri Kazhagam Vijay", "en"),
    ("TVK Vijay students scholarships education welfare", "en"),
    ("Tamilaga Vettri Kazhagam volunteers community service", "en"),
    ("தமிழக வெற்றிக் கழகம் விஜய்", "ta"),
]
RELEVANCE_TERMS = [
    "vijay", "tvk", "tamilaga vettri", "tamilaga vetri", "thalapathy",
    "தமிழக வெற்றிக் கழகம்", "தமிழக வெற்றி கழகம்", "விஜய்", "தவெக",
]


LOW_CONFIDENCE_PUBLISHERS = {"facebook", "instagram", "youtube", "tiktok", "x", "twitter"}


def eligible_publisher(name: str) -> bool:
    source = name.casefold().strip()
    return bool(source) and source not in LOW_CONFIDENCE_PUBLISHERS and source != "google news source"

def suitable_positive_story(title: str, description: str) -> bool:
    # Relevance only. Editorial decisions belong to the owner, not keywords.
    text = (title + " " + description).casefold()
    return True  # No automated topic or sentiment exclusions.


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean_text(value: str | None, limit: int = 1500) -> str:
    if not value:
        return ""
    # Remove RSS markup and decode HTML entities; never paraphrase the source.
    value = html.unescape(re.sub(r"<[^>]+>", " ", value))
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def get_url(url: str, timeout: int = 20) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def google_news_url(query: str, language: str) -> str:
    hl = "ta" if language == "ta" else "en-IN"
    ceid = "IN:ta" if language == "ta" else "IN:en"
    params = urllib.parse.urlencode({"q": query + " when:7d", "hl": hl, "gl": "IN", "ceid": ceid})
    return "https://news.google.com/rss/search?" + params


def parse_rss(xml_bytes: bytes, language: str = "en") -> list[dict[str, str]]:
    root = ET.fromstring(xml_bytes)
    items: list[dict[str, str]] = []
    for node in root.findall(".//item"):
        title = clean_text(node.findtext("title"), 300)
        link = (node.findtext("link") or "").strip()
        description = clean_text(node.findtext("description"), 1200)
        raw_pub_date = clean_text(node.findtext("pubDate"), 100)
        try:
            parsed_date = email.utils.parsedate_to_datetime(raw_pub_date)
            if parsed_date.tzinfo is None:
                parsed_date = parsed_date.replace(tzinfo=dt.timezone.utc)
            pub_date = parsed_date.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        except (TypeError, ValueError, OverflowError):
            pub_date = ""
        source_node = node.find("source")
        source = clean_text(source_node.text if source_node is not None else "", 120)
        if not title or not link.startswith("https://"):
            continue
        if not eligible_publisher(source):
            continue
        stable = re.sub(r"\W+", " ", title.casefold()).strip()
        item_id = hashlib.sha256((link + "|" + stable).encode("utf-8")).hexdigest()[:16]
        items.append({
            "id": item_id,
            "title": title,
            "sourceUrl": link,
            "sourceName": source or "Google News source",
            "sourceSnippet": description,
            "publishedAt": pub_date,
            "language": language,
        })
    return items


def collect_articles(fetcher=get_url) -> list[dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for query, language in FEED_QUERIES:
        url = google_news_url(query, language)
        try:
            payload = fetcher(url)
            for article in parse_rss(payload, language):
                key = article["sourceUrl"].split("&", 1)[0].rstrip("/")
                if key not in found:
                    found[key] = article
        except Exception as exc:
            print(f"RSS source failed ({query}): {type(exc).__name__}: {exc}", file=sys.stderr)
    return sorted(found.values(), key=lambda item: item.get("publishedAt", ""), reverse=True)


def load_existing(path: Path | None = None) -> list[dict[str, Any]]:
    # Resolve at call time so tests and alternate output paths are honored.
    path = path if path is not None else OUTPUT
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("updates", []) if isinstance(data, dict) else data
        return items if isinstance(items, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def valid_rss_entry(item: dict[str, Any]) -> bool:
    """Keep only unreviewed source-linked submissions."""
    title = str(item.get("title") or "")
    excerpt = str(item.get("content") or "")
    return (
        item.get("status") == "PENDING"
        and str(item.get("sourceUrl") or "").startswith("https://")
        and eligible_publisher(str(item.get("sourceName") or ""))
        
    )


def build_feed(
    articles: list[dict[str, str]],
    existing: list[dict[str, Any]],
    generated_at: str | None = None,
) -> dict[str, Any]:
    generated_at = generated_at or now_iso()

    # Keep previously staged source-provided excerpts. Historical
    # Gemini-written content is deliberately excluded from the review queue.
    merged: dict[str, dict[str, Any]] = {
        str(item["id"]): item
        for item in existing
        if isinstance(item, dict) and item.get("id") and valid_rss_entry(item)
    }
    for article in articles:
        title = article.get("title", "")
        excerpt = article.get("sourceSnippet", "")
        source_url = article.get("sourceUrl", "")
        publisher = article.get("sourceName", "")
        if (
            not article.get("id")
            or not source_url.startswith("https://")
            or not eligible_publisher(publisher)
            
        ):
            continue

        # "content" contains only text actually supplied in the RSS
        # description (minus markup/HTML entities). Do not fabricate a
        # summary when Google News provides only a headline.
        excerpt = clean_text(excerpt, 1200)
        if excerpt.casefold() == clean_text(title).casefold():
            excerpt = ""

        item = {
            "id": article["id"],
            "title": title,
            "content": excerpt,
            "category": "TVK",
            "date": article.get("publishedAt") or generated_at[:10],
            "publishedAt": article.get("publishedAt") or generated_at,
            "retrievedAt": generated_at,
            "sourceName": publisher,
            "sourceUrl": source_url,
            "language": article.get("language", "en"),
            "status": "PENDING",
        }
        prior = merged.get(item["id"])
        # No timestamp changes or cosmetic commits for an unchanged item.
        if prior and all(
            prior.get(key) == item.get(key)
            for key in ("title", "content", "category", "date", "publishedAt",
                        "sourceName", "sourceUrl", "language", "status")
        ):
            continue
        merged[item["id"]] = item

    items = sorted(
        merged.values(),
        key=lambda x: str(x.get("publishedAt") or x.get("date") or ""),
        reverse=True,
    )
    return {"updates": items[:MAX_ITEMS], "generatedAt": generated_at}


def main() -> int:
    existing = load_existing()
    articles = collect_articles()
    if not articles:
        # This also covers RSS outages. A failed fetch must never erase the
        # last usable feed or rewrite its freshness timestamp.
        print("No eligible RSS stories available; preserving the current feed.")
        return 0

    feed = build_feed(articles, existing)
    if not feed["updates"]:
        print("No valid source-linked items; preserving the current feed.")
        return 0
    if OUTPUT.exists() and feed["updates"] == existing:
        print("No source-content changes; no commit needed.")
        return 0

    new_content = json.dumps(feed, ensure_ascii=False, indent=2) + "\n"
    old_content = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
    if new_content == old_content:
        print("Feed unchanged; no commit needed.")
        return 0
    OUTPUT.write_text(new_content, encoding="utf-8")
    print(f"Staged {len(feed['updates'])} RSS articles for admin review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
