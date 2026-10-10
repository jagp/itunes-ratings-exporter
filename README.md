# itunes-ratings-exporter

## Purpose

This app automates the process of importing your manually-rated iTunes tracks, to Spotify

## Method

This app operates in two paired procedures, streamlining an otherwise tedious, manual process. Neither half caps the size of importy or export pool, allowing for even the biggest personal music collections to be moved from the retired service into a modern streaming equivalent. The two sides:
-- Step 1: Export your manually-rated iTunes tracks to a csv, preserving metadata like rating, play count, local file location, etc
-- Step 2: Import a csv of tracks' metadata, creating a new Spotify playlist containing all the tracks available on the service

### iTunes Exporter

- Reads `iTunes Music Library.xml` (classic iTunes for Windows)
- Exports:

| File            | Contents                                                                        |
| --------------- | ------------------------------------------------------------------------------- |
| `tracks.csv`    | Every track: title, artist, album, rating, play count, duration, file path, IDs |
| `rated.csv`     | Only tracks with a rating you set yourself (computed ratings excluded)          |
| `playlists.csv` | Your playlists, one row per track membership                                    |
| `library.json`  | Everything in one structured, versioned file                                    |

#### Setup

1. In iTunes: **Edit > Preferences > Advanced**, enable
   **Share iTunes Library XML with other applications**.
2. Python 3.9+ is required. No third-party packages.

#### Usage

```
python -m itunes_ratings_exporter [--library PATH] [--out DIR]
```

- `--library` — path to `iTunes Music Library.xml`; defaults to
  `%USERPROFILE%\Music\iTunes\iTunes Music Library.xml`.
- `--out` — output directory (default `./export`).

#### Outputs

```config
- Exit codes:
`0` success
`1` malformed library XML
`2` library file not found,
`3` output directory could not be written (e.g. read-only, invalid path, or full disk)
```

### Spotify Importer

- Operates on a CSV containing a list of tracks, matching them one-by-one to currently available streamed alternatievs available on Spotify, and then adding the Spotify-held copies onto a new playlist ("iTunes Rated Tracks")

#### One-time setup

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

#### Usage

Spotify import via `spotify-import` builds a Spotify playlist from the exported tracks using OAuth Authorization Code with PKCE and cached tokens at `~/.itunes-ratings-exporter/spotify-token.json`.

```
python -m itunes_ratings_exporter spotify-import [options]
```

- Conservative fuzzy matching on title, artist, and runtime, adjustable with `--min-score`.
- Resumable import workflow with `spotify_import_queue.csv` for pending tracks and `spotify_import_log.csv` for confirmed additions
- Tracks are added in batches of up to 100, unresolved items are retried later, and `--restart` can rebuild the queue while preserving the import log
- `--quiet` reduces output to totals only
- Additional options include `--dry-run`, `--limit`, `--public`, `--name`, `--quiet`, and `--client-id`.

#### Options

| Option           | Default                  | Meaning                                        |
| ---------------- | ------------------------ | ---------------------------------------------- |
| `--csv PATH`     | `export/rated.csv`       | Input CSV                                      |
| `--name NAME`    | `iTunes Ratings <today>` | Playlist name                                  |
| `--min-stars N`  | `4`                      | Only import tracks rated at least N stars      |
| `--limit N`      | all                      | Import at most N tracks — good for a trial run |
| `--public`       | off                      | Create a public playlist                       |
| `--dry-run`      | off                      | Match and report, but create nothing           |
| `--min-score F`  | `0.72`                   | Match acceptance threshold, `0.0`–`1.0`        |
| `--client-id ID` | `$SPOTIFY_CLIENT_ID`     | Spotify app client ID                          |
| `--report PATH`  | next to the input CSV    | Where the report is written                    |

#### Practical First Run

```
python -m itunes_ratings_exporter spotify-import --min-stars 5 --limit 20 --dry-run
```

## Resolving iTunes <-> Spotify Inconsistencies

### The Problem

iTunes and Spotify disagree about metadata constantly — remaster suffixes, where featured artists live, live versions, user's rips contain misconfigured metadata. This makes matching nondeterministic, especially as time goes on and drift occurs

### Our approach

- The entire library is processed in batches of 100 tracks; perfect matches (those with identical Artist, Track anme, Duration) are imediately added to the in-pgroesss iiTunes Rated Tracks playlist
- Match attempts which fail this first tier fall to a lower priority, are pushed to the bottom of the import list, and tagged with a status `rejected` or `not_found`
- Every run writes `spotify_import_report.csv` — one row per track `. The report is written even if the run fails partway, so matching work is never lost.
- each unmatched attempt also contains a similarity score, translating title/artist/duration into a numerical score
- a threshold gates "perfect match" from "near-perfect match" and allows the user to combine the two groups according to their particular iTunes library's needs. Rip your own tracks and enter the metadata by hand? You may find an effective score of 95% may provide 100% effective accuracy while minimizing the energy you have to spend identifying simple typos your manual ripping process may have introduced. For example.
- Matching is deliberately conservative: perfect matches are easy; resolving near-matches can be difficult to disambiguate. This plugin contains heuristics developed through practical experience on large library samples, to silently and routinely score 100% accuracy despite non-100% similarity scores.
- The core dial available to tune the process is the "--min-score" argument. This provides a dial the user may employ to hone the default specificity:

* Lower `--min-score` to accept more, raise it to accept less.

failed, `6` the Spotify API failed (the report is still written).

- manual track 'rating's are exported as 0–5 stars (despite the internal representation on a 0-100 scale)
  another automated source of ratings stored by iTunes in metadata is the _album rating_ a user assigns; all tracks on an album inherit the album rating as if individually rated
- For this reason, we disambiguate the tracks whose effective rating was assigned by the album rating instead of individual track; the result of an album rating are separately considered and memorialized as `rating_computed`

### Libraries on a NAS or network share

This app works with libraries hosted on a NAS (Synology, etc.), whether reached through a mapped drive letter or a UNC path:

```
python -m itunes_ratings_exporter --library "Z:\Music\iTunes\iTunes Music Library.xml"
```

Exported `file_path` values preserve whichever form iTunes recorded — `Z:\Music\song.mp3` for a mapped drive, `\\SERVER\share\song.mp3` for a UNC path. Mapped drive letters are per-machine, so a library recorded as `Z:`
resolves only where that mapping exists.

## Development / Testing

```
pip install pytest
python -m pytest
```
