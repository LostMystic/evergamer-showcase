# EverGamer showcase bot

A new video of a game each time it is updated, on its page at [evergamer.com](https://evergamer.com).

Every hour (`.github/workflows/showcase.yml`) the bot asks the EverGamer hub which games have automatic videos
switched on. For each one, one at a time, it reads the game's updates file, and when the version changed it opens
the game in Chrome, goes to the parts of the game the new notes talk about, plays for 2 to 5 minutes, records it,
(The hourly look takes a few seconds and installs nothing; the recorder is set up only when a game was updated.)
uploads the video to the [Internet Archive](https://archive.org), deletes its own copy and tells the hub. The game's
page then shows the newest video at the top with what changed, and the older ones under it.

Makers switch it on from their game's page on EverGamer (signed in): where the game is played, its updates file,
a login if the build has one, the sound (off unless they choose otherwise), and optionally the areas to show.

## The updates file

JSON that the game's build publishes beside it:

```json
{ "version": "1.4.2", "built": "2026-10-30 22:07", "notes": ["2026-10-30: Siege towers.", "2026-10-29: Night battles."] }
```

`notes` is a list, newest first (or text, one change per line). Without an updates file the bot watches the game's
page and its scripts for changes, and the videos cannot say what is new.

## Areas

Where the bot goes for each kind of update. A maker sets them on their game's page; `areas/<game id>.json` here
holds the defaults for games that have none set there.

| Field | What |
| --- | --- |
| `name` | Shown in the video's title. |
| `match` | Words, comma between, looked for in the new notes. An area with no words is the default. |
| `query` | Added to the game's address (start options). |
| `steps` | What the bot presses: `wait 2`, `key ArrowDown`, `key Enter`, `down KeyW` / `up KeyW`, `click 640 360` (on a 1280x720 page), `type Ada`. |
| `script` | Or the game's own test script, handed to it at the address `{script}` stands for in `query`. |
| `seconds` | How long it plays (20 to 300). All areas together make 2 to 5 minutes. |
| `explore` | Then press keys and click about, from `exploreAfter` seconds on. |
| `settle` | Seconds to wait after the page has loaded. |

## Secrets

| Secret | What |
| --- | --- |
| `SHOWCASE_TOKEN` | The hub's key for this bot (its SHA-256 is in the hub's `server/gamehub_showcase.go`). |
| `IA_ACCESS`, `IA_SECRET` | Internet Archive upload keys, from https://archive.org/account/s3.php (a free account). |

Nothing the bot prints names a game's address or login: the logs of a public repository are public.

## Testing

`SHOWCASE_DRY=1 SHOWCASE_JOBS=jobs.json python showcase.py` records without uploading or reporting and keeps the
video in `out/`. `SHOWCASE_MIN=20` allows shorter videos.

## Resources

One browser, one encoder and one upload at a time; ffmpeg runs at low priority on two threads. A 4½ minute video
took about 5 minutes to record, 40 seconds to encode, 2 minutes of processor time and 256 MB of memory at most, and
came to about 50 MB. Each video's numbers are kept with it on the hub (its maker sees them).

Videos are of fan projects. Not affiliated with or endorsed by the owners of the games they are based on.
