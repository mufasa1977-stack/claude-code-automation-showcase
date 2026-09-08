#!/usr/bin/env python3
"""go_stats.py - read the click counters behind /go/<slug> as a sorted table.

  GO_STATS_URL=https://your-domain.com/go/_stats  GO_STATS_KEY=...  python go_stats.py [--json]

A browser-like User-Agent is sent on purpose: Cloudflare answers 403 to a bare urllib UA.
"""
import json
import os
import sys
import urllib.request

url = os.environ.get("GO_STATS_URL", "")
key = os.environ.get("GO_STATS_KEY", "")
if not url or not key:
    sys.exit("set GO_STATS_URL and GO_STATS_KEY")
req = urllib.request.Request(url + "?key=" + key,
                             headers={"User-Agent": "Mozilla/5.0 (go_stats.py)"})
data = json.loads(urllib.request.urlopen(req, timeout=20).read())
if "--json" in sys.argv:
    print(json.dumps(data, indent=1))
    sys.exit(0)
if not data:
    print("no clicks recorded yet")
for k in sorted(data):
    print(f"{data[k]:6d}  {k}")
