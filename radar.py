#!/usr/bin/env python3
"""EverGamer radar: every night, the remakes, recompilations, source ports and AI-made games out there, and which are new.

LostMystic, 27 November 2026: "taking the difference between all the games that it found on GitHub the day before and
our site too ... and the difference between that and today ... a button ... to check out the raw list ... with
hyperlinks to the appropriate pages ... when the project was last updated ... a simple text search".

Sources (more can be added: each is a function returning records):
  awesome    SubmitGame/awesome-decomp-games (its data/games.json, refreshed daily by its makers)
  github     GitHub searches for AI-made games, recompilations, decompilations, source ports and fan remakes
  evergamer  EverGamer's own listings (existing games, the hub's own and posted games)
Each GitHub project is then read in batches of 100 (GraphQL): stars, size, created, last push, latest release.

Writes radar/ (committed by .github/workflows/radar.yml; the hub reads it from this public repository):
  seen.json     {key: first day seen}: what was known before today
  list.json     every project: {k, n, u, d, s (stars), z (size KB), c (created), p (last update), f (first seen),
                src, t (kind), pl (platform), ai, eg (its EverGamer page)}
  summary.json  {date, total, new, top: the best new ones, sources, history}

Uses the `gh` command (GH_TOKEN in Actions). Nothing it prints is secret.
"""

import datetime
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent / 'radar'
TODAY = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
UA = {'User-Agent': 'EverGamerRadar/1.0 (+https://evergamer.com)'}
GAME = re.compile(r'\b(game|games|rpg|roguelike|platformer|shooter|puzzle|arcade|dungeon|strategy|tower defense|racing|racer|'
                  r'metroidvania|survival|clicker|idle|retro|8-bit|16-bit|pixel|jrpg|fps|rts|adventure|sandbox|voxel|kart)\b', re.I)
NOT_GAME = re.compile(r'\b(template|framework|toolkit|mcp|skill|plugin|library|sdk|boilerplate|starter|prompt|workflow|'
                      r'benchmark|awesome|dotfiles|config|emulator|launcher|mod manager|extension)\b', re.I)


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def gh(args, data=None):
    r = subprocess.run(['gh', 'api', *args], input=data, capture_output=True, text=True, timeout=120)
    if r.returncode:
        # GraphQL answers what it found even when one repository in the batch is gone: keep that part.
        if r.stdout.strip().startswith('{'):
            return json.loads(r.stdout)
        raise RuntimeError(r.stderr.strip()[:300])
    return json.loads(r.stdout or 'null')


def get_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return json.load(r)


def repo_of(url):
    m = re.match(r'https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)', url or '')
    return f'{m.group(1)}/{m.group(2).removesuffix(".git")}'.lower() if m else ''


def rec(key, name, url, src, **kw):
    return {'k': key, 'n': name, 'u': url, 'src': [src], **{k: v for k, v in kw.items() if v not in (None, '', [], False)}}


# ---- sources ---------------------------------------------------------------------------------------------------

KIND = {'static-recomp': 'recompilation', 'decompilation': 'decompilation', 'disassembly': 'disassembly',
        'source-port': 'source port', 'engine-remake': 'engine remake', 'browser-port': 'browser port', 'mod': 'mod',
        'tool': 'tool'}


def awesome():
    d = get_json('https://raw.githubusercontent.com/SubmitGame/awesome-decomp-games/main/data/games.json')
    out = []
    for g in d.get('games', []) + d.get('mods', []):
        if g.get('type') == 'tool':
            continue
        url = g.get('url') or ''
        key = repo_of(url) or url.rstrip('/').lower()
        out.append(rec(key, g.get('title') or key, url, 'awesome-decomp-games', d=(g.get('description') or '')[:160],
                       s=g.get('stars'), p=(g.get('release_date') or g.get('last_commit') or '')[:10],
                       t=KIND.get(g.get('type'), g.get('type')), pl=g.get('platform'), ai=bool(g.get('ai_flag'))))
    return out


