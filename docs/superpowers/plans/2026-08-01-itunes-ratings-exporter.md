# iTunes Ratings Exporter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Windows CLI that exports ratings, play counts, and playlists from `iTunes Music Library.xml` into `tracks.csv`, `rated.csv`, `playlists.csv`, and `library.json`.

**Architecture:** Python package `itunes_ratings_exporter` with three modules — `parser.py` (plist XML → plain dicts), `exporters.py` (dicts → CSV/JSON files), `cli.py` (argparse wiring, auto-detection, errors, summary). Spec: `docs/superpowers/specs/2026-08-01-itunes-ratings-exporter-design.md`.

**Tech Stack:** Python 3.9+, standard library only (`plistlib`, `csv`, `json`, `argparse`, `urllib.parse`). Tests: pytest.

## Global Constraints

- Standard library only at runtime; pytest is the only dev dependency.
- `requires-python = ">=3.9"`; every module starts with `from __future__ import annotations`.
- All output files UTF-8; CSVs opened with `newline=""`.
- Ratings convert 0–100 → 0–5 stars (integer divide by 20); unrated = `None` → blank CSV cell.
- Booleans serialize to CSV as `true`/`false`; `None` serializes as empty string.
- `file_path` uses Windows backslash separators.
- Missing optional XML keys must never raise — default to `None`/`""`/`False`.
- Commit after every task with the message given in the task.

## File Structure

```
pyproject.toml
itunes_ratings_exporter/
    __init__.py          # empty marker
    __main__.py          # python -m entry
    parser.py            # plist → track/playlist dicts
    exporters.py         # dicts → csv/json files
    cli.py               # argparse, defaults, errors, summary
tests/
    fixtures/library.xml # hand-authored plist covering all edge cases
    test_parser.py
    test_exporters.py
    test_cli.py
```

---

### Task 1: Scaffolding, fixture, and track parsing

**Files:**

- Create: `pyproject.toml`, `itunes_ratings_exporter/__init__.py`, `itunes_ratings_exporter/parser.py`
- Create: `tests/fixtures/library.xml`, `tests/test_parser.py`

**Interfaces:**

- Consumes: nothing (first task).
- Produces: `parse_track(raw: dict) -> dict` returning the track dict with keys `persistent_id, title, artist, album_artist, album, rating_stars, rating_computed, play_count, last_played, duration_ms, year, track_number, disc_number, genre, compilation, file_path, purchased, kind, extra_ids`; `location_to_path(location: str) -> str`; `parse_library(path) -> dict` with keys `tracks` (list of track dicts) and `playlists` (list; fully implemented in Task 2 — this task returns tracks and may leave playlists as `[]`).

- [ ] **Step 1: Create scaffolding**

`pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "itunes-ratings-exporter"
version = "1.0.0"
description = "Export ratings, play counts, and playlists from iTunes Music Library.xml to CSV/JSON"
requires-python = ">=3.9"

[project.scripts]
itunes-ratings-exporter = "itunes_ratings_exporter.cli:main"

[tool.setuptools.packages.find]
include = ["itunes_ratings_exporter*"]
```

`itunes_ratings_exporter/__init__.py`: empty file.

