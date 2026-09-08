"""duration_gate.py - example external gate: a video must be within the surface's length limits.

Uses ffprobe when present. If the duration cannot be MEASURED the gate fails closed - a gate that
cannot see must never pass. Non-video files (used in the repo's demo) pass with a note.
"""
import argparse
import json
import shutil
import subprocess
import sys

MAX_SECONDS = 180  # adjust per surface


def duration_seconds(path: str):
    if not shutil.which("ffprobe"):
        return None
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "json", path], capture_output=True, text=True, timeout=60)
    try:
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset", required=True)
    a = ap.parse_args()
    if not a.asset.lower().endswith((".mp4", ".mov", ".webm", ".mkv")):
        print("not a video file; duration gate not applicable")
        return 0
    d = duration_seconds(a.asset)
    if d is None:
        print("cannot measure duration (ffprobe missing or unreadable file) - fail closed")
        return 2
    if d > MAX_SECONDS:
        print(f"duration {d:.1f}s exceeds the {MAX_SECONDS}s limit for this surface")
        return 2
    print(f"duration {d:.1f}s ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
