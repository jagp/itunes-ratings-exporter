# itunes-ratings-exporter

Makes your iTunes ratings/plays metadata portable.

Reads `iTunes Music Library.xml` (classic iTunes for Windows) and exports:

| File            | Contents                                                                        |
| --------------- | ------------------------------------------------------------------------------- |
| `tracks.csv`    | Every track: title, artist, album, rating, play count, duration, file path, IDs |
| `rated.csv`     | Only tracks with a rating you set yourself (computed ratings excluded)          |
| `playlists.csv` | Your playlists, one row per track membership                                    |
| `library.json`  | Everything in one structured, versioned file                                    |

## Setup

1. In iTunes: **Edit > Preferences > Advanced**, enable
   **Share iTunes Library XML with other applications**.
2. Python 3.9+ is required. No third-party packages.

## Usage

```
python -m itunes_ratings_exporter [--library PATH] [--out DIR]
```

- `--library` — path to `iTunes Music Library.xml`; defaults to
  `%USERPROFILE%\Music\iTunes\iTunes Music Library.xml`.
- `--out` — output directory (default `./export`).

Exit codes: `0` success, `1` malformed library XML, `2` library file not
found, `3` output directory could not be written (e.g. read-only, invalid
path, or full disk).

## Importing into Spotify

Turn an exported CSV into a Spotify playlist:

```
python -m itunes_ratings_exporter spotify-import [options]
```

### One-time setup

1. Create an app at <https://developer.spotify.com/dashboard>.
2. Add `http://127.0.0.1:8888/callback` to its **Redirect URIs**.
3. Copy the **Client ID** and set it:
   ```
   set SPOTIFY_CLIENT_ID=your-client-id
   ```
   (or pass `--client-id`). No client secret is needed — the CLI uses
   Authorization Code with PKCE, the flow designed for apps that cannot keep
   a secret.

The first run opens your browser for consent. The resulting tokens are cached
in `~/.itunes-ratings-exporter/spotify-token.json`, so later runs are silent.

### Options

| Option           | Default                  | Meaning                                             |
| ---------------- | ------------------------ | --------------------------------------------------- |
| `--csv PATH`     | `export/rated.csv`       | Input CSV                                           |
| `--name NAME`    | `iTunes Ratings <today>` | Playlist name                                       |
| `--min-stars N`  | `4`                      | Only import tracks rated at least N stars           |
| `--limit N`      | all                      | Import at most N tracks — good for a trial run      |
| `--public`       | off                      | Create a public playlist                            |
| `--dry-run`      | off                      | Match and report, but create nothing                |
| `--min-score F`  | `0.72`                   | Match acceptance threshold, `0.0`–`1.0`             |
| `--client-id ID` | `$SPOTIFY_CLIENT_ID`     | Spotify app client ID                               |
| `--restart`      | off                      | Rebuild the work queue from the input CSV           |
| `--rate F`       | `2.0`                    | Requests per second to Spotify; `0` disables pacing |
| `--quiet`        | off                      | Print totals only, not every track                  |

A first run worth trying:

```
python -m itunes_ratings_exporter spotify-import --min-stars 5 --limit 20 --dry-run
```

### Matching

iTunes and Spotify disagree about metadata constantly — remaster suffixes,
where featured artists live, live versions. Each track is scored on title
similarity, artist similarity, and runtime proximity. Matching is deliberately
conservative: a wrong song in your playlist is silent and may go unnoticed, but
a dropped song stays in the queue where you can see it. Lower `--min-score`
to accept more, raise it to accept less.

Every track is logged as it is decided, so a run cut short by a crash or a
quota still leaves a scrollback record of exactly what was resolved. Pass
`--quiet` for totals only.

### The queue and the log

A large library takes thousands of searches, and Spotify's rolling quota can
run out partway. Rather than keeping a report and working out what to redo,
the import keeps a work list and drains it. Two files sit beside the input CSV:

| File                       | Contents                                           |
| -------------------------- | -------------------------------------------------- |
| `spotify_import_queue.csv` | Tracks **not yet in Spotify** — the work remaining |
| `spotify_import_log.csv`   | Tracks **confirmed in the playlist** — append-only |

