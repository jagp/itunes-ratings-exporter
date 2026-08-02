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


def parse_track(raw: "dict[str, Any]") -> "dict[str, Any]":
    rating = raw.get("Rating")
    # A raw Rating of 0 means "no rating set", not "0 stars" -- iTunes' own
    # UI only offers 1-5 stars, so 0 is the absence of a rating, typically
    # left behind by scripting or third-party taggers clearing a rating.
    rating_stars = rating // 20 if isinstance(rating, int) and rating > 0 else None
    last_played = raw.get("Play Date UTC")
    return {
        "persistent_id": raw.get("Persistent ID", ""),
        "title": raw.get("Name", ""),
        "artist": raw.get("Artist", ""),
        "album_artist": raw.get("Album Artist", ""),
        "album": raw.get("Album", ""),
        "rating_stars": rating_stars,
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