# GitHub searches, newest first. "game" ones keep only what reads as a game; the rest are remakes by their kind.
SEARCHES = [
    ('topic:vibe-coded', 'AI-made game', True), ('topic:vibe-coding topic:game', 'AI-made game', True),
    ('topic:claude-code topic:game', 'AI-made game', True), ('topic:ai-generated topic:game', 'AI-made game', True),
    ('"made with claude code" game in:readme', 'AI-made game', True), ('"vibe coded" game in:readme', 'AI-made game', True),
    ('"built with claude code" game in:readme', 'AI-made game', True), ('topic:vibejam', 'AI-made game', True),
    ('topic:static-recompilation', 'recompilation', False), ('topic:recompilation game', 'recompilation', False),
    ('topic:decompilation game', 'decompilation', False), ('recompiled in:name game', 'recompilation', False),
    ('topic:source-port', 'source port', False), ('topic:game-remake', 'remake', False), ('"fan remake" in:readme', 'remake', False),
    ('topic:reimplementation game', 'engine remake', False),
]


def github_search():
    since = (datetime.date.fromisoformat(TODAY) - datetime.timedelta(days=45)).isoformat()
    out = {}
    for q, kind, is_ai in SEARCHES:
        for page in (1, 2):
            try:
                r = gh(['-X', 'GET', 'search/repositories', '-f', f'q={q} created:>={since}', '-f', 'sort=updated',
                        '-f', 'per_page=100', '-f', f'page={page}'])
            except Exception as e:
                log(f'search failed: {q} ({str(e)[:80]})')
                break
            items = r.get('items') or []
            for it in items:
                desc = it.get('description') or ''
                if is_ai and (not GAME.search(desc + ' ' + it['name']) or NOT_GAME.search(desc)):
                    continue
                key = it['full_name'].lower()
                out.setdefault(key, rec(key, it['name'], it['html_url'], 'GitHub search', d=desc[:160], t=kind, ai=is_ai))
            time.sleep(2.2)  # the search API allows 30 a minute
            if len(items) < 100:
                break
    return list(out.values())


def evergamer():
    out = []
    lists = [('https://evergamer.com/test/data/existing.json', 'existing'), ('https://evergamer.com/test/data/projects.json', 'own')]
    for url, _ in lists:
        try:
            for g in get_json(url):
                if g.get('archived'):
                    continue
                link = next((v for v in (g.get('links') or {}).values() if isinstance(v, str) and 'github.com/' in v), '')
                key = repo_of(link) or 'evergamer:' + g['id']
                out.append(rec(key, g.get('title') or g['id'], link or f"https://evergamer.com/game/{g['id']}", 'EverGamer',
                               d=(g.get('tagline') or '')[:160], t=(g.get('kindLabel') or g.get('stage') or '').lower(),
                               eg=f"https://evergamer.com/game/{g['id']}"))
        except Exception as e:
            log(f'EverGamer list {url}: {type(e).__name__}')
    try:
        s = get_json('https://evergamer-gw-ams.fly.dev/hub/summary')
        for g in s.get('games') or []:
            d = g.get('data') or {}
            link = next((v for v in (d.get('links') or {}).values() if isinstance(v, str) and 'github.com/' in v), '')
            key = repo_of(link) or 'evergamer:' + g['id']
            out.append(rec(key, d.get('title') or g['id'], link or f"https://evergamer.com/game/{g['id']}", 'EverGamer',
                           d=(d.get('tagline') or '')[:160], t='posted on EverGamer', eg=f"https://evergamer.com/game/{g['id']}",
                           c=(g.get('created') or '')[:10]))
    except Exception as e:
        log(f'EverGamer posted games: {type(e).__name__}')
    return out


SOURCES = [awesome, github_search, evergamer]


# ---- GitHub details ---------------------------------------------------------------------------------------------

