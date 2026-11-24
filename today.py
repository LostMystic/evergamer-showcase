#!/usr/bin/env python3
"""EverGamer "New today": once a day, a game on the front page with fresh screenshots and what changed.

LostMystic, 24 November 2026: "every day the front page at least looks a little bit different and it's featuring
something else or an update ... capturing new screenshots and new screens ... I want that completely automated."

Each run (.github/workflows/today.yml, early every morning UTC, again later in case the first failed):
  1. asks the hub which days were featured lately (GET /hub/today); stops if today is done;
  2. lists the games that play in a browser (games posted on the hub and the hub's own listings), with their newest
     notes: the devlog, the patch notes the hub read from the game's updates file, or that file itself;
  3. picks one: the game with the newest notes it has not been featured for since, else the one featured longest
     ago (never the same game two days running while another is available);
  4. opens it headless (never the browser's bars: only the page is captured), where the notes point when the maker
     set areas for its automatic videos, else as it opens, plays a little and takes screenshots; keeps the 4 most
     different ones that are not blank;
  5. puts them on the Internet Archive, then tells the hub (POST /hub/today/done). If the Archive will not take them
     the day is still reported, and the page shows the game's own pictures.

Environment: the same as showcase.py (SHOWCASE_TOKEN, IA_ACCESS, IA_SECRET, HUB_API), and
  TODAY_DRY=1          capture, but do not upload or report; the pictures stay in out/today/
  TODAY_GAME=<id>      feature this game (testing, or by hand from the workflow)
  TODAY_FORCE=1        run even when today is already featured
  TODAY_SUMMARY=file, TODAY_PROJECTS=file   read these instead of the hub (testing)

Nothing it prints names a game's address or login: the logs of a public repository are public.
"""

import datetime
import json
import os
import random
import re
import shutil
import sys
import time
import traceback
import urllib.parse
from pathlib import Path

import showcase as sc
from showcase import API, SITE, W, H, log, hub, public_json

DRY = os.environ.get("TODAY_DRY") == "1"
OUT = sc.HERE / "out" / "today"
TODAY = datetime.datetime.now(datetime.timezone.utc).date()
WINDOW = 10      # days: notes older than this are not "new"
SHOTS = 4        # pictures shown
TRIES = 10       # pictures taken to choose them from
DATE_RE = re.compile(r"^\s*(20\d\d-\d\d-\d\d)\s*[:\-]\s*(.+)$", re.S)


# ---- what there is ---------------------------------------------------------------------------------------------

def read_file_or(env, url):
    f = os.environ.get(env)
    return json.loads(Path(f).read_text()) if f else public_json(url)


def absolute(play):
    if not play:
        return ""
    if play.startswith("/"):
        return SITE + play
    return play if re.match(r"^https?://", play) else ""


def day_of(s):
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def short(text, n=220):
    """A note as the page shows it: its first sentence or two, at most n characters."""
    t = re.sub(r"\s+", " ", str(text)).strip()
    if len(t) <= n:
        return t
    cut = t[:n]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:end + 1] if end > 60 else cut.rsplit(" ", 1)[0] + "..."


def games():
    """Every game that plays in a browser: {id, title, play, stage, notes: [(date, text)], mine}."""
    summary = read_file_or("TODAY_SUMMARY", f"{API}/hub/summary")
    edits = summary.get("edits") or {}
    out = {}
    for g in summary.get("games") or []:
        d, e = g.get("data") or {}, edits.get(g["id"]) or {}
        if e.get("archived"):
            continue
        out[g["id"]] = {"id": g["id"], "title": d.get("title") or g["id"], "stage": e.get("stage") or d.get("stage"),
                        "play": absolute((d.get("links") or {}).get("play")), "devlog": d.get("devlog") or [], "mine": True}
    try:
        projects = read_file_or("TODAY_PROJECTS", f"{SITE}/test/data/projects.json")
    except Exception as e:
        log(f"the hub's own listings could not be read ({type(e).__name__})")
        projects = []
    for p in projects:
        if p["id"] in out or (edits.get(p["id"]) or {}).get("archived"):
            continue  # a saved copy of a listing replaces it (hub.js)
        out[p["id"]] = {"id": p["id"], "title": p.get("title") or p["id"], "stage": (edits.get(p["id"]) or {}).get("stage") or p.get("stage"),
                        "play": absolute((p.get("links") or {}).get("play")), "devlog": p.get("devlog") or [],
                        "mine": not p["id"].startswith("eg-")}  # eg-*: George's games, which seldom change
    # Mods never get a play link on the hub (docs/CONTENT-RULES.md), and a game with no address cannot be captured.
    return [g for g in out.values() if g["play"] and g["stage"] != "mod"]


