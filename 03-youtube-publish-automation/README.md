# 03 - YouTube publishing automation (OAuth, channel guard, pre-publish checks)

**Problem.** A brand produces short videos in batches and posts them by hand: log in, pick a file,
paste a title, paste a description, set privacy, repeat thirty times. Hand-posting is slow, error-prone
(wrong channel, wrong link, duplicate posts), and invisible to any later audit.

**What it does.**
- Pairs `clip_NN.mp4` with entry N of a JSON "bank" (hook line, facts, source) and builds
  title / description / tags automatically.
- Uploads through the YouTube Data API resumable endpoint with a stored OAuth refresh token - no
  browser after the one-time consent.
- **Channel guard:** resolves and prints the channel the token belongs to *before* touching a file;
  `--expect-channel` aborts on a mismatch.
- **Idempotent ledger:** `uploaded.json` records index, videoId, URL, link, timestamp; re-runs never
  double-post; the ledger doubles as the audit trail for later cadence/CTA reporting.
- **Pluggable pre-publish checks** (`--check script.py`, repeatable): each check sees only the slice
  about to go out and any non-zero exit aborts the run. Example included: `checks/link_ok.py`
  (the CTA must be a live, specific page - not a site root).
- Defaults to `unlisted`; `--dry-run` for rehearsal.

**Engineering notes from production (100+ uploads).**
- Checks judge *what is being shipped*, never the whole history - re-judging already-live clips
  produces false aborts, and false aborts teach operators to bypass the check.
- Keep the Google OAuth app in **production** mode; testing-mode refresh tokens die every 7 days.

**Stack.** Python 3, `requests`, Google OAuth 2.0 refresh-token flow, YouTube Data API v3.

**Run.**
```
pip install requests
export YT_CREDENTIALS=/secure/path/yt_credentials.json   # {client_id, client_secret, refresh_token}
python yt_publish.py --selftest
python yt_publish.py --bank bank.example.json --dir clips/ --link https://example.com/offer \
    --expect-channel "My Brand" --limit 2 --check checks/link_ok.py --dry-run
```

**Proof.** `../docs/03_publish_selftest.png`.
