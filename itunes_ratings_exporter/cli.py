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

SPOTIFY_IMPORT = "spotify-import"


def default_library_path() -> Path:
    home = os.environ.get("USERPROFILE") or str(Path.home())
    return Path(home) / "Music" / "iTunes" / "iTunes Music Library.xml"


def main(argv: "list[str] | None" = None) -> int:
    """Dispatch to a subcommand, or run the export when none is named.

    The export interface shipped without subcommands, so bare
    ``--library``/``--out`` invocations must keep working. Only an explicit
    subcommand name in first position routes elsewhere.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == SPOTIFY_IMPORT:
        return spotify_import_main(args[1:])
    return export_main(args)


def export_main(argv: "list[str]") -> int:
    ap = argparse.ArgumentParser(
        prog="itunes-ratings-exporter",
        description="Export ratings, play counts, and playlists from "
        "iTunes Music Library.xml to CSV/JSON.",
        epilog="Run '{} --help' to push an export into Spotify.".format(SPOTIFY_IMPORT),
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


def _spotify_import_parser() -> argparse.ArgumentParser:
    from .spotify.matcher import DEFAULT_MIN_SCORE

    ap = argparse.ArgumentParser(
        prog="itunes-ratings-exporter " + SPOTIFY_IMPORT,
        description="Save an exported ratings CSV to Spotify Liked Songs.",
    )
    ap.add_argument("--csv", default=str(Path("export") / "rated.csv"), help="Input CSV")
    ap.add_argument("--min-stars", type=int, default=4, help="Minimum star rating (default: 4)")
    ap.add_argument("--limit", type=int, help="Import at most this many tracks")
    ap.add_argument("--dry-run", action="store_true", help="Match and report, change nothing")
    ap.add_argument(
        "--min-score",
        type=float,
        default=DEFAULT_MIN_SCORE,
        help="Match acceptance threshold, 0.0-1.0 (default: %(default)s)",
    )
    ap.add_argument(
        "--client-id",
        default=os.environ.get("SPOTIFY_CLIENT_ID", ""),
        help="Spotify app client ID (default: $SPOTIFY_CLIENT_ID)",
    )
    ap.add_argument("--report", help="Report CSV path (default: alongside the input CSV)")
    return ap


def spotify_import_main(argv: "list[str]", client=None) -> int:
    """Match an exported CSV against Spotify and save hits to Liked Songs.

    ``client`` exists so tests can drive the whole command with a fake API;
    normal runs authorize and construct a real one.
    """
    from .spotify.auth import AuthError, get_access_token
    from .spotify.client import SpotifyApiError, SpotifyClient
    from .spotify.importer import ImportInputError, read_rows, run_import

    args = _spotify_import_parser().parse_args(argv)

    try:
        rows = read_rows(args.csv, args.min_stars, args.limit)
    except ImportInputError as exc:
        print(str(exc), file=sys.stderr)
        return 4

    if not rows:
        print(f"No tracks in {args.csv} rated {args.min_stars}+ stars. Nothing to import.")
        return 0

    report = (
        Path(args.report)
        if args.report
        else Path(args.csv).parent / "spotify_import_report.csv"
    )

    if client is None:
        # A dry run still needs search access to score candidates; it simply
        # never writes anything back to the account.
        try:
            client = SpotifyClient(get_access_token(args.client_id))
        except AuthError as exc:
            print(str(exc), file=sys.stderr)
            return 5

    print(f"Matching {len(rows)} tracks against Spotify...")
    try:
        summary = run_import(
            rows,
            client,
            report,
            dry_run=args.dry_run,
            min_score=args.min_score,
            progress=lambda msg: print(msg, flush=True),
        )
    except SpotifyApiError as exc:
        print(f"{exc}\nPartial results were written to {report}.", file=sys.stderr)
        return 6
    except OSError as exc:
        print(f"Could not write the report to {report}: {exc}", file=sys.stderr)
        return 3

    print(
        "Matched {matched}/{total} tracks ({rejected} near misses, "
        "{not_found} not found)".format(**summary)
    )
    if summary["dry_run"]:
        print(f"Dry run: nothing saved. See {report}.")
    elif summary["added"]:
        print(f"Added {summary['added']} tracks to your Liked Songs")
        print(f"Report: {report}")
    else:
        print(f"Nothing matched confidently enough to add. See {report}.")
    return 0
