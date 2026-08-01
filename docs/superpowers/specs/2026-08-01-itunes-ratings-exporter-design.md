# iTunes Ratings Exporter — Design

**Date:** 2026-08-01
**Status:** Approved design, pending implementation plan

## Purpose

A reusable Windows CLI that exports ratings, play counts, and playlists from a
classic iTunes-for-Windows library into open, portable files (CSV + JSON). The
exports are both a standalone backup and the input for a future companion tool
that will match tracks against Spotify and carry the metadata over, so the
output schema is deliberately matching-friendly.

## Non-goals

- No reading of the binary `iTunes Library.itl`.
- No COM automation of a live iTunes instance.
- No network lookups (MusicBrainz, Spotify, etc.) — only data already present
  in the library XML is exported.
- No writing metadata back into music files.

## Source of truth

`iTunes Music Library.xml` — the Apple plist that iTunes writes when
*Edit → Preferences → Advanced → "Share iTunes Library XML with other
applications"* is enabled. Default location:
`%USERPROFILE%\Music\iTunes\iTunes Music Library.xml`.

## Architecture

Python 3 CLI, standard library only. Package `itunes_ratings_exporter` with
three modules, each independently testable:

| Module | Responsibility |
|---|---|
| `parser.py` | Read the plist XML (`plistlib`) into plain track/playlist dicts; field conversion (stars, paths, dates) |
| `exporters.py` | Write `tracks.csv`, `rated.csv`, `playlists.csv`, `library.json` |
| `cli.py` | Argument parsing, library auto-detection, error messages, summary output |

Invocation: `python -m itunes_ratings_exporter` or the installed
`itunes-ratings-exporter` entry point.

## CLI

```
itunes-ratings-exporter [--library PATH] [--out DIR]
```

- `--library` — path to the XML; defaults to the auto-detected standard
  location above.
- `--out` — output directory, created if missing; defaults to `./export`.
- Exit is non-zero on failure. If no XML is found, the error message explains
  the iTunes preferences checkbox that enables it.
- On success, print a summary: track count, playlist count, how many tracks
  had manual ratings and plays.

## Track data model

One record per track in the library:

| Field | Source (XML key) | Notes |
|---|---|---|
| `persistent_id` | `Persistent ID` | Stable within this library; primary key for playlist references |
| `title` | `Name` | |
| `artist` | `Artist` | |
| `album_artist` | `Album Artist` | Helps Spotify matching on compilations |
| `album` | `Album` | |
| `rating_stars` | `Rating` | 0–5, converted from iTunes' 0–100 (20 per star); blank if unrated |
| `rating_computed` | `Rating Computed` | `true` when iTunes derived the rating from the album rating rather than the user setting it |
| `play_count` | `Play Count` | Blank if never played |
| `last_played` | `Play Date UTC` | ISO 8601 |
| `duration_ms` | `Total Time` | Raw milliseconds as iTunes stores it (Spotify's API also uses ms) |
| `year` | `Year` | Matching disambiguator |
| `track_number` | `Track Number` | Matching disambiguator |
| `disc_number` | `Disc Number` | Matching disambiguator |
| `genre` | `Genre` | Matching disambiguator |
| `compilation` | `Compilation` | Matching disambiguator |
| `file_path` | `Location` | Decoded from percent-encoded `file://localhost/` URL to a Windows path; blank for cloud-only tracks |
| `purchased` | `Purchased` | `true` for iTunes Store purchases |
| `kind` | `Kind` | e.g. "Purchased AAC audio file" — cheap provenance signal |

Any other stable identifier the XML happens to carry for a track (e.g. Store
metadata) is passed through into the JSON export as-is under an `extra_ids`
object, but not added to the CSVs. No identifiers are invented or looked up.

Missing optional fields are exported as blank values, never a crash.

## Playlist data model

User-created playlists only. Built-in/system playlists are excluded by their
XML marker keys (`Master`, `Distinguished Kind`, plus the `Music`/`Downloaded`
style containers). Folders are excluded; smart playlists are included and
flagged.

Per playlist: `name`, `smart` (boolean), and an ordered list of member track
`persistent_id`s.

## Outputs

All files UTF-8, written to the output directory:

| File | Contents |
|---|---|
| `tracks.csv` | One row per track, all fields above except `extra_ids` |
| `rated.csv` | Same columns, but only tracks with a manual rating: `rating_stars` present **and** `rating_computed` false |
| `playlists.csv` | One row per playlist membership: playlist name, smart flag, position, track persistent ID, track title, artist (title/artist denormalized for human readability) |
| `library.json` | Everything in one file: `schema_version` (starts at `1`), export timestamp, source library path, `tracks` array (including `extra_ids`), `playlists` array referencing tracks by persistent ID |

## Error handling

- Missing XML file → friendly instructions for enabling XML sharing in iTunes.
- Malformed plist → clear parse error naming the file.
- Unwritable output directory → clear error.
- Per-track missing fields → blanks, with the export continuing.

## Testing

Pytest with a small hand-authored fixture XML covering the edge cases: a
manually rated track, a computed-rating track, an unrated track, a cloud-only
track (no Location), a purchased track, a non-ASCII file path, a smart
playlist, and a system playlist that must be excluded. Tests cover parsing,
star conversion, path decoding, rated.csv filtering, playlist exclusion, and
both output formats end-to-end via `tmp_path`.

## Future (out of scope here)

A separate tool will ingest `library.json` / `rated.csv` and match tracks to
Spotify. This exporter's contribution is a stable, versioned schema with
title/artist/album/duration_ms and the disambiguator fields above.
