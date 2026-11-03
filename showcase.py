#!/usr/bin/env python3
"""EverGamer showcase bot: a new video of a game each time it is updated.

Once a day (.github/workflows/showcase.yml) it asks the EverGamer hub which games have automatic videos switched on.
For each, one at a time, it reads the game's updates file. When the version changed since last time it opens the
game in a browser, goes to the areas the new notes talk about, plays (the game's own script, then pressing keys and
clicking about), records 2 to 5 minutes, uploads the video to the Internet Archive, deletes its copy, and tells the
hub, which shows it on the game's page with what changed.

Settings come from the hub (the maker sets them on their game's page). Areas a maker has not set there may come from
areas/<game id>.json here: they are not secret (words to look for, start options and scripts).

Environment:
  SHOWCASE_TOKEN      the hub's key for this bot (a repository secret)
  IA_ACCESS, IA_SECRET  the Internet Archive upload keys (https://archive.org/account/s3.php)
  HUB_API             the hub's server (default https://evergamer-gw-ams.fly.dev)
  SHOWCASE_DRY=1      record but do not upload or report (testing); the video stays in out/
  SHOWCASE_ONLY=<id>  only this game
  SHOWCASE_JOBS=file  read the jobs from a file instead of the hub (testing)
  SHOWCASE_FAKE_UPLOAD="<folder>|<address>"  copy the video there instead of uploading it (testing)

Nothing it prints names a game's address or login: the logs of a public repository are public.
"""

import base64
import hashlib
import json
import os
import random
import re
import resource
import shutil
import ssl
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
API = os.environ.get("HUB_API", "https://evergamer-gw-ams.fly.dev").rstrip("/")
SITE = "https://evergamer.com"
DRY = os.environ.get("SHOWCASE_DRY") == "1"
W, H = 1280, 720
MIN_SECONDS, MAX_SECONDS = 120, 300   # the video's length (LostMystic: "a 2 to 5 minute long video")
MIN_SECONDS = int(os.environ.get("SHOWCASE_MIN", MIN_SECONDS))  # shorter only for testing
MAX_GAMES = int(os.environ.get("SHOWCASE_MAX", "3"))  # recordings per run, so one run stays short
UA = "EverGamerShowcaseBot/1.0 (+https://evergamer.com)"


def redact(text, job):
    """Text safe for the public log: the game's addresses, hosts and login blanked out."""
    st = job.get("settings") or {}
    if isinstance(st, str):
        st = json.loads(st)
    for v in [st.get("play"), st.get("feed"), st.get("user"), st.get("pass")]:
        if v:
            text = text.replace(v, "<hidden>")
    for v in [st.get("play"), st.get("feed")]:
        host = urllib.parse.urlsplit(v or "").hostname
        if host:
            text = text.replace(host, "<host>")
    text = re.sub(r"\b\d{1,3}(\.\d{1,3}){3}\b", "<ip>", text)
    text = re.sub(r"https?://\S+", "<address>", text)
    return " | ".join(l.strip() for l in text.strip().splitlines()[-14:])


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---- the hub ---------------------------------------------------------------------------------------------------

