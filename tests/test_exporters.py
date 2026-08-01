import csv
import json
from pathlib import Path

from itunes_ratings_exporter.exporters import (
    CSV_FIELDS,
    manually_rated,
    write_library_json,
    write_playlists_csv,
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
    assert row["title"] == "Túnel"
    assert row["rating_stars"] == "5"
    assert row["rating_computed"] == "false"
    assert row["file_path"] == "C:\\Users\\jared\\Música\\Túnel.mp3"
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
        "title": "Túnel",
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
