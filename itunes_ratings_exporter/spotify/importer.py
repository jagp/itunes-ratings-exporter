"""Turn an exported iTunes CSV into a Spotify playlist."""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Any, Callable, Optional, Union

from .matcher import DEFAULT_MIN_SCORE, candidate_artists, find_match

REQUIRED_COLUMNS = ("title", "artist")

REPORT_FIELDS = [
    "title",
    "artist",
    "album",
    "rating_stars",
    "status",
    "score",
    "spotify_uri",
    "spotify_title",
    "spotify_artist",
]


class ImportInputError(RuntimeError):
    """The input CSV is missing, unreadable, or the wrong shape."""


def default_playlist_name(today: "Optional[date]" = None) -> str:
    return "iTunes Ratings {}".format((today or date.today()).isoformat())


def read_rows(
    csv_path: "Union[str, Path]", min_stars: int = 0, limit: "Optional[int]" = None
) -> "list[dict[str, Any]]":
    """Load the CSV, keeping only tracks rated at least ``min_stars``."""
    path = Path(csv_path)
    if not path.is_file():
        raise ImportInputError(
            "No CSV at {}. Run the exporter first, or point --csv at one.".format(path)
        )
    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            missing = [c for c in REQUIRED_COLUMNS if c not in fieldnames]
            if missing:
                raise ImportInputError(
                    "{} is missing required column(s): {}".format(path, ", ".join(missing))
                )
            if min_stars > 0 and "rating_stars" not in fieldnames:
                raise ImportInputError(
                    "{} has no rating_stars column, so --min-stars cannot be "
                    "applied. Use --min-stars 0 to import every row.".format(path)
                )
            rows = [r for r in reader if _stars(r) >= min_stars]
    except OSError as exc:
        raise ImportInputError("Could not read {}: {}".format(path, exc))
    return rows[:limit] if limit else rows


def _stars(row: "dict[str, Any]") -> int:
    try:
        return int(row.get("rating_stars") or 0)
    except (TypeError, ValueError):
        return 0


def write_report(path: "Union[str, Path]", results: "list[dict[str, Any]]") -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        for result in results:
            writer.writerow({k: result.get(k, "") for k in REPORT_FIELDS})


def _result_row(row: "dict[str, Any]", match) -> "dict[str, Any]":
    candidate = match.candidate or {}
    return {
        "title": row.get("title", ""),
        "artist": row.get("artist", ""),
        "album": row.get("album", ""),
        "rating_stars": row.get("rating_stars", ""),
        "status": match.status,
        "score": "" if match.score is None else "{:.3f}".format(match.score.total),
        "spotify_uri": candidate.get("uri", "") if match.status == "matched" else "",
        "spotify_title": candidate.get("name", ""),
        "spotify_artist": ", ".join(candidate_artists(candidate)),
    }


def run_import(
    rows: "list[dict[str, Any]]",
    client,
    report_path: "Union[str, Path]",
    name: "Optional[str]" = None,
    public: bool = False,
    dry_run: bool = False,
    min_score: float = DEFAULT_MIN_SCORE,
    progress: "Optional[Callable[[str], None]]" = None,
) -> "dict[str, Any]":
    """Match every row, create the playlist, and write the report.

    The report is written even when the API fails partway through, so the
    matching work -- the slow part -- is never lost to a network blip.
    """
    results: "list[dict[str, Any]]" = []
    uris: "list[str]" = []
    try:
        for index, row in enumerate(rows, start=1):
            match = find_match(row, client, min_score)
            results.append(_result_row(row, match))
            if match.status == "matched":
                uris.append(match.candidate["uri"])
            if progress and (index % 25 == 0 or index == len(rows)):
                progress("Matched {}/{} tracks ({} found)".format(index, len(rows), len(uris)))

        summary = {
            "total": len(rows),
            "matched": len(uris),
            "rejected": sum(1 for r in results if r["status"] == "rejected"),
            "not_found": sum(1 for r in results if r["status"] == "not_found"),
            "playlist_url": "",
            "added": 0,
            "dry_run": dry_run,
        }
        if uris and not dry_run:
            # POST /me/playlists infers the owner from the token, so there is
            # no longer any reason to look the current user up first.
            playlist = client.create_playlist(
                name or default_playlist_name(),
                public,
                "Imported from an iTunes library export.",
            )
            summary["added"] = client.add_tracks(playlist["id"], uris)
            summary["playlist_url"] = playlist.get("external_urls", {}).get("spotify", "")
        return summary
    finally:
        write_report(report_path, results)