def hub(path, body=None):
    req = urllib.request.Request(API + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + os.environ.get("SHOWCASE_TOKEN", ""),
                                          "Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def public_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def game_title(game):
    """The game's name on the hub: a game posted there, else one of the hub's own listings."""
    try:
        g = public_json(f"{API}/hub/game?id={game}").get("game")
        if g and g.get("data", {}).get("title"):
            return g["data"]["title"]
    except Exception:
        pass
    try:
        for p in public_json(f"{SITE}/test/data/projects.json"):
            if p.get("id") == game:
                return p.get("title") or game
    except Exception:
        pass
    return game


# ---- the game's updates ----------------------------------------------------------------------------------------

def fetch(url, s, binary=False):
    """GET with the game's login when the address is on the game's own host. A private build's certificate may be
    its own (self-signed); the bot reads it anyway, as a browser would after the one warning."""
    headers = {"User-Agent": UA, "Cache-Control": "no-cache"}
    play = urllib.parse.urlsplit(s.get("play", ""))
    if s.get("user") and urllib.parse.urlsplit(url).netloc == play.netloc:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{s['user']}:{s.get('pass', '')}".encode()).decode()
    req = urllib.request.Request(url, headers=headers)
    try:
        r = urllib.request.urlopen(req, timeout=60)
    except urllib.error.URLError as e:
        if not isinstance(getattr(e, "reason", None), ssl.SSLError):
            raise
        r = urllib.request.urlopen(req, timeout=60, context=ssl._create_unverified_context())
    with r:
        data = r.read()
    return data if binary else data.decode("utf-8", "replace")


def read_updates(s):
    """(version, notes, built). The updates file is JSON with a version and notes (a list, newest first, or text);
    without one the version is a fingerprint of the play page and the scripts it loads."""
    if s.get("feed"):
        d = json.loads(fetch(s["feed"], s))
        notes = d.get("notes") or d.get("changes") or []
        if isinstance(notes, str):
            notes = [n.strip() for n in notes.splitlines() if n.strip() and not n.lstrip().startswith("#")]
        return str(d.get("version") or d.get("built") or ""), [str(n) for n in notes][:60], str(d.get("built") or "")
    page = fetch(s["play"], s)
    h = hashlib.sha256(page.encode())
    for src in re.findall(r'<script[^>]+src="([^"]+)"', page)[:6]:
        try:
            h.update(fetch(urllib.parse.urljoin(s["play"], src), s, binary=True))
        except Exception:
            pass
    return h.hexdigest()[:16], [], ""


def new_notes(game, notes):
    """The notes not in the last video: what changed since. The first video takes the newest few."""
    try:
        vids = public_json(f"{API}/hub/showcase?game={game}").get("videos") or []
    except Exception:
        vids = []
    seen = set(vids[0].get("notes") or []) if vids else set()
    fresh = [n for n in notes if n not in seen]
    return (fresh if vids else notes)[:12]


# ---- what to show ----------------------------------------------------------------------------------------------

def areas_of(game, s):
    areas = s.get("areas") or []
    if not areas:
        f = HERE / "areas" / f"{game}.json"
        if f.exists():
            areas = json.loads(f.read_text()).get("areas", [])
    return areas


def pick(areas, notes):
    """The areas the notes talk about, most talked about first, with how long each plays: 2 to 5 minutes in all.
    With nothing matched, the area with no words (the game's default), else the game as it opens."""
    def words(a):
        return [w.strip().lower() for w in (a.get("match") or "").split(",") if w.strip()]
    text = [n.lower() for n in notes]
    scored = []
    for a in areas:
        ws = words(a)
        n = sum(1 for t in text if any(w in t for w in ws)) if ws else 0
        if n:
            scored.append((n, a))
    scored.sort(key=lambda x: -x[0])
    chosen = [a for _, a in scored[:3]]
    if not chosen:
        chosen = [a for a in areas if not words(a)][:1] or areas[:1] or [{"name": "The game", "query": "", "explore": True}]
    secs = [int(a.get("seconds") or 90) for a in chosen]
    total = sum(secs)
    if total < MIN_SECONDS:
        secs[-1] += MIN_SECONDS - total
    elif total > MAX_SECONDS:
        secs = [max(20, s * MAX_SECONDS // total) for s in secs]
    return list(zip(chosen, secs))


# ---- recording -------------------------------------------------------------------------------------------------

def area_url(play, query, script_name):
    q = (query or "").replace("{script}", script_name)
    if not q:
        return play
    q = q.lstrip("?&")
    return play + ("&" if "?" in play else "?") + q


def run_steps(page, steps):
    """A maker's steps, pressed by the bot: "wait 2", "key ArrowDown" (a press), "down KeyW" / "up KeyW" (hold and let
    go), "click 640 360" (a point on the 1280x720 page), "type Ada". Key names are the browser's (ArrowUp, Enter,
    Space, Escape, KeyA ...)."""
    for line in (steps or "").splitlines() if isinstance(steps, str) else (steps or []):
        word, _, rest = line.strip().partition(" ")
        rest = rest.strip()
        try:
            if word == "wait":
                page.wait_for_timeout(int(float(rest) * 1000))
            elif word == "key":
                page.keyboard.press(rest)
            elif word == "down":
                page.keyboard.down(rest)
            elif word == "up":
                page.keyboard.up(rest)
            elif word == "click":
                x, y = (int(float(v)) for v in rest.split()[:2])
                page.mouse.click(x, y)
            elif word == "type":
                page.keyboard.type(rest, delay=80)
        except Exception:
            pass


EXPLORE_KEYS = ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "ArrowRight", "ArrowUp", "Space", "Enter"]


def explore(page, seconds):
    """Press keys and click about, like someone trying the game out."""
    end = time.time() + seconds
    while time.time() < end:
        r = random.random()
        try:
            if r < 0.45:
                page.mouse.click(random.randint(W // 8, W * 7 // 8), random.randint(H // 6, H * 5 // 6))
                page.wait_for_timeout(random.randint(1200, 3000))
            else:
                k = random.choice(EXPLORE_KEYS)
                page.keyboard.down(k)
                page.wait_for_timeout(random.randint(250, 1200))
                page.keyboard.up(k)
                page.wait_for_timeout(random.randint(300, 900))
        except Exception:
            page.wait_for_timeout(500)


_x11 = {}


def x11_ready():
    """Sound needs a real (virtual) screen and a sound server to record from: Xvfb and PulseAudio, installed the first
    time a game wants sound (the runner lets the job use sudo). Without sound the browser runs headless instead."""
    if _x11:
        return True
    if not shutil.which("Xvfb") or not shutil.which("pulseaudio"):
        subprocess.run(["sudo", "apt-get", "install", "-y", "-qq", "xvfb", "pulseaudio"], check=True,
                       stdout=subprocess.DEVNULL)
    _x11["xvfb"] = subprocess.Popen(["Xvfb", ":99", "-screen", "0", f"{W}x{H}x24", "-nolisten", "tcp"])
    os.environ["DISPLAY"] = ":99"
    subprocess.run(["pulseaudio", "--start", "--exit-idle-time=-1"], check=True)
    subprocess.run(["pactl", "load-module", "module-null-sink", "sink_name=rec"], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["pactl", "set-default-sink", "rec"], check=True)
    time.sleep(1)
    return True


def record_segment(pw, s, area, seconds, n, work):
    """One area: open it, let it load, play for its seconds. Answers the clip and where play began in it.
    Without sound: headless, the browser's own recording (which starts with the page, so the loading is cut later).
    With sound: on the virtual screen, ffmpeg takes the picture and the sound from the moment play begins."""
    sound = s.get("sound") or "off"
    with_sound = sound != "off"
    # Software WebGL: the runner has no graphics card.
    args = ["--autoplay-policy=no-user-gesture-required", "--disable-dev-shm-usage", "--use-angle=swiftshader",
            "--enable-unsafe-swiftshader"]
    if with_sound:
        x11_ready()
        args += [f"--window-size={W},{H}", "--window-position=0,0", "--kiosk", "--no-first-run"]
    else:
        args.append("--mute-audio")
    # The virtual screen goes to the browser itself: Playwright's driver started before x11_ready set DISPLAY, and
    # the browser takes the driver's environment, so without this it found no screen and quit (3 November 2026).
    browser = pw.chromium.launch(channel="chrome", headless=not with_sound, args=args,
                                 **({"env": {**os.environ, "DISPLAY": ":99"}} if with_sound else {}))
    opts = dict(viewport={"width": W, "height": H}, ignore_https_errors=True,
                http_credentials={"username": s["user"], "password": s.get("pass", "")} if s.get("user") else None)
    if not with_sound:
        opts.update(record_video_dir=str(work), record_video_size={"width": W, "height": H})
    ctx = browser.new_context(**opts)
    script_name = f"__showcase/{n}.txt"
    if area.get("script"):
        ctx.route("**/__showcase/*", lambda route: route.fulfill(status=200, content_type="text/plain", body=area["script"]))
    music = [m.lower() for m in (s.get("music") or []) if m]
    if sound == "nomusic" and music:
        ctx.route("**/*", lambda route: route.abort() if any(m in route.request.url.lower() for m in music) else route.continue_())
    page = ctx.new_page()
    # Why a page goes away, for the log (never its address): the tab crashing (out of memory, often).
    page.on("crash", lambda *_: log(f"  area {n}: the game's tab crashed"))
    t0 = time.time()
    page.goto(area_url(s["play"], area.get("query"), script_name), wait_until="load", timeout=120000)
    try:
        page.wait_for_load_state("networkidle", timeout=90000)
    except Exception:
        pass
    page.wait_for_timeout(int(area.get("settle", 2)) * 1000)
    # The game's picture: the biggest canvas (or video) on the page. A game that draws small in the middle of its page
    # is cut out and scaled up to fill the video.
    box = page.evaluate("""() => { let b = null; for (const e of document.querySelectorAll('canvas, video')) {
        const r = e.getBoundingClientRect(); if (r.width * r.height > (b ? b.width * b.height : 0)) b = r }
        return b && { x: Math.max(0, b.x), y: Math.max(0, b.y), w: Math.min(b.width, innerWidth), h: Math.min(b.height, innerHeight) } }""")
    crop = None
    if box and box["w"] >= 200 and box["h"] >= 150 and box["w"] * box["h"] < 0.9 * W * H:
        crop = tuple(int(round(v)) // 2 * 2 for v in (box["w"], box["h"], box["x"], box["y"]))
    start = time.time() - t0
    grab = None
    if with_sound:
        clip = work / f"clip{n}.mp4"
        grab = subprocess.Popen(["nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                                 "-f", "x11grab", "-video_size", f"{W}x{H}", "-framerate", "30", "-i", ":99",
                                 "-f", "pulse", "-i", "rec.monitor", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                                 "-c:a", "aac", "-b:a", "128k", "-threads", "2", str(clip)], stdin=subprocess.PIPE)
        start = 0.0
    t_play = time.time()
    run_steps(page, area.get("steps"))
    left = max(0.0, seconds - (time.time() - t_play))
    if area.get("explore"):
        # The script or steps play first (for exploreAfter seconds from the start, else about half the time when there
        # is a script); then the bot tries things out.
        after = float(area.get("exploreAfter", seconds * 0.55 if area.get("script") else 0))
        quiet = min(left, max(0.0, after - (time.time() - t_play)))
        page.wait_for_timeout(int(quiet * 1000))
        explore(page, left - quiet)
    else:
        page.wait_for_timeout(int(left * 1000))
    if grab:
        grab.communicate(b"q", timeout=60)
    else:
        clip = Path(page.video.path())
    ctx.close()
    browser.close()
    return clip, start, crop


def ff(*args):
    subprocess.run(["nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def has_drawtext():
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    return " drawtext " in out


# The small line kept along the bottom of the whole video (LostMystic, 3 November 2026: "put like a date or something
# like that when it's recorded ... if you have to mute the sound ... a tiny little note at the bottom").
MUTED = {"off": "Sound muted for copyright reasons", "nomusic": "Music muted for copyright reasons"}


def footnote(sound, when=None):
    return " \u00b7 ".join(x for x in [f"Recorded {when or time.strftime('%Y-%m-%d', time.gmtime())}", MUTED.get(sound)] if x)


def caption_filter(work, title, note=""):
    font = next((p for p in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                             "/usr/share/fonts/TTF/dejavu/DejaVuSans-Bold.ttf"] if Path(p).exists()), None)
    if not font or not has_drawtext():
        return None
    tf, nf = work / "caption.txt", work / "note.txt"
    tf.write_text(title)
    vf = (f"drawtext=fontfile={font}:textfile={tf}:x=24:y=h-th-28:fontsize=26:fontcolor=white:"
          f"box=1:boxcolor=black@0.55:boxborderw=12:enable='lt(t,7)'")
    if note:
        nf.write_text(note)
        vf += (f",drawtext=fontfile={font}:textfile={nf}:x=w-tw-10:y=h-th-8:fontsize=13:fontcolor=white@0.8:"
               f"box=1:boxcolor=black@0.45:boxborderw=4")
    return vf


def content_box(clip, start, secs):
    """Where the picture is: a game that draws small in the middle of its page (black round it) is cut out and scaled
    up to fill the video. ffmpeg's cropdetect, over the part that is kept."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-ss", f"{start + secs / 2:.2f}", "-i", str(clip), "-t", "8",
                        "-vf", "cropdetect=limit=24:round=2:reset=0", "-f", "null", "-"], capture_output=True, text=True)
    m = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", r.stderr)
    if not m:
        return None
    w, h, x, y = map(int, m[-1])
    if w < 200 or h < 150 or w * h > 0.85 * W * H:
        return None
    return w, h, x, y


def make_video(clips, work, title, sound):
    """Cut each clip's loading off, join them, and encode H.264 for the web (two threads, low priority)."""
    parts = []
    for i, (clip, start, secs, crop) in enumerate(clips):
        p = work / f"part{i}.mp4"
        crop = crop or content_box(clip, start, secs)
        audio = ["-c:a", "aac", "-b:a", "128k"] if sound != "off" else ["-an"]
        vf = (f"crop={crop[0]}:{crop[1]}:{crop[2]}:{crop[3]},scale={W}:{H}:force_original_aspect_ratio=decrease:flags=neighbor,"
              f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2" if crop else "null")
        ff("-ss", f"{start:.2f}", "-i", str(clip), "-t", str(secs), "-vf", vf, *audio, "-r", "30", "-c:v", "libx264",
           "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-threads", "2", str(p))
        parts.append(p)
    lst = work / "parts.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in parts))
    out = work / "showcase.mp4"
    vf = caption_filter(work, title, footnote(sound))
    enc = lambda vf: ff("-f", "concat", "-safe", "0", "-i", str(lst), *(["-vf", vf] if vf else []), "-c:v", "libx264", "-preset", "veryfast",
       "-crf", "28", "-pix_fmt", "yuv420p", *(["-c:a", "aac", "-b:a", "128k"] if sound != "off" else ["-an"]), "-movflags", "+faststart", "-threads", "2", str(out))
    try:
        enc(vf)
    except subprocess.CalledProcessError:
        if not vf:
            raise
        log("captions failed; the video goes without them (the page still shows the date and the sound note)")
        enc(None)
    poster = work / "poster.jpg"
    dur = sum(c[2] for c in clips)
    ff("-ss", str(max(1, dur * 0.4)), "-i", str(out), "-frames:v", "1", "-q:v", "4", str(poster))
    return out, poster, dur


# ---- the Internet Archive --------------------------------------------------------------------------------------

def ia_put(ident, path, mime, meta):
    headers = {"authorization": f"LOW {os.environ['IA_ACCESS']}:{os.environ['IA_SECRET']}",
               "Content-Type": mime, "Content-Length": str(path.stat().st_size), "x-archive-size-hint": str(path.stat().st_size)}
    for k, v in meta.items():
        headers[k] = "uri(" + urllib.parse.quote(v, safe="") + ")" if k.startswith("x-archive-meta") else v
    for attempt in range(4):
        with open(path, "rb") as f:
            req = urllib.request.Request(f"https://s3.us.archive.org/{ident}/{path.name}", data=f, method="PUT", headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=900) as r:
                    r.read()
                return
            except urllib.error.HTTPError as e:
                # 503 SlowDown: the Archive asks uploaders to wait and try again.
                if e.code not in (500, 502, 503, 504) or attempt == 3:
                    raise
        time.sleep(60 * (attempt + 1))


def ia_upload(game, title, desc, video, poster):
    ident = f"evergamer-{game}-{time.strftime('%Y%m%d-%H%M', time.gmtime())}"[:100]
    meta = {"x-amz-auto-make-bucket": "1", "x-archive-meta-mediatype": "movies",
            "x-archive-meta-collection": "opensource_movies", "x-archive-meta-title": title,
            "x-archive-meta-description": desc, "x-archive-meta-subject": "EverGamer;game showcase;video game",
            "x-archive-meta-creator": "EverGamer showcase bot", "x-archive-meta-language": "eng"}
    ia_put(ident, video, "video/mp4", meta)
    ia_put(ident, poster, "image/jpeg", {})
    # The item is ready once the Archive lists the file: wait for it (up to 20 minutes), so the hub never shows a
    # video that does not play yet.
    for _ in range(40):
        try:
            names = {f.get("name") for f in public_json(f"https://archive.org/metadata/{ident}").get("files", [])}
            if video.name in names:
                break
        except Exception:
            pass
        time.sleep(30)
    base = f"https://archive.org/download/{ident}/"
    return base + video.name, base + poster.name, f"https://archive.org/details/{ident}"


# ---- one game --------------------------------------------------------------------------------------------------

def run_game(pw, job):
    game, s = job["game"], job["settings"]
    if isinstance(s, str):
        s = json.loads(s)
    t_start = time.time()
    version, notes, built = read_updates(s)
    if not version or version == job.get("lastVersion"):
        log(f"{game}: no update")
        return False
    log(f"{game}: update {version[:24]}")
    fresh = new_notes(game, notes)
    plan = pick(areas_of(game, s), fresh)
    log(f"{game}: showing " + ", ".join(f"{a.get('name', '?')} ({secs}s)" for a, secs in plan))
    work = OUT / game
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    clips = []
    for n, (area, secs) in enumerate(plan):
        clip, start, crop = record_segment(pw, s, area, secs, n, work)
        clips.append((clip, start, secs, crop))
    name = game_title(game)
    when = (built or time.strftime("%Y-%m-%d"))[:10]
    shown = ", ".join(a.get("name", "") for a, _ in plan if a.get("name"))
    title = f"{name}: update of {when}" + (f" ({shown})" if shown else "")
    t_enc = time.time()
    video, poster, dur = make_video(clips, work, title, s.get("sound") or "off")
    enc_s = time.time() - t_enc
    for c, *_ in clips:
        c.unlink(missing_ok=True)
    size = video.stat().st_size
    log(f"{game}: video {dur}s, {size / 1e6:.1f} MB, encoded in {enc_s:.0f}s")
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    # The page shows these under the video too: the day it was recorded, and whether the sound was left out.
    stats = {"recorded": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "sound": s.get("sound") or "off", "recordSeconds": round(t_enc - t_start), "encodeSeconds": round(enc_s), "mb": round(size / 1e6, 1),
             "cpuSeconds": round(ru.ru_utime + ru.ru_stime), "peakMemMB": round(ru.ru_maxrss / 1024),
             "load": [round(x, 2) for x in os.getloadavg()]}
    if DRY:
        log(f"{game}: dry run, video kept at {video}; stats {json.dumps(stats)}")
        return True
    desc = (f"An automatic video of the latest build of {name}, recorded by the EverGamer showcase bot.\n\n"
            + ("What changed:\n" + "\n".join("- " + n for n in fresh) + "\n\n" if fresh else "")
            + f"More: {SITE}/game/{game}\n\nA fan project: not affiliated with or endorsed by the owners of the games it is based on.")
    t_up = time.time()
    fake = os.environ.get("SHOWCASE_FAKE_UPLOAD")  # testing: "<folder>|<its web address>" instead of the Archive
    if fake:
        folder, base = fake.split("|", 1)
        Path(folder).mkdir(parents=True, exist_ok=True)
        for f in (video, poster):
            shutil.copy(f, Path(folder) / f.name)
        url, poster_url, page = f"{base}/{video.name}", f"{base}/{poster.name}", ""
    else:
        url, poster_url, page = ia_upload(game, title, desc, video, poster)
    stats["uploadSeconds"] = round(time.time() - t_up)
    stats["uploadMB"] = round((size + poster.stat().st_size) / 1e6, 1)
    # Uploaded: our copy goes (LostMystic: "deleting it off their own server after it's uploaded").
    shutil.rmtree(work, ignore_errors=True)
    hub("/hub/showcase/done", {"game": game, "version": version, "video": {
        "title": title, "notes": fresh, "url": url, "poster": poster_url, "page": page, "seconds": dur, "stats": stats}})
    log(f"{game}: published; {json.dumps(stats)}")
    return True


def main():
    if os.environ.get("SHOWCASE_JOBS"):
        jobs = json.loads(Path(os.environ["SHOWCASE_JOBS"]).read_text())["jobs"]
    else:
        jobs = hub("/hub/showcase/jobs")["jobs"]
    only = os.environ.get("SHOWCASE_ONLY")
    if only:
        jobs = [j for j in jobs if j["game"] == only]
    log(f"{len(jobs)} game(s) with automatic videos")
    if not jobs:
        return 0
    if not DRY and not os.environ.get("SHOWCASE_FAKE_UPLOAD") and not (os.environ.get("IA_ACCESS") and os.environ.get("IA_SECRET")):
        log("no Internet Archive keys yet (repository secrets IA_ACCESS and IA_SECRET): nothing is recorded until they are set")
        return 0
    if os.environ.get("SHOWCASE_CHECK"):  # the hourly look: is there anything to record? (no browser needed)
        work = False
        for job in jobs:
            st = job["settings"] if isinstance(job["settings"], dict) else json.loads(job["settings"])
            try:
                version = read_updates(st)[0]
            except Exception as e:
                log(f"{job.get('game')}: could not read its updates ({type(e).__name__})")
                continue
            if version and version != job.get("lastVersion"):
                log(f"{job.get('game')}: update {version[:24]}")
                work = True
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                f.write(f"work={'1' if work else '0'}\n")
        log("something to record" if work else "nothing new")
        return 0
    from playwright.sync_api import sync_playwright
    made, failed = 0, 0
    with sync_playwright() as pw:
        for job in jobs:  # one at a time: one browser, one encoder, one upload
            if made >= MAX_GAMES:
                log("enough for one run; the rest wait for the next")
                break
            try:
                made += run_game(pw, job)
            except Exception as e:
                failed += 1
                # The error's kind only: its words could hold the game's address. Known causes are named, so the
                # maker can tell a missing login from a broken game without the log saying where it is played.
                why = str(e)
                hint = next((h for k, h in [
                    ("ERR_INVALID_AUTH_CREDENTIALS", "the game asks for a login: save the play-test login in the panel"),
                    ("401", "the game asks for a login: save the play-test login in the panel"),
                    ("ERR_NAME_NOT_RESOLVED", "its play address does not exist"),
                    ("ERR_CONNECTION", "its server did not answer"),
                    ("Timeout", "the game took too long to load")] if k in why), "")
                log(f"{job.get('game')}: failed ({type(e).__name__}{': ' + hint if hint else ''}); it is tried again next run")
                log("  where: " + redact(traceback.format_exc(limit=6), job))
                if os.environ.get("SHOWCASE_DEBUG"):  # local testing only: the details can name the game's address
                    traceback.print_exc()
    if not DRY:
        shutil.rmtree(OUT, ignore_errors=True)
    for p in _x11.values():
        p.terminate()
    return 1 if failed and not made else 0


if __name__ == "__main__":
    sys.exit(main())
