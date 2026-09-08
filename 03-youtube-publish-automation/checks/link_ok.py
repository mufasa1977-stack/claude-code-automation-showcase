"""Example pre-publish check: the CTA link must be a live, specific page (not a bare domain).

Called by yt_publish.py as:  python link_ok.py --bank <slice.json> --link <url>
Exit 0 = proceed. Any other exit = abort the publish run (output is shown to the operator).
"""
import argparse
import sys
import urllib.parse
import urllib.request


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bank", required=True)
    ap.add_argument("--link", required=True)
    a = ap.parse_args()
    u = urllib.parse.urlparse(a.link)
    if u.scheme not in ("http", "https") or not u.netloc:
        print(f"REFUSE: not a URL: {a.link}")
        return 2
    if u.path in ("", "/"):
        print("REFUSE: link points at a site root; send viewers to the specific offer page")
        return 2
    try:
        req = urllib.request.Request(a.link, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            if r.status >= 400:
                print(f"REFUSE: link answers HTTP {r.status}")
                return 2
    except Exception as e:
        print(f"REFUSE: link unreachable ({e})")
        return 2
    print(f"ok: {a.link} is live and specific")
    return 0


if __name__ == "__main__":
    sys.exit(main())