def notes_of(game, jobs):
    """The game's notes, newest first, as (date, text): its devlog, the patch notes the hub read from its updates
    file (its own, or those of a listing played at the same address: Heart of Adventure and the Fantasy Empires
    remake are one build), else that file read here, dated by its build."""
    notes = []
    for e in game["devlog"]:
        d = day_of(e.get("date"))
        if d and (e.get("text") or e.get("title")):
            notes.append((d, e.get("title") or e.get("text")))
    sources = [game["id"]] + [j["game"] for j in jobs if j["game"] != game["id"] and same_place(j["settings"].get("play"), game["play"])]
    video = None
    for gid in sources:
        try:
            s = public_json(f"{API}/hub/showcase?game={gid}")
        except Exception:
            continue
        for e in s.get("devlog") or []:
            d = day_of(e.get("date"))
            if d and e.get("text"):
                notes.append((d, e["text"]))
        v = (s.get("videos") or [None])[0]
        if v and not video and day_of(v.get("created")) and (TODAY - day_of(v["created"])).days <= 7:
            video = v.get("page") or v.get("url")
    if not any(d for d, _ in notes):
        st = settings_for(game, jobs)
        try:
            d = json.loads(sc.fetch(urllib.parse.urljoin(game["play"], "version.json"), st))
            built = day_of(d.get("built")) or TODAY
            raw = d.get("notes") or []
            raw = raw.splitlines() if isinstance(raw, str) else raw
            for n in raw[:6]:
                m = DATE_RE.match(str(n))
                notes.append((day_of(m.group(1)), m.group(2)) if m else (built, str(n)))
        except Exception:
            pass  # most games have no updates file
    seen, uniq = set(), []
    for d, t in sorted(notes, key=lambda x: x[0], reverse=True):
        k = short(t, 80)
        if k not in seen:
            seen.add(k)
            uniq.append((d, t))
    return uniq, video


def same_place(a, b):
    """Two play addresses for one build: the same host and folder, whatever the query or index.html."""
    def norm(u):
        p = urllib.parse.urlsplit(u or "")
        path = re.sub(r"(index\.html?)?$", "", p.path or "/")
        return (p.hostname or "", path.rstrip("/"))
    return bool(a and b) and norm(a) == norm(b)


def settings_for(game, jobs):
    """The game's automatic-video settings (login, areas), or a listing's played at the same address."""
    for j in jobs:
        if j["game"] == game["id"]:
            return j["settings"]
    for j in jobs:
        if same_place(j["settings"].get("play"), game["play"]):
            return {**j["settings"], "play": game["play"], "_from": j["game"]}
    return {"play": game["play"]}


# ---- which game ------------------------------------------------------------------------------------------------

