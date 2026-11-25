# Namma Thalapathy public Buzz content

This repository hosts the public JSON consumed by the Android app. It is a
**source-linked fan information feed**, not a mirror of articles and not an
official publisher/TVK feed.

## Daily collection and guarded publication

The **existing single GitHub Actions workflow** runs **once each day at
00:30 UTC / 06:00 IST** (GitHub's scheduled-run time may be delayed).
No additional workflow or paid service is needed.

1. `scripts/update_news.py` discovers Google News RSS search results and
   stages at most 500 candidates in `pending_articles.json`. It does not
   scrape article bodies or make paid AI calls.
2. `scripts/auto_publish_buzz.py` reads `buzz_control.json` and publishes
   **at most 8 eligible items per UTC day**. Conservative gates include an
   allowlisted publisher label, a valid HTTPS source link, a recent date, an
   explicit Vijay/TVK context and a concrete student welfare, health,
   environment or community-help activity in the title. Disputes, accusations,
   elections and other contested political claims are **not** automatically
   published. A publisher label alone does **not** verify the event.
3. To avoid republishing copyrighted RSS excerpts, each eligible item uses a
   short **original factual topic notice** that says the source reports on
   the relevant activity and links to its reporting. This is not a detailed,
   independently verified article summary or a guarantee of copyright clearance.
   No third-party photographs are imported.
4. Uncertain articles remain in the **optional** owner review queue, capped
   at 60 recent items. Older pending items expire without being published.
   The owner never needs to approve each article just to keep the feed running.
5. Existing published entries and `reviewed_articles.json` are preserved.
   A failed source fetch preserves previously published content. No fake
   articles or invented article-specific details are generated.

## Phone control

A private Telegram bot webhook can run on the existing PHP/Plesk installation
without creating a separate mobile admin login. The webhook **does not activate
until the owner privately configures** a Telegram bot token, their exact
Telegram numeric user ID and a random webhook secret. See the app repository
file `plesk/community/TELEGRAM_OWNER_CONTROLS.md`.

Available Buzz commands: `/status`, `/queue`, `/pause`, `/resume`.
Queue review is optional, not a requirement for every article.

The bot updates the existing `buzz_control.json` switch; it does not change
workflow frequency, use a public admin endpoint or expose credentials in code.

## Operational limits

- Never trigger GitHub Actions manually unless the owner approves.
- The daily workflow is scheduled automatically as requested. The Android
  app fetches the published JSON, never invokes AI.
- RSS coverage, Google News links, publisher terms, and factual claims still
  require periodic review. Old `RSS_EXCERPT` entries from before this migration
  remain in `updates.json` until separately assessed; do not falsely describe
  them as rewritten.
- If the new conservative filter rejects everything on a given day, no new
  stories are published. This is safer than publishing unverifiable content.
- Run `python -m unittest discover -s tests -v` locally before production
  changes. The same test command runs inside the scheduled workflow.
