#!/usr/bin/env python3
"""Build the public Namma Thalapathy news feed from Google News RSS + one Gemini batch call."""
from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "updates.json"
MAX_ITEMS = 100
USER_AGENT = "NammaThalapathyNewsBot/1.0 (RSS reader)"
FEED_QUERIES = [
    ("Vijay TVK Tamil Nadu", "en"),
    ("Tamilaga Vettri Kazhagam Vijay", "en"),
    ("தமிழக வெற்றிக் கழகம் விஜய்", "ta"),
]
RELEVANCE_TERMS = [
    "vijay", "tvk", "tamilaga vettri", "tamilaga vetri", "thalapathy",
    "தமிழக வெற்றிக் கழகம்", "தமிழக வெற்றி கழகம்", "விஜய்", "தவெக",
]
ALLOWED_CATEGORIES = {"TVK", "CINEMA", "PUBLIC_AFFAIRS", "OTHER"}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean_text(value: str | None, limit: int = 1500) -> str:
    if not value:
        return ""
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def get_url(url: str, timeout: int = 20) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def google_news_url(query: str, language: str) -> str:
    hl = "ta" if language == "ta" else "en-IN"
    ceid = "IN:ta" if language == "ta" else "IN:en"
    params = urllib.parse.urlencode({"q": query + " when:3d", "hl": hl, "gl": "IN", "ceid": ceid})
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
        if not title or not link.startswith(("https://", "http://")):
            continue
        haystack = (title + " " + description).casefold()
        if not any(term.casefold() in haystack for term in RELEVANCE_TERMS):
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


def gemini_summarize(articles: list[dict[str, str]], api_key: str, model: str) -> dict[str, dict[str, str]]:
    """One Gemini request for the entire batch; the key stays in the workflow secret."""
    if not articles:
        return {}
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model, safe='')}:generateContent?key={urllib.parse.quote(api_key, safe='')}"
    prompt_articles = [
        {"id": a["id"], "headline": a["title"], "publisher": a["sourceName"],
         "feed_description": a["sourceSnippet"], "source_language": a["language"]}
        for a in articles[:MAX_ITEMS]
    ]
    prompt = (
        "You write neutral, concise news summaries for an unofficial Tamil Nadu fan news app. "
        "Use only the supplied headline and feed description. Do not add facts, infer outcomes, "
        "write political persuasion, invent quotes, or claim verification. If the description is "
        "insufficient, produce a cautious one-sentence summary based only on the headline. "
        "Preserve the supplied article ID exactly. Return JSON only with key 'items', an array of "
        "objects {id, summary, category, language}. category must be one of TVK, CINEMA, "
        "PUBLIC_AFFAIRS, OTHER. language must be ta or en. summary should be 1-3 short sentences; "
        "Tamil for ta and English for en. Keep the headline separate; do not rewrite it. Input: "
        + json.dumps(prompt_articles, ensure_ascii=False)
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                result = json.loads(response.read().decode("utf-8"))
            text = result["candidates"][0]["content"]["parts"][0]["text"]
            decoded = json.loads(text)
            outputs: dict[str, dict[str, str]] = {}
            for item in decoded.get("items", []):
                item_id = str(item.get("id", ""))
                summary = clean_text(str(item.get("summary", "")), 700)
                category = str(item.get("category", "OTHER")).upper()
                language = str(item.get("language", "en")).lower()
                if item_id and summary and category in ALLOWED_CATEGORIES and language in {"ta", "en"}:
                    outputs[item_id] = {"summary": summary, "category": category, "language": language}
            if not outputs:
                raise ValueError("Gemini returned no valid summaries")
            return outputs
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Gemini summarization failed after retries: {last_error}")


def load_existing(path: Path = OUTPUT) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("updates", []) if isinstance(data, dict) else data
        return items if isinstance(items, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def build_feed(articles: list[dict[str, str]], summaries: dict[str, dict[str, str]],
               existing: list[dict[str, Any]], generated_at: str | None = None) -> dict[str, Any]:
    generated_at = generated_at or now_iso()
    old_by_id: dict[str, dict[str, Any]] = {}
    for old in existing:
        if isinstance(old, dict):
            old_id = str(old.get("id") or "")
            if old_id:
                old_by_id[old_id] = old
    merged = dict(old_by_id)
    for article in articles:
        summary = summaries.get(article["id"])
        if not summary:
            continue
        merged[article["id"]] = {
            "id": article["id"],
            "title": article["title"],
            "content": summary["summary"],
            "category": summary["category"],
            "date": article.get("publishedAt") or generated_at[:10],
            "publishedAt": article.get("publishedAt") or generated_at,
            "retrievedAt": generated_at,
            "sourceName": article["sourceName"],
            "sourceUrl": article["sourceUrl"],
            "language": summary["language"],
            "status": "SOURCE_LINKED",
        }
    # Keep newest entries first; missing/old dates naturally sort lower.
    items = sorted(merged.values(), key=lambda x: str(x.get("publishedAt") or x.get("date") or ""), reverse=True)
    return {"updates": items[:MAX_ITEMS], "generatedAt": generated_at}


def main() -> int:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("Missing GEMINI_API_KEY. Add it under GitHub repository Settings > Secrets and variables > Actions.", file=sys.stderr)
        return 2
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()
    existing = load_existing()
    articles = collect_articles()
    if not articles:
        print("No relevant RSS articles collected; preserving existing updates.json.")
        return 0
    existing_ids = {str(item.get("id")) for item in existing if isinstance(item, dict) and item.get("id")}
    articles = [article for article in articles if article["id"] not in existing_ids]
    if not articles:
        print("No new stories; skipping Gemini and preserving updates.json.")
        return 0
    try:
        summaries = gemini_summarize(articles, api_key, model)
    except Exception as exc:
        print(f"News generation failed; preserving existing updates.json. {exc}", file=sys.stderr)
        return 1
    feed = build_feed(articles, summaries, existing)
    if not feed["updates"]:
        print("No valid generated stories; preserving existing updates.json.", file=sys.stderr)
        return 1
    new_content = json.dumps(feed, ensure_ascii=False, indent=2) + "\n"
    old_content = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
    if new_content == old_content:
        print("Feed unchanged; no commit needed.")
        return 0
    OUTPUT.write_text(new_content, encoding="utf-8")
    print(f"Updated feed with {len(feed['updates'])} stories; generated summaries for {len(summaries)} items.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