- [ ] **Step 2: Create the fixture** at `tests/fixtures/library.xml` (UTF-8). It covers: manually rated track with non-ASCII percent-encoded path (101), computed-rating track (102), unrated cloud-only track (103), purchased compilation track (104), Master/system/folder playlists that must be excluded, one regular playlist, one smart playlist.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Major Version</key><integer>1</integer>
    <key>Minor Version</key><integer>1</integer>
    <key>Application Version</key><string>12.13.4.4</string>
    <key>Library Persistent ID</key><string>FFFF0000FFFF0000</string>
    <key>Tracks</key>
    <dict>
        <key>101</key>
        <dict>
            <key>Track ID</key><integer>101</integer>
            <key>Name</key><string>Túnel</string>
            <key>Artist</key><string>Aria</string>
            <key>Album Artist</key><string>Aria</string>
            <key>Album</key><string>Cañón</string>
            <key>Genre</key><string>Rock</string>
            <key>Kind</key><string>MPEG audio file</string>
            <key>Total Time</key><integer>215000</integer>
            <key>Disc Number</key><integer>1</integer>
            <key>Track Number</key><integer>3</integer>
            <key>Year</key><integer>2020</integer>
            <key>Play Count</key><integer>12</integer>
            <key>Play Date UTC</key><date>2025-06-01T10:00:00Z</date>
            <key>Rating</key><integer>100</integer>
            <key>Persistent ID</key><string>AAAA1111AAAA1111</string>
            <key>Location</key><string>file://localhost/C:/Users/jared/M%C3%BAsica/T%C3%BAnel.mp3</string>
        </dict>
        <key>102</key>
        <dict>
            <key>Track ID</key><integer>102</integer>
            <key>Name</key><string>Guessed</string>
            <key>Artist</key><string>Aria</string>
            <key>Album</key><string>Cañón</string>
            <key>Total Time</key><integer>180000</integer>
            <key>Rating</key><integer>60</integer>
            <key>Rating Computed</key><true/>
            <key>Persistent ID</key><string>BBBB2222BBBB2222</string>
            <key>Location</key><string>file://localhost/C:/Music/guessed.mp3</string>
        </dict>
        <key>103</key>
        <dict>
            <key>Track ID</key><integer>103</integer>
            <key>Name</key><string>Cloudy</string>
            <key>Artist</key><string>Nimbus</string>
            <key>Album</key><string>Vapor</string>
            <key>Total Time</key><integer>200000</integer>
            <key>Persistent ID</key><string>CCCC3333CCCC3333</string>
        </dict>
        <key>104</key>
        <dict>
            <key>Track ID</key><integer>104</integer>
            <key>Name</key><string>Bought</string>
            <key>Artist</key><string>Store</string>
            <key>Album</key><string>Receipts</string>
            <key>Kind</key><string>Purchased AAC audio file</string>
            <key>Purchased</key><true/>
            <key>Compilation</key><true/>
            <key>Total Time</key><integer>240000</integer>
            <key>Rating</key><integer>80</integer>
            <key>Play Count</key><integer>3</integer>
            <key>Persistent ID</key><string>DDDD4444DDDD4444</string>
            <key>Location</key><string>file://localhost/C:/Music/bought.m4a</string>
        </dict>
    </dict>
    <key>Playlists</key>
    <array>
        <dict>
            <key>Name</key><string>Library</string>
            <key>Master</key><true/>
            <key>Playlist ID</key><integer>201</integer>
            <key>Playlist Persistent ID</key><string>1111AAAA1111AAAA</string>
            <key>All Items</key><true/>
            <key>Playlist Items</key>
            <array>
                <dict><key>Track ID</key><integer>101</integer></dict>
                <dict><key>Track ID</key><integer>102</integer></dict>
                <dict><key>Track ID</key><integer>103</integer></dict>
                <dict><key>Track ID</key><integer>104</integer></dict>
            </array>
        </dict>
        <dict>
            <key>Name</key><string>Music</string>
            <key>Distinguished Kind</key><integer>4</integer>
            <key>Music</key><true/>
            <key>Playlist ID</key><integer>202</integer>
            <key>Playlist Persistent ID</key><string>2222BBBB2222BBBB</string>
            <key>All Items</key><true/>
        </dict>
        <dict>
            <key>Name</key><string>My Folder</string>
            <key>Folder</key><true/>
            <key>Playlist ID</key><integer>203</integer>
            <key>Playlist Persistent ID</key><string>3333CCCC3333CCCC</string>
        </dict>
        <dict>
            <key>Name</key><string>Road Trip</string>
            <key>Playlist ID</key><integer>204</integer>
            <key>Playlist Persistent ID</key><string>4444DDDD4444DDDD</string>
            <key>All Items</key><true/>
            <key>Playlist Items</key>
            <array>
                <dict><key>Track ID</key><integer>101</integer></dict>
                <dict><key>Track ID</key><integer>104</integer></dict>
                <dict><key>Track ID</key><integer>103</integer></dict>
            </array>
        </dict>
        <dict>
            <key>Name</key><string>Best Guessed</string>
            <key>Playlist ID</key><integer>205</integer>
            <key>Playlist Persistent ID</key><string>5555EEEE5555EEEE</string>
            <key>All Items</key><true/>
            <key>Smart Info</key><data>AQ==</data>
            <key>Smart Criteria</key><data>AQ==</data>
            <key>Playlist Items</key>
            <array>
                <dict><key>Track ID</key><integer>102</integer></dict>
            </array>
        </dict>
    </array>
