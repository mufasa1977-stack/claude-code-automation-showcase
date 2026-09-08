"""yt_publish.py - batch-publish a folder of videos to YouTube with OAuth, a channel guard,
pluggable pre-publish checks, and an idempotent upload ledger.

WHAT IT DOES
  Reads a JSON "bank" (one entry per clip: hook line, supporting facts, source), pairs entry N
  with clip_NN.mp4 in --dir, builds title/description/tags, and uploads via the YouTube Data API
  resumable upload using a stored OAuth refresh token (no browser after the first consent).

SAFETY RAILS (each one exists because of a real near-miss)
  * CHANNEL GUARD: it prints the channel the token resolves to BEFORE uploading anything, and
    --expect-channel aborts unless that channel's title matches. (A batch of 30 clips once came
    within one keypress of landing on the wrong channel.)
  * IDEMPOTENT LEDGER: every upload is recorded in <dir>/uploaded.json (index, videoId, url, link,
    timestamp). Re-running never double-posts. The ledger is also the producer side of any
    cadence or CTA audit you run later.
  * DEFAULT PRIVACY = unlisted. Verify, then flip to public with --privacy public.
  * PRE-PUBLISH CHECKS: --check <script> can be given more than once. Each script receives the
    JSON slice this run will upload (only that slice; never re-judge clips already live) and a
    non-zero exit aborts the run with the check's output. Use it for a content-quality scorer, a
    link validator, a posting-cadence cap, a brand-voice check, a secrets scan, etc.
  * --dry-run does everything except the upload.

CREDENTIALS
  YT_CREDENTIALS env var -> path to a JSON file {client_id, client_secret, refresh_token}.
  Keep the OAuth app in PRODUCTION mode; testing-mode refresh tokens expire every 7 days.

USAGE
  python yt_publish.py --bank bank.json --dir clips/ --link https://example.com/offer \
      --expect-channel "My Brand" --limit 3 [--privacy public] [--check checks/link_ok.py]
  python yt_publish.py --selftest
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    requests = None

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"


# ---- OAuth -----------------------------------------------------------------
def access_token(creds_path: str) -> str:
    with open(creds_path, encoding="utf-8") as f:
        c = json.load(f)
    r = requests.post(TOKEN_URL, data={
        "client_id": c["client_id"], "client_secret": c["client_secret"],
        "refresh_token": c["refresh_token"], "grant_type": "refresh_token"}, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def whoami(tok: str) -> dict:
    r = requests.get(CHANNELS_URL, params={"part": "snippet,statistics", "mine": "true"},
                     headers={"Authorization": "Bearer " + tok}, timeout=30).json()
    items = r.get("items", [])
    if not items:
        sys.exit("could not resolve a channel for this token")
    s, st = items[0]["snippet"], items[0]["statistics"]
    return {"title": s["title"], "id": items[0]["id"], "handle": s.get("customUrl"),
            "subs": st.get("subscriberCount"), "videos": st.get("videoCount")}


# ---- pure helpers (covered by --selftest) ----------------------------------
def build_meta(item: dict, link: str, tags: list, footer: str = ""):
    """Bank entry -> (title, description, tags). Title <= 100 chars per YouTube."""
    hook = item["hook"].strip()
    title = hook if len(hook) <= 95 else hook[:92] + "..."
    if "#shorts" not in title.lower() and len(title) <= 86:
        title += " #Shorts"
    facts = "\n".join("- " + f for f in item.get("facts", []))
    src = item.get("src", "")
    desc = f"{hook}\n\n{facts}\n\nMore: {link}\n"
    if footer:
        desc += "\n" + footer + "\n"
    if src:
        desc += f"\nSource: {src}\n"
    desc += "\n" + " ".join("#" + t.replace(" ", "") for t in tags[:5])
    return title, desc[:4900], tags


def select_items(bank: list, start: int, limit: int, already: set):
    """Which (index, item) pairs will THIS run upload? Skips the ledger and honours --start/--limit."""
    out = []
    for i, item in enumerate(bank, 1):
        if i < start or i in already:
            continue
        if limit and len(out) >= limit:
            break
        out.append((i, item))
    return out


def channel_matches(expected: str, title: str) -> bool:
    return (not expected) or (expected.lower() in (title or "").lower())


# ---- upload ----------------------------------------------------------------
def upload(tok: str, path: str, title: str, desc: str, tags: list, privacy: str):
    meta = {"snippet": {"title": title, "description": desc, "tags": tags, "categoryId": "27"},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}
    size = os.path.getsize(path)
    init = requests.post(UPLOAD_URL,
                         params={"uploadType": "resumable", "part": "snippet,status"},
                         headers={"Authorization": "Bearer " + tok,
                                  "Content-Type": "application/json; charset=UTF-8",
                                  "X-Upload-Content-Length": str(size),
                                  "X-Upload-Content-Type": "video/mp4"},
                         data=json.dumps(meta).encode("utf-8"), timeout=60)
    if init.status_code not in (200, 201):
        return None, f"init HTTP {init.status_code}: {init.text[:200]}"
    session_url = init.headers.get("Location")
    with open(path, "rb") as f:
        put = requests.put(session_url, headers={"Authorization": "Bearer " + tok,
                                                 "Content-Length": str(size),
                                                 "Content-Type": "video/mp4"},
                           data=f, timeout=600)
    if put.status_code not in (200, 201):
        return None, f"upload HTTP {put.status_code}: {put.text[:200]}"
    return put.json().get("id"), None


# ---- pre-publish checks ----------------------------------------------------
def run_checks(check_scripts: list, selected: list, link: str) -> None:
    """Each check gets ONLY the slice being uploaded. Non-zero exit aborts the run."""
    if not check_scripts or not selected:
        return
    fd, slice_path = tempfile.mkstemp(suffix=".json", prefix="publish_slice_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump([item for _, item in selected], f, ensure_ascii=False)
    try:
        for script in check_scripts:
            r = subprocess.run([sys.executable, script, "--bank", slice_path, "--link", link],
                               capture_output=True, text=True, timeout=300)
            out = ((r.stdout or "") + (r.stderr or "")).strip()
            print(f"[check] {os.path.basename(script)} -> exit {r.returncode}")
            if out:
                print("        " + out.replace("\n", "\n        "))
            if r.returncode != 0:
                sys.exit(f"ABORT: pre-publish check {os.path.basename(script)} refused this run.")
    finally:
        if os.path.exists(slice_path):
            os.unlink(slice_path)


# ---- main ------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bank", required=True, help="JSON list of {hook, facts[], src}")
    ap.add_argument("--dir", required=True, help="folder holding clip_01.mp4, clip_02.mp4, ...")
    ap.add_argument("--link", required=True, help="call-to-action URL placed in every description")
    ap.add_argument("--privacy", default="unlisted", choices=["private", "unlisted", "public"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--start", type=int, default=1, help="1-based bank index to start from")
    ap.add_argument("--tags", default="")
    ap.add_argument("--footer", default="", help="text appended to every description")
    ap.add_argument("--expect-channel", default="", help="abort unless the channel title contains this")
    ap.add_argument("--check", action="append", default=[], help="pre-publish check script (repeatable)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--creds", default=os.environ.get("YT_CREDENTIALS", "yt_credentials.json"))
    a = ap.parse_args()

    if requests is None:
        sys.exit("pip install requests")

    tok = access_token(a.creds)
    ch = whoami(tok)
    print(f"CHANNEL: {ch['title']}  ({ch['handle']})  subs={ch['subs']} videos={ch['videos']}")
    if not channel_matches(a.expect_channel, ch["title"]):
        sys.exit(f"ABORT: expected channel containing '{a.expect_channel}', got '{ch['title']}'")
    print(f"PRIVACY: {a.privacy}   DRY-RUN: {a.dry_run}\n")

    with open(a.bank, encoding="utf-8") as f:
        bank = json.load(f)
    ledger_path = os.path.join(a.dir, "uploaded.json")
    done = []
    if os.path.exists(ledger_path):
        with open(ledger_path, encoding="utf-8") as f:
            done = json.load(f)
    already = {d["index"] for d in done}
    tags = [t.strip() for t in a.tags.split(",") if t.strip()]

    selected = select_items(bank, a.start, a.limit, already)
    print(f"this run: {len(selected)} clip(s)  (bank {len(bank)}, already live {len(already)})")
    run_checks(a.check, selected, a.link)

    n = 0
    for i, item in selected:
        path = os.path.join(a.dir, f"clip_{i:02d}.mp4")
        if not os.path.exists(path):
            print(f"[{i:02d}] SKIP - no file {path}")
            continue
        title, desc, tg = build_meta(item, a.link, tags, a.footer)
        if a.dry_run:
            print(f"[{i:02d}] DRY  {title[:70]}")
            continue
        vid, err = upload(tok, path, title, desc, tg, a.privacy)
        if err:
            print(f"[{i:02d}] FAIL {err}")
            continue
        url = f"https://www.youtube.com/watch?v={vid}"
        print(f"[{i:02d}] OK  {url}  <- {title[:60]}")
        done.append({"index": i, "videoId": vid, "url": url, "title": title, "link": a.link,
                     "privacy": a.privacy,
                     "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")})
        with open(ledger_path, "w", encoding="utf-8") as f:
            json.dump(done, f, indent=2)
        n += 1
        time.sleep(2)
    print(f"\nuploaded {n} | ledger {ledger_path}")


def selftest() -> int:
    failures = []

    def chk(label, cond):
        print(f"  {'ok  ' if cond else 'FAIL'} {label}")
        if not cond:
            failures.append(label)

    item = {"hook": "Three things every first-time buyer gets wrong",
            "facts": ["Fact one", "Fact two"], "src": "Company blog, 2026"}
    title, desc, tags = build_meta(item, "https://example.com/go", ["buying guide", "tips"])
    chk("title gets #Shorts appended", title.endswith("#Shorts"))
    chk("title stays under YouTube's 100-char limit", len(title) <= 100)
    chk("description carries the CTA link", "https://example.com/go" in desc)
    chk("description carries the facts as bullets", "- Fact one" in desc and "- Fact two" in desc)
    chk("description carries hashtags", "#buyingguide" in desc)
    long_hook = {"hook": "x" * 140, "facts": []}
    t2, _, _ = build_meta(long_hook, "https://e.com", [])
    chk("over-long hook is truncated with an ellipsis", len(t2) <= 100 and t2.endswith("..."))

    bank = [{"hook": f"clip {i}"} for i in range(1, 8)]
    chk("ledger entries are skipped", [i for i, _ in select_items(bank, 1, 0, {1, 2})] == [3, 4, 5, 6, 7])
    chk("--start honoured", [i for i, _ in select_items(bank, 5, 0, set())] == [5, 6, 7])
    chk("--limit honoured", [i for i, _ in select_items(bank, 1, 2, set())] == [1, 2])
    chk("limit counts only NEW items", [i for i, _ in select_items(bank, 1, 2, {1})] == [2, 3])

    chk("channel guard: mismatch aborts", not channel_matches("My Brand", "Some Other Channel"))
    chk("channel guard: case-insensitive match passes", channel_matches("my brand", "MY BRAND Official"))
    chk("channel guard: empty expectation passes", channel_matches("", "Anything"))

    print(f"[selftest] {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(selftest()) if "--selftest" in sys.argv else main()