def enrich(records):
    """Stars, size, created, last push and latest release of every GitHub project, 100 a request."""
    repos = sorted({r['k'] for r in records if '/' in r['k'] and not r['k'].startswith(('http', 'evergamer:'))})
    info = {}
    for i in range(0, len(repos), 100):
        part = repos[i:i + 100]
        q = 'query{' + ''.join(
            f'r{j}:repository(owner:{json.dumps(o)},name:{json.dumps(n)}){{nameWithOwner stargazerCount diskUsage createdAt pushedAt '
            f'isArchived description latestRelease{{publishedAt}}}} ' for j, (o, n) in enumerate(x.split('/', 1) for x in part)) + '}'
        try:
            d = gh(['graphql', '-f', f'query={q}'])
        except Exception as e:
            log(f'details batch {i // 100}: {str(e)[:80]}')
            d = {}
        for j, x in enumerate(part):
            v = ((d or {}).get('data') or {}).get(f'r{j}')
            if v:
                info[x] = v
        time.sleep(0.5)
    for r in records:
        v = info.get(r['k'])
        if not v:
            continue
        rel = ((v.get('latestRelease') or {}).get('publishedAt') or '')[:10]
        r.update(s=v['stargazerCount'], z=v['diskUsage'], c=v['createdAt'][:10],
                 p=max(rel, (v.get('pushedAt') or '')[:10]))
        if v.get('isArchived'):
            r['arch'] = True
        if not r.get('d') and v.get('description'):
            r['d'] = v['description'][:160]
    log(f'GitHub details for {len(info)} of {len(repos)} projects')


# ---- what may be highlighted -------------------------------------------------------------------------------------
# The raw list shows everything found. The front page counts and features only what EverGamer's rules allow
# (docs/CONTENT-RULES.md): no forks or tools, and no copies or ports of modern or big-company games.
JUNK = re.compile(r"\bfork\b|v[i1]ctory|cool_fox|\bmirror\b|extractor|unpacker|play (xbox|ps\d|switch|console) games|"
                  r"\brage\b|asset|\brtx\b|compatibility layer|\bdump\b|retail|leak|xbox series|\bps[45]\b|switch 2|steam deck|"
                  r"launcher|emulat|\btool\b|cheat|trainer|save editor|randomizer", re.I)
MODERN = {'Xbox 360', 'Xbox One', 'Xbox Series', 'PS3', 'PS4', 'PS5', 'Switch', 'switch2', 'Switch 2', 'Wii U', '3DS', 'Mobile'}
BIG = re.compile(r"mario|zelda|pok[eé]mon|metroid|kirby|donkey kong|star ?fox|smash|banjo|goldeneye|perfect dark|crash|spyro|"
                 r"uncharted|god of war|bloodborne|colossus|sonic|resident evil|mega ?man|street fighter|metal gear|castlevania|"
                 r"silent hill|final fantasy|kingdom hearts|tomb raider|need for speed|tony hawk|spider-man|call of duty|"
                 r"grand theft|\bgta\b|red dead|halo|diablo|starcraft|warcraft|fallout|elder scrolls|skyrim|assassin|"
                 r"minecraft|fortnite|roblox|persona|yakuza|dark souls|elden ring|astro bot|ratchet|jak\b|gran turismo", re.I)


OLD_PLATFORMS = {'NES', 'SNES', 'Genesis', 'N64', 'PS1', 'Game Boy', 'Game Boy Color', 'Arcade', 'DOS', 'Amiga', 'ZX Spectrum',
                 'BBC Micro/Acorn', 'Commodore 64/8-bit', 'Apple II', 'Saturn', 'Master System/Game Gear', 'Sega CD/32X', 'Dreamcast', 'PC'}
OLD_WORDS = re.compile(r"amiga|\bdos\b|ms-dos|c64|commodore|\bnes\b|snes|genesis|mega drive|\bn64\b|nintendo 64|\bps1\b|psx|"
                       r"playstation 1|game boy|arcade|saturn|dreamcast|zx spectrum|atari|\b19[6-9]\d\b|\b200[01]\b", re.I)


