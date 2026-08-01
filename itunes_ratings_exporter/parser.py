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
