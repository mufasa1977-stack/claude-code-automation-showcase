"""comment_monitor.py - watch public social posts and alert (Telegram) when a new comment lands.

WHAT IT DOES
  Every run polls each watched post's PUBLIC page (no login, no platform API key), parses the
  embedded stats JSON, diffs the comment count against saved state, and on a genuine increase
  sends a Telegram alert with the post link. Every run also appends a stats row
  (views / likes / comments / shares / saves) to a JSONL ledger, so the traffic picture accrues
  even when nothing alerts.

DESIGN RULES (each one came from a real failure)
  * First sight of a post must NOT alert (else every new post spams once).
  * Equal or lower counts must NOT alert.
  * A blocked / login page must parse to None - never to 0 or a hallucinated number. None feeds a
    "blind" counter; after N consecutive failures the monitor alerts ONCE that it cannot see,
    rather than dying silently. (A sensor without a drain is just noise.)
  * --selftest exercises the parser and the alert decision against real specimens, so a regression
    in either trips a non-zero exit before it reaches the scheduler.

CONFIG
  watch_config.json  {"posts":[{"url":..., "label":..., "platform":"tiktok"}]}   (see example)
  TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID   environment variables for alerts
  State and ledger live next to this script (_state.json, _stats_log.jsonl).

SCHEDULE
  Windows Task Scheduler / cron, every 30 minutes:  python comment_monitor.py
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = Path(os.environ.get("WATCH_CONFIG", HERE / "watch_config.json"))
STATE = HERE / "_state.json"
LEDGER = HERE / "_stats_log.jsonl"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
FAIL_ALERT_AT = 6  # consecutive parse failures before the single "I'm blind" alert


def tg_send(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        print("alert (no Telegram configured): " + text)
        return False
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status == 200
    except Exception as e:
        print(f"tg_send failed: {e}", file=sys.stderr)
        return False


def parse_stats(html: str):
    """Extract counts from a public post page. Pure (no network) so it can be specimen-tested.

    Returns None when the page carries no commentCount - that is the 'blind' signal, and it
    must never be replaced by a made-up number.
    """
    out = {}
    for key in ("commentCount", "playCount", "diggCount", "shareCount", "collectCount"):
        m = re.search(rf'"{key}"\s*:\s*"?(\d+)"?', html)
        if m:
            out[key] = int(m.group(1))
    return out if "commentCount" in out else None


def is_new_comment(prev, cur) -> bool:
    """Alert only on a genuine increase. prev is None on first sight -> no alert."""
    return prev is not None and cur > prev


def fetch_stats(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode("utf-8", "replace")
    return parse_stats(html)


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def main() -> int:
    cfg = load(CONFIG, {"posts": []})
    state = load(STATE, {})
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    for post in cfg["posts"]:
        if post.get("platform", "tiktok") != "tiktok":
            continue  # other platforms get their own parser; the diff/alert logic is shared
        url, label = post["url"], post.get("label", post["url"])
        st = state.setdefault(url, {"commentCount": None, "fails": 0, "blind_alerted": False})
        try:
            stats = fetch_stats(url)
        except Exception as e:
            stats = None
            print(f"fetch failed for {label}: {e}", file=sys.stderr)
        if not stats:
            st["fails"] += 1
            if st["fails"] >= FAIL_ALERT_AT and not st["blind_alerted"]:
                tg_send(f"WARNING: monitor cannot read {label} ({st['fails']} runs straight); "
                        f"it may be blocked.")
                st["blind_alerted"] = True
            continue
        st["fails"], st["blind_alerted"] = 0, False
        with LEDGER.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": now, "label": label, **stats}) + "\n")
        prev, cur = st.get("commentCount"), stats["commentCount"]
        if is_new_comment(prev, cur):
            tg_send(f"New comment on {label} ({prev} -> {cur}). "
                    f"Views {stats.get('playCount', '?')} / likes {stats.get('diggCount', '?')}.\n{url}")
        st["commentCount"] = cur
    STATE.write_text(json.dumps(state, indent=1), encoding="utf-8")
    print(f"ok {now}")
    return 0


def selftest() -> int:
    # Specimen A: parser must extract the real count from a page carrying the stats blob.
    GOOD_HTML = (
        '<script>window["SIGI_STATE"]={"ItemModule":{"7401":{"id":"7401",'
        '"stats":{"playCount":"120400","diggCount":"8800","commentCount":"137",'
        '"shareCount":"512","collectCount":"640"}}}}</script>'
    )
    # Control A: a blocked/login page has no commentCount -> must be None (not 0, not a guess).
    BLOCKED_HTML = "<html><body>Log in to continue</body></html>"

    checks = {
        "parser reads commentCount=137 from a real page": (parse_stats(GOOD_HTML) or {}).get("commentCount") == 137,
        "parser reads playCount=120400": (parse_stats(GOOD_HTML) or {}).get("playCount") == 120400,
        "blocked page parses to None (no hallucinated number)": parse_stats(BLOCKED_HTML) is None,
        "genuine increase alerts (137->141)": is_new_comment(137, 141) is True,
        "no change stays quiet (141->141)": is_new_comment(141, 141) is False,
        "decrease stays quiet (141->140)": is_new_comment(141, 140) is False,
        "first sight stays quiet (None->141)": is_new_comment(None, 141) is False,
    }
    for label, ok in checks.items():
        print(f"  {'ok  ' if ok else 'FAIL'} {label}")
    ok_all = all(checks.values())
    print(f"selftest {'PASS' if ok_all else 'FAIL'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else main())
