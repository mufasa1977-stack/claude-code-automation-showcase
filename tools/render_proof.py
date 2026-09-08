"""render_proof.py - run a command and render its terminal output to a PNG (for docs/).

  python tools/render_proof.py --out docs/x.png --title "title" -- python some_script.py --selftest
"""
import argparse
import os
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont


def font(size):
    for name in ("consola.ttf", "DejaVuSansMono.ttf", "cour.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def main() -> int:
    if sys.argv[1:2] == ["--selftest"]:
        print("render_proof selftest ok (PIL importable)")
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="")
    ap.add_argument("--cwd", default=None)
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=a.cwd, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    lines = [f"$ {' '.join(os.path.basename(c) if os.sep in c else c for c in cmd)}"] + out.rstrip().splitlines() \
        + [f"[exit {r.returncode}]"]
    f = font(16)
    pad, lh = 18, 22
    width = max(760, min(1400, max(f.getlength(l) for l in lines) + 2 * pad + 20))
    height = pad * 2 + lh * (len(lines) + (2 if a.title else 0))
    img = Image.new("RGB", (int(width), int(height)), (24, 26, 32))
    d = ImageDraw.Draw(img)
    y = pad
    if a.title:
        d.text((pad, y), a.title, font=font(18), fill=(160, 200, 255))
        y += lh * 2
    for l in lines:
        color = (200, 200, 200)
        if l.startswith("$ "):
            color = (120, 220, 120)
        elif "FAIL" in l or "BLOCK" in l or "REFUSE" in l:
            color = (255, 130, 130)
        elif "ok" in l.lower()[:6] or "PASS" in l or "ISSUED" in l or "clean" in l:
            color = (140, 230, 160)
        d.text((pad, y), l, font=f, fill=color)
        y += lh
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    img.save(a.out)
    print(f"wrote {a.out} (exit {r.returncode})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
