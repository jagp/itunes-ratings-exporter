# itunes-ratings-exporter

Makes your iTunes ratings/plays metadata portable.

Reads `iTunes Music Library.xml` (classic iTunes for Windows) and exports:

| File | Contents |
| --- | --- |
| `tracks.csv` | Every track: title, artist, album, rating, play count, duration, file path, IDs |
| `rated.csv` | Only tracks with a rating you set yourself (computed ratings excluded) |
| `playlists.csv` | Your playlists, one row per track membership |
| `library.json` | Everything in one structured, versioned file |

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

| Option | Default | Meaning |
| --- | --- | --- |
| `--csv PATH` | `export/rated.csv` | Input CSV |
| `--name NAME` | `iTunes Ratings <today>` | Playlist name |
| `--min-stars N` | `4` | Only import tracks rated at least N stars |
| `--limit N` | all | Import at most N tracks — good for a trial run |
| `--public` | off | Create a public playlist |
| `--dry-run` | off | Match and report, but create nothing |
| `--min-score F` | `0.72` | Match acceptance threshold, `0.0`–`1.0` |
| `--client-id ID` | `$SPOTIFY_CLIENT_ID` | Spotify app client ID |
| `--report PATH` | next to the input CSV | Where the report is written |

A first run worth trying:

```
python -m itunes_ratings_exporter spotify-import --min-stars 5 --limit 20 --dry-run
```

### Matching and the report

iTunes and Spotify disagree about metadata constantly — remaster suffixes,
where featured artists live, live versions. Each track is scored on title
similarity, artist similarity, and runtime proximity. Matching is deliberately
conservative: a wrong song in your playlist is silent and may go unnoticed, but
a dropped song appears in the report where you can see it. Lower `--min-score`
to accept more, raise it to accept less.

Every run writes `spotify_import_report.csv` — one row per track with a status
of `matched`, `rejected` (a near miss, shown with the candidate so you can
judge it), or `not_found`. The report is written even if the run fails partway,
so matching work is never lost.

Each run creates a new playlist; it never modifies an existing one.

Additional exit codes: `4` input CSV missing or wrong shape, `5` authorization
failed, `6` the Spotify API failed (the report is still written).

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
