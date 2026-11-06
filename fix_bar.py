"""Cut the browser's tabs and address bar off videos already on the Internet Archive, in place.

Until 6 November 2026 the videos with sound were a grab of the whole virtual screen, and Chrome's --kiosk did not hide
its bar there, so the top 88 rows showed the tabs and the address the game was played at: for Fantasy Empires, the
maker's private server (LostMystic: "You can't do that ... make a rule that you cannot show the top few bars").
showcase.py now records the page itself only. This takes each named item's showcase.mp4, cuts the top rows off and pads
the picture back to 1280x720 with black, makes a new poster and item tile from it, and puts them back under the same
names without keeping the old version, so the hub's links stay as they are.

    FIX_ITEMS  the items, comma between (evergamer-<game>-<yyyymmdd-hhmm>)
    FIX_TOP    rows to cut off the top (88: Chrome's tab strip and toolbar at 1280x720)

Nothing it prints names the address the game was played at (the logs of a public repository are public).
"""

import os
import subprocess
import urllib.request
from pathlib import Path

from showcase import W, H, OUT, ff, ia_put, log

KEEP_NOTHING = {"x-archive-keep-old-version": "0"}


def bar_left(video, top):
    """Whether anything like the bar is still at the top: the bar is light grey and white, the page under it dark."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vf", f"fps=1/5,crop={W}:{top}:0:0,format=gray",
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    n = W * top
    frames = [raw[i:i + n] for i in range(0, len(raw) - n + 1, n)]
    return sum(1 for f in frames if sum(f) / n > 200)


def fix(item, top):
    work = OUT / "fix" / item
    work.mkdir(parents=True, exist_ok=True)
    src, out, poster = work / "original.mp4", work / "showcase.mp4", work / "poster.jpg"
    urllib.request.urlretrieve(f"https://archive.org/download/{item}/showcase.mp4", src)
    before = bar_left(src, top)
    ff("-i", str(src), "-vf", f"crop={W}:{H - top}:0:{top},pad={W}:{H}:0:{top // 2}:black", "-c:v", "libx264",
       "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart",
       "-threads", "2", str(out))
    after = bar_left(out, top)
    log(f"{item}: frames with the bar, every 5 s: {before} before, {after} after")
    if after:
        raise SystemExit(f"{item}: the bar is still there; nothing uploaded")
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                               capture_output=True, text=True, check=True).stdout)
    ff("-ss", str(max(1, dur * 0.4)), "-i", str(out), "-frames:v", "1", "-q:v", "4", str(poster))
    tile = work / "__ia_thumb.jpg"
    ff("-i", str(poster), "-vf", "scale=360:-2", "-q:v", "4", str(tile))
    for path, mime in ((out, "video/mp4"), (poster, "image/jpeg"), (tile, "image/jpeg")):
        ia_put(item, path, mime, KEEP_NOTHING)
    log(f"{item}: replaced")


def main():
    top = int(os.environ.get("FIX_TOP", "88"))
    items = [i.strip() for i in os.environ.get("FIX_ITEMS", "").split(",") if i.strip()]
    for item in items:
        if not item.startswith("evergamer-"):
            raise SystemExit(f"{item}: not one of the bot's items")
        fix(item, top)


if __name__ == "__main__":
    main()
