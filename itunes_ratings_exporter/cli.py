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
    try:
        out.mkdir(parents=True, exist_ok=True)
        write_tracks_csv(tracks, out / "tracks.csv")
        write_rated_csv(tracks, out / "rated.csv")
        write_playlists_csv(playlists, tracks, out / "playlists.csv")
        write_library_json(tracks, playlists, str(library), out / "library.json")
    except OSError as exc:
        print(f"Could not write to output directory {out}: {exc}", file=sys.stderr)
        return 3

    rated = manually_rated(tracks)
    played = [t for t in tracks if t["play_count"] is not None]
    print(
        f"Exported {len(tracks)} tracks ({len(rated)} manually rated, "
        f"{len(played)} with plays), {len(playlists)} playlists -> {out}"
    )
    return 0