</dict>
</plist>
```

- [ ] **Step 3: Write the failing tests** in `tests/test_parser.py`:

```python
from pathlib import Path

from itunes_ratings_exporter.parser import parse_library

FIXTURE = Path(__file__).parent / "fixtures" / "library.xml"


def _track(lib, pid):
    return next(t for t in lib["tracks"] if t["persistent_id"] == pid)


def test_manually_rated_track_fields():
    lib = parse_library(FIXTURE)
    t = _track(lib, "AAAA1111AAAA1111")
    assert t["title"] == "T\u00fanel"
    assert t["artist"] == "Aria"
    assert t["album_artist"] == "Aria"
    assert t["album"] == "Ca\u00f1\u00f3n"
    assert t["rating_stars"] == 5
    assert t["rating_computed"] is False
    assert t["play_count"] == 12
    assert t["last_played"] == "2025-06-01T10:00:00Z"
    assert t["duration_ms"] == 215000
    assert t["year"] == 2020
    assert t["track_number"] == 3
    assert t["disc_number"] == 1
    assert t["genre"] == "Rock"
    assert t["compilation"] is False
    assert t["purchased"] is False
    assert t["kind"] == "MPEG audio file"
    assert t["extra_ids"] == {"Track ID": 101}


def test_computed_rating_flagged():
    t = _track(parse_library(FIXTURE), "BBBB2222BBBB2222")
    assert t["rating_stars"] == 3
    assert t["rating_computed"] is True


def test_unrated_cloud_track_has_blanks():
    t = _track(parse_library(FIXTURE), "CCCC3333CCCC3333")
    assert t["rating_stars"] is None
    assert t["play_count"] is None
    assert t["last_played"] == ""
    assert t["file_path"] == ""


def test_purchased_compilation_flags():
    t = _track(parse_library(FIXTURE), "DDDD4444DDDD4444")
    assert t["purchased"] is True
    assert t["compilation"] is True
    assert t["rating_stars"] == 4
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `python -m pytest tests/test_parser.py -v` (from the worktree root; `pip install pytest` first if missing)
Expected: FAIL/ERROR with `ModuleNotFoundError` or `ImportError` (parser does not exist yet).

- [ ] **Step 5: Write the implementation** in `itunes_ratings_exporter/parser.py`:

```python
"""Read iTunes Music Library.xml into plain track/playlist dicts."""
from __future__ import annotations

import plistlib
from datetime import datetime
from typing import Any, Union
from pathlib import Path
from urllib.parse import unquote, urlparse

_SYSTEM_PLAYLIST_KEYS = (
    "Master",
    "Distinguished Kind",
    "Folder",
    "Music",
    "Movies",
    "TV Shows",
    "Podcasts",
    "Audiobooks",
)


def location_to_path(location: str) -> str:
    """Decode an iTunes ``file://localhost/...`` URL to a Windows path."""
    if not location:
        return ""
    path = unquote(urlparse(location).path)
    if len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return path.replace("/", "\\")


def parse_track(raw: "dict[str, Any]") -> "dict[str, Any]":
    rating = raw.get("Rating")
    last_played = raw.get("Play Date UTC")
    return {
        "persistent_id": raw.get("Persistent ID", ""),
        "title": raw.get("Name", ""),
        "artist": raw.get("Artist", ""),
        "album_artist": raw.get("Album Artist", ""),
        "album": raw.get("Album", ""),
        "rating_stars": rating // 20 if isinstance(rating, int) else None,
        "rating_computed": bool(raw.get("Rating Computed", False)),
        "play_count": raw.get("Play Count"),
        "last_played": (
            last_played.isoformat() + "Z" if isinstance(last_played, datetime) else ""
        ),
        "duration_ms": raw.get("Total Time"),
        "year": raw.get("Year"),
        "track_number": raw.get("Track Number"),
        "disc_number": raw.get("Disc Number"),
        "genre": raw.get("Genre", ""),
        "compilation": bool(raw.get("Compilation", False)),
        "file_path": location_to_path(raw.get("Location", "")),
        "purchased": bool(raw.get("Purchased", False)),
        "kind": raw.get("Kind", ""),
        "extra_ids": {
            k: v for k, v in raw.items() if k.endswith(" ID") and k != "Persistent ID"
        },
    }


def parse_library(path: "Union[str, Path]") -> "dict[str, Any]":
    with open(path, "rb") as f:
        data = plistlib.load(f)
    raw_tracks = data.get("Tracks", {})
    tracks = [parse_track(t) for t in raw_tracks.values()]
    pid_by_track_id = {
        t["Track ID"]: t.get("Persistent ID", "")
        for t in raw_tracks.values()
        if "Track ID" in t
    }
    playlists = []
    for p in data.get("Playlists", []):
        if any(k in p for k in _SYSTEM_PLAYLIST_KEYS):
            continue
        items = [
            pid_by_track_id[i["Track ID"]]
            for i in p.get("Playlist Items", [])
            if i.get("Track ID") in pid_by_track_id
        ]
        playlists.append(
            {
                "name": p.get("Name", ""),
                "smart": "Smart Info" in p,
                "track_persistent_ids": items,
            }
        )
    return {"tracks": tracks, "playlists": playlists}
```

