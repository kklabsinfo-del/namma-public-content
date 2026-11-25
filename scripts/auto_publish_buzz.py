#!/usr/bin/env python3
"""Conservative daily Buzz auto-publication; no AI, copied RSS excerpts, or paid APIs.

The collector produces pending_articles.json. This script publishes ONLY recent,
source-attributed, clearly noncontroversial welfare stories, with original short
descriptions about the presence of a source report, not invented story details.
Everything else stays pending for optional owner review (or expires). Safe failure
is an unchanged published feed. Runs in the EXISTING daily GitHub workflow.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PENDING = ROOT / "pending_articles.json"
PUBLISHED = ROOT / "updates.json"
REVIEWED = ROOT / "reviewed_articles.json"
CONTROL = ROOT / "buzz_control.json"
MAX_NEW_PER_DAY = 8
MAX_PUBLISHED = 500
MAX_PENDING = 60
MAX_AGE_HOURS = 72

# Conservative allowlist. A known source label is NOT independent verification.
PUBLISHERS = frozenset({
    "the hindu", "hindu tamil thisai", "the new indian express",
    "the indian express", "indian express - tamil", "dinamani",
    "ndtv", "bbc", "bbc news தமிழ்", "the times of india",
    "india today", "news18", "news18 tamil", "dt next", "the week",
    "deccan herald", "hindustan times", "news today", "the south first",
    "the federal", "vikatan", "etv bharat", "the economic times",
    "oneindia tamil", "abp nadu", "theprint", "livemint",
})
PARTY = re.compile(r"\b(?:tvk|vijay|thalapathy|tamilaga vettri|tamilaga vetri)\b|விஜய்|தவெக|தமிழக வெற்றிக் கழகம்", re.I)
# Fail closed on disputes, political claims, speculation, and charged topics.
EXCLUDE = re.compile(
    r"\b(?:fir|court|police|supreme court|arrest|accus|alleg|scam|fraud|"
    r"controvers|violent|attack|clash|protest|opposition|rival|election|"
    r"minister|chief minister|mla|mp|party leader|politic|vote|seat|"
    r"campaign|manifesto|prediction|rumou?r|viral|slams?|war|"
    r"critic|fake|leak|exclusive|shocking|government|govt)\b|"
    r"நீதிமன்ற|காவல்|போலீஸ்|வழக்கு|கைது|குற்ற|சர்ச்சை|மோதல்|"
    r"தேர்தல்|எதிர்க்கட்சி|அரசியல்|முதல்வர்|அமைச்சர்|எம்எல்ஏ|"
    r"வாக்கு|போராட்ட|வதந்தி|அதிர்ச்சி|ஆட்சி", re.I
)
TOPICS = (
    ("education", re.compile(r"\b(?:students?|schools?|education|scholarships?|awards?|"
     r"college|textbook)\b|மாணவ|கல்வி|உதவித்தொகை|பள்ளி|விருது", re.I),
     re.compile(r"\b(?:award|felicitat|donat|distribut|scholarship|support|"
     r"honou?r|recognition)\w*\b|வழங்க|உதவி|பாராட்ட|விருது", re.I)),
    ("health", re.compile(r"\b(?:blood donation|medical camp|health camp|"
     r"healthcare drive)\b|இரத்ததான|ரத்ததான|மருத்துவ முகாம்|ரத்த தான", re.I),
     re.compile(r"\b(?:donation|camp|drive)\b|முகாம்|தானம்", re.I)),
    ("environment", re.compile(r"\b(?:tree planting|sapling|plant trees|"
     r"environmental clean.up|clean.up drive)\b|மரக்கன்று|மரநடுகை|மரம் நடு", re.I),
     re.compile(r"\b(?:plant|drive|distribut|clean)\w*\b|நட்ட|நடுதல்|வழங்க", re.I)),
    ("welfare", re.compile(r"\b(?:community welfare|volunteer service|"
     r"food distribution|relief supplies)\b|நலத்திட்ட உதவி|நிவாரண உதவி", re.I),
     re.compile(r"\b(?:volunteer|donat|distribut|relief|welfare)\w*\b|உதவி|சேவை|வழங்க", re.I)),
)
SUMMARY = {
    "en": {
        "education": "A source report covers education support or student recognition linked to Vijay or TVK. Visit the publisher for the details.",
        "health": "A source report covers a health or blood-donation initiative linked to Vijay or TVK. Visit the publisher for the details.",
        "environment": "A source report covers environmental volunteering linked to Vijay or TVK. Visit the publisher for the details.",
        "welfare": "A source report covers a community-welfare activity linked to Vijay or TVK. Visit the publisher for the details.",
    },
    "ta": {
        "education": "விஜய் அல்லது தவெக தொடர்பான கல்வி உதவி அல்லது மாணவர் பாராட்டு குறித்து மூல செய்தி வெளியாகியுள்ளது. விவரங்களுக்கு வெளியீட்டாளரின் இணைப்பைப் பார்க்கவும்.",
        "health": "விஜய் அல்லது தவெக தொடர்பான மருத்துவ முகாம் அல்லது இரத்ததான முயற்சி குறித்து மூல செய்தி வெளியாகியுள்ளது. விவரங்களுக்கு மூல இணைப்பைப் பார்க்கவும்.",
        "environment": "விஜய் அல்லது தவெக தொடர்பான சுற்றுச்சூழல் சேவை குறித்து மூல செய்தி வெளியாகியுள்ளது. விவரங்களுக்கு மூல இணைப்பைப் பார்க்கவும்.",
        "welfare": "விஜய் அல்லது தவெக தொடர்பான சமூக நல உதவி குறித்து மூல செய்தி வெளியாகியுள்ளது. விவரங்களுக்கு மூல இணைப்பைப் பார்க்கவும்.",
    },
}


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError(f"Invalid JSON object in {path.name}")
    return result


def write_if_changed(path: Path, data: dict[str, Any]) -> bool:
    encoded = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == encoded:
        return False
    path.write_text(encoded, encoding="utf-8")
    return True


def parse_date(value: Any) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(dt.timezone.utc)
    except (ValueError, TypeError):
        return None


def topic_for(item: dict[str, Any], now: dt.datetime) -> str | None:
    title = str(item.get("title") or "").strip()
    publisher = str(item.get("sourceName") or "").strip().casefold()
    url = str(item.get("sourceUrl") or "")
    date = parse_date(item.get("publishedAt"))
    try:
        parsed_url = urlsplit(url)
        valid_url = (parsed_url.scheme == "https" and bool(parsed_url.hostname)
                     and not parsed_url.username and not parsed_url.password
                     and parsed_url.port in (None, 443))
    except ValueError:
        valid_url = False
    if (item.get("status") != "PENDING" or not re.fullmatch(r"[0-9a-f]{16}", str(item.get("id", "")))
        or not title or len(title) > 280 or publisher not in PUBLISHERS
        or not valid_url or not date
        or not (dt.timedelta() <= now-date <= dt.timedelta(hours=MAX_AGE_HOURS))
        or not PARTY.search(title) or EXCLUDE.search(title)):
        return None
    # The headline must independently identify a concrete positive activity.
    # RSS snippets are not copied or used to invent additional details.
    for name, subject, activity in TOPICS:
        if subject.search(title) and activity.search(title):
            return name
    return None


def publish(
    pending: dict[str, Any],
    published: dict[str, Any],
    reviewed: dict[str, Any],
    control: dict[str, Any],
    now: dt.datetime,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    queue = pending.get("updates", [])
    existing = published.get("updates", [])
    excluded = set(reviewed.get("ids", []))
    if not isinstance(queue, list) or not isinstance(existing, list):
        raise ValueError("Malformed Buzz feed: updates must be arrays")
    approved_ids = {str(it.get("id")) for it in existing if isinstance(it, dict)}
    queue = [x for x in queue if isinstance(x, dict)
             and x.get("id") not in excluded and x.get("id") not in approved_ids]
    queue.sort(key=lambda x: str(x.get("publishedAt") or ""), reverse=True)
    allowed = control.get("autoPublishEnabled") is True
    today = now.date()
    already_published_today = sum(
        1 for x in existing if isinstance(x, dict)
        and x.get("status") == "AUTO_SOURCE_LINKED"
        and (stamp := parse_date(x.get("approvedAt"))) is not None
        and stamp.date() == today
    )
    remaining_quota = max(0, MAX_NEW_PER_DAY - already_published_today)
    additions: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for item in queue:
        if not allowed or len(additions) >= remaining_quota:
            break
        topic = topic_for(item, now)
        if topic is None:
            continue
        normalized = re.sub(r"[^\w\u0b80-\u0bff]+", " ", str(item["title"]).casefold()).strip()
        if normalized in seen_titles:
            continue
        seen_titles.add(normalized)
        lang = "ta" if item.get("language") == "ta" else "en"
        source = dict(item)
        source["content"] = SUMMARY[lang][topic]
        source["category"] = {"education":"Education","health":"Welfare",
                              "environment":"Environment","welfare":"Welfare"}[topic]
        source["status"] = "AUTO_SOURCE_LINKED"
        source["approvedAt"] = now.isoformat().replace("+00:00", "Z")
        source["summaryMethod"] = "ORIGINAL_TOPIC_TEMPLATE"
        # No copyrighted imageUrl, no scraped article body, no fake verification flag.
        source.pop("imageUrl", None)
        source.pop("sourceSnippet", None)
        additions.append(source)
        approved_ids.add(str(item["id"]))
    pending_rows = [x for x in queue if str(x.get("id")) not in approved_ids]
    # Hold only recent, unresolved exceptions. Old queue entries expire
    # without adding them to the reviewed/deleted ledger.
    pending_rows = [x for x in pending_rows if
                    (d := parse_date(x.get("publishedAt"))) is not None
                    and dt.timedelta() <= now-d <= dt.timedelta(hours=MAX_AGE_HOURS)]
    # The previous pending pool may be huge: bound it without touching reviewed IDs.
    pending_rows = pending_rows[:MAX_PENDING]
    next_pending = dict(pending)
    if next_pending.get("updates") != pending_rows:
        next_pending["updates"] = pending_rows
        next_pending["generatedAt"] = now.isoformat().replace("+00:00", "Z")
    next_published = dict(published)
    if additions:
        next_published["updates"] = (additions + existing)[:MAX_PUBLISHED]
        next_published["generatedAt"] = now.isoformat().replace("+00:00", "Z")
    return next_pending, next_published, len(additions)


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc)
    # Fail closed on missing control: no accidental auto publication on rollout.
    control = read_json(CONTROL, {"autoPublishEnabled": False})
    pending = read_json(PENDING, {"updates": []})
    published = read_json(PUBLISHED, {"updates": []})
    reviewed = read_json(REVIEWED, {"ids": []})
    new_queue, new_public, count = publish(pending, published, reviewed, control, now)
    changed_queue = write_if_changed(PENDING, new_queue)
    changed_feed = write_if_changed(PUBLISHED, new_public) if count else False
    print(f"Buzz daily: auto-published={count}, pending={len(new_queue['updates'])}, "
          f"queue_changed={changed_queue}, feed_changed={changed_feed}, "
          f"enabled={control.get('autoPublishEnabled') is True}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
