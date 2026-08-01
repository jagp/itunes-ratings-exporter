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