def fine(r):
    text = (r.get('n') or '') + ' ' + (r.get('d') or '')
    if r.get('arch') or JUNK.search(text):
        return False
    copy = (r.get('t') or '') in ('recompilation', 'decompilation', 'disassembly', 'source port', 'browser port', 'mod')
    if copy and (r.get('pl') in MODERN or BIG.search(r.get('n') or '')):
        return False
    # A copy of a game found by search names no platform: only one plainly of an old game (2001 or before) counts.
    if copy and not (r.get('pl') in OLD_PLATFORMS or OLD_WORDS.search(text)):
        return False
    if (r.get('t') or '') == 'AI-made game' and BIG.search(text):
        return False  # a "game" built on someone's famous one
    return True


# ---- the day ----------------------------------------------------------------------------------------------------

def main():
    OUT.mkdir(exist_ok=True)
    merged = {}
    counts = {}
    for fn in SOURCES:
        try:
            got = fn()
        except Exception as e:
            log(f'{fn.__name__} failed: {type(e).__name__}: {str(e)[:120]}')
            got = []
        counts[fn.__name__] = len(got)
        log(f'{fn.__name__}: {len(got)}')
        for r in got:
            m = merged.get(r['k'])
            if not m:
                merged[r['k']] = r
            else:
                m['src'] = sorted(set(m['src']) | set(r['src']))
                for k, v in r.items():
                    if k != 'src' and v and not m.get(k):
                        m[k] = v
                m['ai'] = m.get('ai') or r.get('ai')
    records = list(merged.values())
    enrich(records)
    seen_f = OUT / 'seen.json'
    first_run = not seen_f.exists()
    seen = json.loads(seen_f.read_text()) if not first_run else {}
    yesterday = (datetime.date.fromisoformat(TODAY) - datetime.timedelta(days=1)).isoformat()
    for r in records:
        if r['k'] not in seen:
            # The first night knows nothing yet: only projects created in the last day count as new then.
            seen[r['k']] = TODAY if not first_run or (r.get('c') or '') >= yesterday else 'before'
        r['f'] = seen[r['k']]
    for r in records:
        if fine(r):
            r['ok'] = True
    found_new = [r for r in records if r['f'] == TODAY]
    new = [r for r in found_new if r.get('ok')]
    score = lambda r: (r.get('s') or 0) + (50 if r.get('eg') else 0)
    new.sort(key=lambda r: -score(r))
    records.sort(key=lambda r: (r.get('p') or '', r.get('s') or 0), reverse=True)
    hist_f = OUT / 'summary.json'
    history = json.loads(hist_f.read_text()).get('history', []) if hist_f.exists() else []
    history = [h for h in history if h['date'] != TODAY][-59:] + [{'date': TODAY, 'new': len(new), 'newAll': len(found_new), 'total': len(records)}]
    summary = {'date': TODAY, 'total': len(records), 'new': len(new), 'newAll': len(found_new),
               'newAI': sum(1 for r in new if r.get('ai')), 'newEverGamer': sum(1 for r in new if 'EverGamer' in r['src']),
               'top': [{k: r.get(k) for k in ('n', 'u', 'd', 's', 't', 'ai', 'eg', 'c') if r.get(k) not in (None, '', False)} for r in new[:12]],
               'sources': counts, 'history': history}
    seen_f.write_text(json.dumps(seen, separators=(',', ':'), sort_keys=True))
    (OUT / 'list.json').write_text(json.dumps(records, separators=(',', ':'), ensure_ascii=False))
    hist_f.write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    log(f'{len(records)} projects, {len(found_new)} new today, {len(new)} of them fine to highlight ({summary["newAI"]} AI-made, {summary["newEverGamer"]} on EverGamer)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