def choose(cands, recent):
    """(game, notes, why). New notes first: the game whose newest note is newest and came after the last day it was
    featured. Else the one featured longest ago, the hub's own makers' games first. Never yesterday's game again
    while another is available."""
    last = {}
    for r in recent:
        d = day_of(r["day"])
        if d and r["game"] not in last:
            last[r["game"]] = d
    yesterday = recent[0]["game"] if recent and day_of(recent[0]["day"]) == TODAY - datetime.timedelta(days=1) else None
    rng = random.Random(TODAY.isoformat())
    fresh = []
    for g, notes, video in cands:
        new = [(d, t) for d, t in notes if (TODAY - d).days <= WINDOW and (g["id"] not in last or d > last[g["id"]])]
        if new:
            fresh.append((new[0][0], g, new, video))
    fresh.sort(key=lambda x: (x[0], x[1]["mine"], rng.random()), reverse=True)
    for _, g, new, video in fresh:
        if g["id"] != yesterday or len(cands) == 1:
            return g, new, video, "update"
    order = sorted(cands, key=lambda c: (last.get(c[0]["id"], datetime.date.min), not c[0]["mine"], rng.random()))
    for g, notes, video in order:
        if g["id"] != yesterday or len(cands) == 1:
            return g, notes[:3], video, "rotation"
    return None


# ---- the pictures ----------------------------------------------------------------------------------------------

def pick_area(game, s, notes):
    areas = sc.areas_of(s.get("_from") or game["id"], s)
    if not areas:
        return {"name": "The game", "query": "", "explore": True}
    plan = sc.pick(areas, [t for _, t in notes])
    # A different place each day when the notes point nowhere in particular.
    matched = [a for a, _ in plan if (a.get("match") or "").strip()]
    return random.choice(matched or areas)


GENTLE_KEYS = ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "ArrowRight", "ArrowUp", "Space", "KeyW", "KeyD"]


def gentle(page, seconds):
    """A game the maker set no areas for: hold movement keys, never click about (a random click found a menu's Exit
    and closed Heart of Adventure, 24 November 2026)."""
    end = time.time() + seconds
    while time.time() < end:
        k = random.choice(GENTLE_KEYS)
        try:
            page.keyboard.down(k)
            page.wait_for_timeout(random.randint(300, 1400))
            page.keyboard.up(k)
            page.wait_for_timeout(random.randint(200, 700))
        except Exception:
            page.wait_for_timeout(500)