A track is in exactly one of them, so `queue + log` is always your whole
library, and the queue's line count is literally what is left to do. The
original export is only ever read; the queue is a copy.

That makes every run a resume. There is no flag — you just run the same
command again:

```
python -m itunes_ratings_exporter spotify-import --min-stars 4
```

A track leaves the queue only once Spotify has accepted it, and matches are
delivered in batches of 100 (the API's maximum per call) as they accumulate.
So an interruption leaves a real partial playlist, plus a queue holding
precisely the tracks that did not make it. Nothing is redone and nothing is
double-added.

Tracks that were searched but not resolved stay queued with their verdict
recorded — `rejected` (a near miss, shown with the candidate so you can judge
it) or `not_found`. Re-running retries them, which is what you want after
lowering `--min-score` or when a miss was just a bad search. Matching results
are kept, so a re-run spends search quota only on tracks that still need it.

The queue is stored **least-tried first**, with an `attempts` count per track.
Tracks nobody has searched yet lead; a track that is tried and stays unresolved
sinks below everything tried fewer times. Since Spotify's ceiling is spent per
*request* rather than per track, and re-examining a known near miss costs the
same searches as a track nobody has looked at — while only the latter can add
anything to the playlist — this is what stops a series of quota-limited runs
from spending their whole budget on the same head of the list. The order is
written to the file, not just used within a run, so a run cut short by a spent
quota still leaves the correct work list behind.

A run creates a playlist the first time and tops up that same playlist on
later runs, so an import broken across several sessions still ends as one
playlist.

`--restart` rebuilds the queue from the export, discarding matching progress.
The log is kept, so tracks already in the playlist are not imported twice.

### Quota

Spotify limits how many requests an app may make, and a large library is
thousands of searches. Two things follow.

Requests are paced (`--rate`, default 2/second) rather than fired as fast as
they will go, and getting throttled widens the pace for the rest of the run.
Treat this as a precaution rather than a fix: across a full 826-track run the
burst-retry path never fired once, so there is currently **no evidence that
pacing helps** this workload.

The ceiling that does bite is not yet understood. A run reached track 593 and
landed 500 tracks before Spotify asked for 23.9 hours — at a deliberately slow
2 requests/second. That is consistent with either a fixed request budget per
rolling day (in which case pacing is irrelevant) or a rate limit with
escalating penalties (in which case 2/second is still too fast). Nothing
observed so far distinguishes them.

So every run now reports what it spent:

```
Spent 912 Spotify requests this run (paced at 2/s).
```

The two explanations predict different things about that number: a budget caps
it whatever the pace, while a rate limit should let a slower run reach a higher
count. Comparing consecutive runs at different `--rate` values is what will
settle it. Either way the practical answer is the same — re-run the next day,
which the queue makes painless — and an app on Spotify's default development
quota can apply for extended quota to raise the ceiling.

Because the ceiling is spent per _request_ under either explanation, a resume
searches never-seen tracks before retrying tracks it has already judged: a
recorded near miss costs the same three searches as an unseen track but cannot
add anything to the playlist.

Additional exit codes: `4` input CSV missing or wrong shape, `5` authorization
failed, `6` the Spotify API failed, `7` the account's request quota is spent.
For `6` and `7` the queue holds what is left — wait if asked, then re-run the
same command.

Ratings are exported as 0–5 stars. `rating_computed` marks ratings iTunes
derived from the album rating rather than ones you set. `library.json`
carries a `schema_version` field for downstream tools (for example, a future
Spotify-matching importer).

### Libraries on a NAS or network share

Works with libraries hosted on a NAS (Synology, etc.), whether reached
through a mapped drive letter or a UNC path:

```
python -m itunes_ratings_exporter --library "Z:\Music\iTunes\iTunes Music Library.xml"
```

Exported `file_path` values preserve whichever form iTunes recorded —
`Z:\Music\song.mp3` for a mapped drive, `\\SERVER\share\song.mp3` for a UNC
path. Mapped drive letters are per-machine, so a library recorded as `Z:`
resolves only where that mapping exists.

## Development

```
pip install pytest
python -m pytest
```

Design docs live under `docs/superpowers/`.
