# 02 - Social comment monitor + alerting

**Problem.** A small business posts a video ad and then has to keep opening the app to see whether
anyone commented. Comments answered within the hour convert; comments found two days later do not.

**What it does.**
- Polls each watched post's public page every 30 minutes (scheduled task / cron). No login, no
  platform API keys, no scraping service.
- Diffs the comment count against saved state and sends a Telegram alert with the link the
  moment a new comment appears.
- Appends views / likes / comments / shares / saves to a JSONL ledger on every run, so you get a
  free time-series of every post's traffic as a by-product.
- If a page stops being readable (blocked, layout change), it alerts once that it is blind instead
  of silently returning zeros forever.

**Engineering notes.**
- Parser and alert decision are pure functions covered by `--selftest` with real specimens
  (a page with the stats blob, a login wall, increase / no-change / decrease / first-sight).
- Adding a post = one JSON entry. Instagram support in production uses a second parser with the
  same state/alert core.

**Stack.** Python 3 standard library only (`urllib`, `re`, `json`). Telegram Bot API. Windows Task
Scheduler (`pythonw`, hidden) or cron.

**Run.**
```
cp watch_config.example.json watch_config.json    # add your posts
export TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
python comment_monitor.py --selftest
python comment_monitor.py
```

**Proof.** `../docs/02_monitor_selftest.png`, `../docs/02_monitor_ledger.png` (ledger rows from a real
30-minute cadence, labels anonymized).