(Playlist parsing lands here for cohesion; Task 2 adds its tests.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_parser.py -v`
Expected: 4 passed.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml itunes_ratings_exporter tests
git commit -m "feat: parse iTunes library XML into track dicts"
```

---

### Task 2: Location decoding and playlist parsing tests

**Files:**

- Modify: `tests/test_parser.py` (append tests)
- Modify: `itunes_ratings_exporter/parser.py` (only if a test exposes a bug)

**Interfaces:**

- Consumes: `parse_library`, `location_to_path` from Task 1.
- Produces: verified behavior later tasks rely on — playlist dicts `{"name": str, "smart": bool, "track_persistent_ids": list[str]}`, system playlists excluded, non-ASCII paths decoded.

- [ ] **Step 1: Append the failing/verifying tests** to `tests/test_parser.py`:

```python
from itunes_ratings_exporter.parser import location_to_path


def test_location_decodes_percent_encoding_and_backslashes():
    t = _track(parse_library(FIXTURE), "AAAA1111AAAA1111")
    assert t["file_path"] == "C:\\Users\\jared\\M\u00fasica\\T\u00fanel.mp3"


def test_location_to_path_empty():
    assert location_to_path("") == ""


def test_system_playlists_excluded():
    lib = parse_library(FIXTURE)
    assert [p["name"] for p in lib["playlists"]] == ["Road Trip", "Best Guessed"]


def test_playlist_membership_order_and_smart_flag():
    lib = parse_library(FIXTURE)
    road = next(p for p in lib["playlists"] if p["name"] == "Road Trip")
    assert road["smart"] is False
    assert road["track_persistent_ids"] == [
        "AAAA1111AAAA1111",
        "DDDD4444DDDD4444",
        "CCCC3333CCCC3333",
    ]
    smart = next(p for p in lib["playlists"] if p["name"] == "Best Guessed")
    assert smart["smart"] is True
    assert smart["track_persistent_ids"] == ["BBBB2222BBBB2222"]
```

- [ ] **Step 2: Run the new tests**

Run: `python -m pytest tests/test_parser.py -v`
Expected: all pass (Task 1 implemented this behavior). If any fail, fix `parser.py` minimally until green — the assertions above are the contract.

- [ ] **Step 3: Commit**

```bash
git add tests/test_parser.py itunes_ratings_exporter/parser.py
git commit -m "test: cover location decoding and playlist parsing"
```

---

### Task 3: CSV exporters (tracks.csv, rated.csv)

**Files:**

- Create: `itunes_ratings_exporter/exporters.py`, `tests/test_exporters.py`

**Interfaces:**

- Consumes: track dicts from `parse_library` (Task 1 shape).
- Produces: `CSV_FIELDS: list[str]`; `manually_rated(tracks: list) -> list`; `write_tracks_csv(tracks: list, path: Path) -> None`; `write_rated_csv(tracks: list, path: Path) -> None`. Cell rules: `None` → `""`, `True`/`False` → `"true"`/`"false"`.

- [ ] **Step 1: Write the failing tests** in `tests/test_exporters.py`:

```python
import csv
from pathlib import Path

from itunes_ratings_exporter.exporters import (
    CSV_FIELDS,
    manually_rated,
    write_rated_csv,
    write_tracks_csv,
)
from itunes_ratings_exporter.parser import parse_library

FIXTURE = Path(__file__).parent / "fixtures" / "library.xml"


def _read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_tracks_csv_has_all_tracks_and_fields(tmp_path):
    tracks = parse_library(FIXTURE)["tracks"]
    out = tmp_path / "tracks.csv"
    write_tracks_csv(tracks, out)
    rows = _read_csv(out)
    assert len(rows) == 4
    assert list(rows[0].keys()) == CSV_FIELDS
    row = next(r for r in rows if r["persistent_id"] == "AAAA1111AAAA1111")
    assert row["title"] == "T\u00fanel"
    assert row["rating_stars"] == "5"
    assert row["rating_computed"] == "false"
    assert row["file_path"] == "C:\\Users\\jared\\M\u00fasica\\T\u00fanel.mp3"
    cloudy = next(r for r in rows if r["persistent_id"] == "CCCC3333CCCC3333")
    assert cloudy["rating_stars"] == ""
    assert cloudy["play_count"] == ""
    bought = next(r for r in rows if r["persistent_id"] == "DDDD4444DDDD4444")
    assert bought["purchased"] == "true"
    assert bought["compilation"] == "true"


def test_rated_csv_excludes_computed_and_unrated(tmp_path):
    tracks = parse_library(FIXTURE)["tracks"]
    assert {t["persistent_id"] for t in manually_rated(tracks)} == {
        "AAAA1111AAAA1111",
        "DDDD4444DDDD4444",
    }
    out = tmp_path / "rated.csv"
    write_rated_csv(tracks, out)
    rows = _read_csv(out)
    assert {r["persistent_id"] for r in rows} == {
        "AAAA1111AAAA1111",
        "DDDD4444DDDD4444",
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_exporters.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: itunes_ratings_exporter.exporters`.

- [ ] **Step 3: Write the implementation** in `itunes_ratings_exporter/exporters.py`:

```python
"""Write parsed library data to CSV and JSON files."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Union

CSV_FIELDS = [
    "persistent_id",
    "title",
    "artist",
    "album_artist",
    "album",
    "rating_stars",
    "rating_computed",
    "play_count",
    "last_played",
    "duration_ms",
    "year",
    "track_number",
    "disc_number",
    "genre",
    "compilation",
    "file_path",
    "purchased",
    "kind",
]


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def manually_rated(tracks: "list[dict]") -> "list[dict]":
    return [
        t for t in tracks if t["rating_stars"] is not None and not t["rating_computed"]
    ]


def write_tracks_csv(tracks: "list[dict]", path: "Union[str, Path]") -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for t in tracks:
            writer.writerow({k: _cell(t.get(k)) for k in CSV_FIELDS})


def write_rated_csv(tracks: "list[dict]", path: "Union[str, Path]") -> None:
    write_tracks_csv(manually_rated(tracks), path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_exporters.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add itunes_ratings_exporter/exporters.py tests/test_exporters.py
git commit -m "feat: export tracks.csv and manually-rated rated.csv"
```

---

### Task 4: playlists.csv and library.json exporters

**Files:**

- Modify: `itunes_ratings_exporter/exporters.py` (append functions)
- Modify: `tests/test_exporters.py` (append tests)

**Interfaces:**

- Consumes: `CSV_FIELDS`, `_cell`, track/playlist dict shapes from Tasks 1–3.
- Produces: `write_playlists_csv(playlists: list, tracks: list, path) -> None` (columns `playlist, smart, position, track_persistent_id, title, artist`; position 1-based); `write_library_json(tracks: list, playlists: list, source: str, path, exported_at: str | None = None) -> None` (JSON keys `schema_version` = 1, `exported_at`, `source_library`, `tracks`, `playlists`).

- [ ] **Step 1: Append the failing tests** to `tests/test_exporters.py`:

```python
import json

from itunes_ratings_exporter.exporters import write_library_json, write_playlists_csv


def test_playlists_csv_rows(tmp_path):
    lib = parse_library(FIXTURE)
    out = tmp_path / "playlists.csv"
    write_playlists_csv(lib["playlists"], lib["tracks"], out)
    rows = _read_csv(out)
    assert len(rows) == 4
    assert rows[0] == {
        "playlist": "Road Trip",
        "smart": "false",
        "position": "1",
        "track_persistent_id": "AAAA1111AAAA1111",
        "title": "T\u00fanel",
        "artist": "Aria",
    }
    assert rows[3]["playlist"] == "Best Guessed"
    assert rows[3]["smart"] == "true"
    assert rows[3]["track_persistent_id"] == "BBBB2222BBBB2222"


def test_library_json_structure(tmp_path):
    lib = parse_library(FIXTURE)
    out = tmp_path / "library.json"
    write_library_json(
        lib["tracks"], lib["playlists"], "X:\\lib.xml", out, exported_at="2026-08-01T00:00:00+00:00"
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["exported_at"] == "2026-08-01T00:00:00+00:00"
    assert data["source_library"] == "X:\\lib.xml"
    assert len(data["tracks"]) == 4
    track = next(t for t in data["tracks"] if t["persistent_id"] == "AAAA1111AAAA1111")
    assert track["extra_ids"] == {"Track ID": 101}
    assert len(data["playlists"]) == 2


def test_library_json_default_timestamp(tmp_path):
    lib = parse_library(FIXTURE)
    out = tmp_path / "library.json"
    write_library_json(lib["tracks"], lib["playlists"], "X:\\lib.xml", out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["exported_at"]  # non-empty ISO timestamp
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_exporters.py -v`
Expected: new tests FAIL with `ImportError` (functions not defined).

- [ ] **Step 3: Append the implementation** to `itunes_ratings_exporter/exporters.py`:

```python
PLAYLIST_CSV_FIELDS = [
    "playlist",
    "smart",
    "position",
    "track_persistent_id",
    "title",
    "artist",
]


def write_playlists_csv(
    playlists: "list[dict]", tracks: "list[dict]", path: "Union[str, Path]"
) -> None:
    by_pid = {t["persistent_id"]: t for t in tracks}
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=PLAYLIST_CSV_FIELDS)
        writer.writeheader()
        for p in playlists:
            for position, pid in enumerate(p["track_persistent_ids"], start=1):
                track = by_pid.get(pid, {})
                writer.writerow(
                    {
                        "playlist": p["name"],
                        "smart": _cell(p["smart"]),
                        "position": position,
                        "track_persistent_id": pid,
                        "title": track.get("title", ""),
                        "artist": track.get("artist", ""),
                    }
                )


def write_library_json(
    tracks: "list[dict]",
    playlists: "list[dict]",
    source: str,
    path: "Union[str, Path]",
    exported_at: "str | None" = None,
) -> None:
    payload = {
        "schema_version": 1,
        "exported_at": exported_at
        or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_library": source,
        "tracks": tracks,
        "playlists": playlists,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
```

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest -v`
Expected: all tests pass (parser + exporters).

- [ ] **Step 5: Commit**

```bash
git add itunes_ratings_exporter/exporters.py tests/test_exporters.py
git commit -m "feat: export playlists.csv and library.json"
```

---

### Task 5: CLI, entry points, end-to-end test, README

**Files:**

- Create: `itunes_ratings_exporter/cli.py`, `itunes_ratings_exporter/__main__.py`, `tests/test_cli.py`
- Modify: `README.md`

**Interfaces:**

- Consumes: `parse_library` (Task 1); `write_tracks_csv`, `write_rated_csv`, `manually_rated` (Task 3); `write_playlists_csv`, `write_library_json` (Task 4).
- Produces: `main(argv: list[str] | None = None) -> int` (0 success, 1 parse error, 2 library not found); `default_library_path() -> Path`.

- [ ] **Step 1: Write the failing tests** in `tests/test_cli.py`:

```python
from pathlib import Path

from itunes_ratings_exporter.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "library.xml"


def test_main_exports_all_four_files(tmp_path, capsys):
    out = tmp_path / "export"
    code = main(["--library", str(FIXTURE), "--out", str(out)])
    assert code == 0
    for name in ("tracks.csv", "rated.csv", "playlists.csv", "library.json"):
        assert (out / name).is_file(), name
    summary = capsys.readouterr().out
    assert "4 tracks" in summary
    assert "2 manually rated" in summary
    assert "2 playlists" in summary


def test_main_missing_library_explains_setting(tmp_path, capsys):
    code = main(["--library", str(tmp_path / "nope.xml"), "--out", str(tmp_path)])
    assert code == 2
    err = capsys.readouterr().err
    assert "Share iTunes Library XML" in err


def test_main_malformed_library(tmp_path, capsys):
    bad = tmp_path / "bad.xml"
    bad.write_text("not a plist", encoding="utf-8")
    code = main(["--library", str(bad), "--out", str(tmp_path)])
    assert code == 1
    assert "Could not parse" in capsys.readouterr().err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cli.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: itunes_ratings_exporter.cli`.

- [ ] **Step 3: Write the implementation** in `itunes_ratings_exporter/cli.py`:

```python
"""Command-line interface for the iTunes ratings exporter."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .exporters import (
    manually_rated,
    write_library_json,
    write_playlists_csv,
    write_rated_csv,
    write_tracks_csv,
)
from .parser import parse_library

_ENABLE_XML_HELP = (
    "No iTunes library XML found at: {path}\n"
    "In iTunes, open Edit > Preferences > Advanced and enable\n"
    '"Share iTunes Library XML with other applications", then try again.\n'
    "Or pass the file's location explicitly with --library."
)


def default_library_path() -> Path:
    home = os.environ.get("USERPROFILE") or str(Path.home())
    return Path(home) / "Music" / "iTunes" / "iTunes Music Library.xml"


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(
        prog="itunes-ratings-exporter",
        description="Export ratings, play counts, and playlists from "
        "iTunes Music Library.xml to CSV/JSON.",
    )
    ap.add_argument("--library", help="Path to iTunes Music Library.xml")
    ap.add_argument("--out", default="export", help="Output directory (default: ./export)")
    args = ap.parse_args(argv)

    library = Path(args.library) if args.library else default_library_path()
    if not library.is_file():
        print(_ENABLE_XML_HELP.format(path=library), file=sys.stderr)
        return 2

    try:
        lib = parse_library(library)
    except Exception as exc:  # plistlib raises several types for bad input
        print(f"Could not parse {library}: {exc}", file=sys.stderr)
        return 1

    tracks, playlists = lib["tracks"], lib["playlists"]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    write_tracks_csv(tracks, out / "tracks.csv")
    write_rated_csv(tracks, out / "rated.csv")
    write_playlists_csv(playlists, tracks, out / "playlists.csv")
    write_library_json(tracks, playlists, str(library), out / "library.json")

    rated = manually_rated(tracks)
    print(
        f"Exported {len(tracks)} tracks ({len(rated)} manually rated), "
        f"{len(playlists)} playlists -> {out}"
    )
    return 0
```

And `itunes_ratings_exporter/__main__.py`:

```python
import sys

from .cli import main

sys.exit(main())
```

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest -v`
Expected: all tests pass.

- [ ] **Step 5: Update `README.md`** (replace entire file):

```markdown
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

Ratings are exported as 0–5 stars. `rating_computed` marks ratings iTunes
derived from the album rating rather than ones you set. `library.json`
carries a `schema_version` field for downstream tools (for example, a future
Spotify-matching importer).

## Development

```

pip install pytest
python -m pytest

```

Design docs live under `docs/superpowers/`.
```

- [ ] **Step 6: Verify the CLI runs for real**

Run: `python -m itunes_ratings_exporter --library tests/fixtures/library.xml --out %TEMP%\itre-smoke`
Expected: exit 0 and summary line `Exported 4 tracks (2 manually rated), 2 playlists -> ...`.

- [ ] **Step 7: Commit**

```bash
git add itunes_ratings_exporter tests/test_cli.py README.md
git commit -m "feat: add CLI entry point, end-to-end tests, README"
```

---

### Task 6: Network-share (UNC) and mapped-drive path support

**Files:**

- Modify: `itunes_ratings_exporter/parser.py` (rewrite `location_to_path`)
- Modify: `tests/test_parser.py` (append unit tests)
- Modify: `README.md` (add a NAS/network-share note)

**Interfaces:**

- Consumes: `location_to_path(location: str) -> str` from Task 1.
- Produces: same signature, now correct for network shares. Later tasks and
  the downstream Spotify-matching tool rely on `file_path` being a usable
  Windows path for NAS-hosted libraries.

**Why:** iTunes writes a track's `Location` in three shapes. The Task 1
implementation reads only `urlparse(...).path`, so for a library hosted on a
NAS (e.g. Synology) it silently DROPS the server name — producing
`\music\song.mp3` instead of `\SYNOLOGY\music\song.mp3`. A silently wrong
path is worse than a crash.

| Location URL                         | Required output             |
| ------------------------------------ | --------------------------- |
| `file://localhost/C:/Music/song.mp3` | `C:\Music\song.mp3`         |
| `file://localhost/Z:/Music/song.mp3` | `Z:\Music\song.mp3`         |
| `file:///C:/Music/song.mp3`          | `C:\Music\song.mp3`         |
| `file://SYNOLOGY/music/song.mp3`     | `\\SYNOLOGY\music\song.mp3` |
| `file://///SYNOLOGY/music/song.mp3`  | `\\SYNOLOGY\music\song.mp3` |
| `""`                                 | `""`                        |

Do NOT add a NAS track to `tests/fixtures/library.xml` — several existing
tests assert exactly 4 tracks. Test `location_to_path` directly instead.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_parser.py`
      (`location_to_path` is already imported there by Task 2's tests; if that
      import is absent, add `from itunes_ratings_exporter.parser import location_to_path`):

```python
def test_location_to_path_mapped_network_drive():
    assert (
        location_to_path("file://localhost/Z:/Music/Aria/T%C3%BAnel.mp3")
        == "Z:\Music\Aria\T\u00fanel.mp3"
    )


def test_location_to_path_unc_host_in_netloc():
    assert (
        location_to_path("file://SYNOLOGY/music/Aria/song.mp3")
        == "\\\\SYNOLOGY\\music\\Aria\\song.mp3"
    )


def test_location_to_path_unc_leading_slashes():
    assert (
        location_to_path("file://///SYNOLOGY/music/Aria/song.mp3")
        == "\\\\SYNOLOGY\\music\\Aria\\song.mp3"
    )


def test_location_to_path_no_host_drive_letter():
    assert location_to_path("file:///C:/Music/song.mp3") == "C:\Music\song.mp3"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `rtk proxy python -m pytest tests/test_parser.py -v`
Expected: the three network-path tests FAIL (server name dropped / extra
slashes); `test_location_to_path_no_host_drive_letter` may already pass.
NOTE: plain `python -m pytest` output is filtered by a local hook and can
falsely print "Pytest: No tests collected" — always use `rtk proxy`.

- [ ] **Step 3: Replace `location_to_path`** in `itunes_ratings_exporter/parser.py`:

```python
def location_to_path(location: str) -> str:
    """Decode an iTunes ``file://`` URL to a Windows path.

    Handles local and mapped drives (``file://localhost/Z:/...``) as well as
    the two UNC forms iTunes writes for network shares such as a NAS:
    ``file://SERVER/share/...`` and ``file://///SERVER/share/...``.
    """
    if not location:
        return ""
    parsed = urlparse(location)
    host = unquote(parsed.netloc)
    path = unquote(parsed.path)
    if host and host.lower() != "localhost":
        path = "//" + host + path
    elif path.startswith("///"):
        path = "//" + path.lstrip("/")
    elif len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return path.replace("/", "\\")
```

- [ ] **Step 4: Run the full suite**

Run: `rtk proxy python -m pytest -v`
Expected: all tests pass, including the pre-existing local-path test
asserting `C:\Users\jared\Música\Túnel.mp3`.

- [ ] **Step 5: Add a README note** under the Usage section:

```markdown
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
```

- [ ] **Step 6: Commit**

```bash
git add itunes_ratings_exporter/parser.py tests/test_parser.py README.md
git commit -m "fix: preserve server name in UNC paths for NAS-hosted libraries"
```