def capture(pw, game, s, area, work):
    """Screenshots of the page only (never the browser's bars): of the game's canvas when it is drawn smaller than
    the page. Answers the files taken, in order."""
    args = ["--autoplay-policy=no-user-gesture-required", "--disable-dev-shm-usage", "--use-angle=swiftshader",
            "--enable-unsafe-swiftshader", "--mute-audio"]
    browser = pw.chromium.launch(channel="chrome", headless=True, args=args)
    ctx = browser.new_context(viewport={"width": W, "height": H}, ignore_https_errors=True,
                              http_credentials={"username": s["user"], "password": s.get("pass", "")} if s.get("user") else None)
    script_name = "__showcase/0.txt"
    if area.get("script"):
        ctx.route("**/__showcase/*", lambda route: route.fulfill(status=200, content_type="text/plain", body=area["script"]))
    page = ctx.new_page()
    page.goto(sc.area_url(s["play"], area.get("query"), script_name, sc.area_level(area)), wait_until="load", timeout=120000)
    try:
        page.wait_for_load_state("networkidle", timeout=90000)
    except Exception:
        pass
    page.wait_for_timeout(int(area.get("settle", 3)) * 1000)
    own = bool(area.get("steps") or area.get("query"))
    if not own:
        # A page that waits for a click to start ("Click to play"): one click in the middle of the game's picture.
        page.mouse.click(W // 2, H // 2)
        page.wait_for_timeout(6000)
    sc.run_steps(page, area.get("steps"))
    box = page.evaluate("""() => { let b = null; for (const e of document.querySelectorAll('canvas, video')) {
        const r = e.getBoundingClientRect(); if (r.width * r.height > (b ? b.width * b.height : 0)) b = r }
        return b && { x: Math.max(0, b.x), y: Math.max(0, b.y), width: Math.min(b.width, innerWidth), height: Math.min(b.height, innerHeight) } }""")
    clip = box if box and box["width"] >= 320 and box["height"] >= 200 and box["width"] * box["height"] < 0.9 * W * H else None
    files = []
    for i in range(TRIES):
        f = work / f"raw{i}.png"
        page.screenshot(path=str(f), clip=clip) if clip else page.screenshot(path=str(f))
        files.append(f)
        if own and area.get("explore"):
            sc.explore(page, random.uniform(4, 8))
        elif not own:
            gentle(page, random.uniform(4, 8))
        else:
            page.wait_for_timeout(random.randint(4000, 8000))
    ctx.close()
    browser.close()
    return files


def choose_shots(files, work):
    """The SHOTS most different pictures that are not blank or a loading screen, as JPEGs of at most 1280 wide."""
    from PIL import Image, ImageChops, ImageStat
    good = []
    for f in files:
        im = Image.open(f).convert("RGB")
        st = ImageStat.Stat(im.convert("L"))
        if st.stddev[0] < 12:  # one color, nearly: a blank or loading screen
            continue
        good.append((f, im, im.resize((64, 36))))
    chosen = []
    while good and len(chosen) < SHOTS:
        def dist(x):
            if not chosen:
                return ImageStat.Stat(x[2].convert("L")).stddev[0]
            return min(sum(ImageStat.Stat(ImageChops.difference(x[2], c[2])).mean) for c in chosen)
        best = max(good, key=dist)
        if chosen and dist(best) < 6:  # the rest look the same
            break
        chosen.append(best)
        good.remove(best)
    chosen.sort(key=lambda x: files.index(x[0]))  # in the order they were taken
    out = []
    for n, (_, im, _) in enumerate(chosen, 1):
        if im.width > 1280:
            im = im.resize((1280, round(im.height * 1280 / im.width)))
        p = work / f"shot{n}.jpg"
        im.save(p, "JPEG", quality=84, optimize=True, progressive=True)
        out.append(p)
    return out


def ia_pictures(game, title, desc, shots):
    ident = f"evergamer-today-{game}-{TODAY.strftime('%Y%m%d')}"[:100]
    meta = {"x-amz-auto-make-bucket": "1", "x-archive-meta-mediatype": "image",
            "x-archive-meta-collection": "opensource_image", "x-archive-meta-title": title,
            "x-archive-meta-description": desc, "x-archive-meta-subject": "EverGamer;screenshot;video game",
            "x-archive-meta-creator": "EverGamer showcase bot", "x-archive-meta-language": "eng"}
    for n, f in enumerate(shots):
        sc.ia_put(ident, f, "image/jpeg", meta if n == 0 else {})
    for _ in range(40):  # ready once the Archive lists the files (up to 20 minutes)
        try:
            names = {f.get("name") for f in public_json(f"https://archive.org/metadata/{ident}").get("files", [])}
            if all(f.name in names for f in shots):
                break
        except Exception:
            pass
        time.sleep(30)
    base = f"https://archive.org/download/{ident}/"
    return [base + f.name for f in shots], f"https://archive.org/details/{ident}"


# ---- the day ---------------------------------------------------------------------------------------------------

def main():
    try:
        recent = public_json(f"{API}/hub/today?days=30").get("recent") or []
    except Exception as e:
        if not DRY:
            log(f"the hub did not answer ({type(e).__name__}); tried again next run")
            return 1
        recent = []
    if recent and recent[0]["day"] == TODAY.isoformat() and not os.environ.get("TODAY_FORCE") and not DRY:
        log(f"today ({TODAY}) is already featured: {recent[0]['game']}")
        return 0
    jobs = []
    if os.environ.get("SHOWCASE_JOBS"):  # testing: the jobs from a file
        jobs = json.loads(Path(os.environ["SHOWCASE_JOBS"]).read_text())["jobs"]
    elif os.environ.get("SHOWCASE_TOKEN"):
        try:
            jobs = hub("/hub/showcase/jobs")["jobs"]
        except Exception as e:
            log(f"no automatic-video settings ({type(e).__name__}): games open as they start")
    for j in jobs:
        if isinstance(j["settings"], str):
            j["settings"] = json.loads(j["settings"])
    cands = []
    for g in games():
        notes, video = notes_of(g, jobs)
        cands.append((g, notes, video))
    log(f"{len(cands)} game(s) that play in a browser; {sum(1 for c in cands if c[1])} with notes")
    only = os.environ.get("TODAY_GAME")
    if only:
        cands = [c for c in cands if c[0]["id"] == only]
        if not cands:
            log(f"{only}: not a game that plays in a browser")
            return 1
    picked = choose(cands, recent)
    if not picked:
        log("nothing to feature")
        return 0
    game, notes, video, why = picked
    log(f"today: {game['id']} ({why}; {len(notes)} note(s))")
    s = settings_for(game, jobs)
    log(f"  {len(jobs)} game(s) with automatic-video settings; " + (f"using those of {s['_from']}" if s.get("_from") else
        "its own" if any(j["game"] == game["id"] for j in jobs) else "none for this game"))
    if not s.get("_from") and os.environ.get("TODAY_DEBUG"):
        gp = urllib.parse.urlsplit(game["play"])
        for j in jobs:  # paths only, never a host (the log is public)
            jp = urllib.parse.urlsplit(j["settings"].get("play") or "")
            log(f"    {j['game']}: same host {jp.hostname == gp.hostname}, path {jp.path!r} (this game {gp.path!r}), query {bool(jp.query)}, areas {len(j['settings'].get('areas') or [])}")
    area = pick_area(game, s, notes)
    log(f"  showing: {area.get('name', 'the game')}")
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    shots = []
    from playwright.sync_api import sync_playwright
    for attempt in range(2):
        try:
            with sync_playwright() as pw:
                shots = choose_shots(capture(pw, game, s, area, OUT), OUT)
            if len(shots) >= 2:
                break
            log(f"  only {len(shots)} usable picture(s); trying once more" if attempt == 0 else "  still too few pictures")
        except Exception as e:
            log(f"  capture failed ({type(e).__name__})")
            if os.environ.get("SHOWCASE_DEBUG"):
                traceback.print_exc()
        area = pick_area(game, s, [])  # maybe another place does better
    log(f"  {len(shots)} picture(s): " + ", ".join(f"{p.stat().st_size // 1024} KB" for p in shots))
    title = f"{game['title']}: {TODAY.strftime('%B')} {TODAY.day}, {TODAY.year}"
    body = {"day": TODAY.isoformat(), "game": game["id"], "title": game["title"], "why": why,
            "notes": [{"date": d.isoformat(), "text": short(t)} for d, t in notes[:4]], "shots": [], "page": "",
            "video": video or ""}
    if DRY:
        log(f"dry run: pictures in {OUT}; would report {json.dumps({**body, 'notes': len(body['notes'])})}")
        return 0
    if shots and os.environ.get("IA_ACCESS") and os.environ.get("IA_SECRET"):
        desc = (f"Screenshots of {game['title']} taken on {TODAY.isoformat()} by the EverGamer showcase bot, featured on "
                f"the front page of {SITE}.\n\nMore: {SITE}/game/{game['id']}\n\nA fan project: not affiliated with or "
                "endorsed by the owners of the games it is inspired by.")
        try:
            ok, why_not = sc.ia_room(f"evergamer-today-{game['id']}")
            if not ok:
                raise RuntimeError("the Archive is not taking uploads now: " + why_not)
            body["shots"], body["page"] = ia_pictures(game["id"], title, desc, shots)
        except Exception as e:
            log(f"  the pictures could not be put on the Archive ({type(e).__name__}): the page shows the game's own")
    hub("/hub/today/done", body)
    shutil.rmtree(OUT, ignore_errors=True)
    log(f"featured {game['id']} for {TODAY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
