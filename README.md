# Namma Thalapathy public content

Public JSON content consumed by the Namma Thalapathy Android app.

## Automated news feed

The scheduled GitHub Actions workflow collects recent Vijay/TVK-related items from Google News RSS search feeds, sends one batch request per run to Gemini for concise neutral summaries, and updates `updates.json`. App users only download the shared JSON file; the Android app does not call Gemini.

### One-time setup

1. Open **Settings → Secrets and variables → Actions** in this repository.
2. Create a repository secret named `GEMINI_API_KEY` with your Gemini API key.
3. Open **Actions**, enable workflows if GitHub prompts you, then run **Automated Namma Thalapathy News Feed → Run workflow**.
4. Check the workflow run logs. The workflow also runs every two hours (UTC schedule: minute 17 of every second hour).

### Behaviour and safeguards

- One Gemini batch request is made per successful ingestion run, not per story or app user.
- The model receives only article headlines, feed descriptions, publisher labels and IDs. It is instructed not to invent facts or claim verification.
- Each published item includes its headline, original feed link, publisher label, publication time, generated summary and a `SOURCE_LINKED` status. This label means a source link is present; it is not a claim that the story has been independently verified.
- The pipeline deduplicates stories, retains up to 100 items and preserves the last valid feed if sources or Gemini fail.
- It uses no database and no third-party Python packages.
- The feed uses Google News RSS search URLs as a discovery source. Availability, source coverage, feed terms and model quotas may vary; inspect links before treating a story as independently verified.
- Keep the Gemini key in GitHub Actions secrets. Never put it in Android code or any public JSON file.
