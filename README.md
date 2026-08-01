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
`Z:\Music\song.mp3` for a mapped drive, `\SERVER\share\song.mp3` for a UNC
path. Mapped drive letters are per-machine, so a library recorded as `Z:`
resolves only where that mapping exists.

## Development

```
pip install pytest
python -m pytest
```

Design docs live under `docs/superpowers/`.
