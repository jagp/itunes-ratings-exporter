"""Interactively resolve the tracks the import could not place.

After ``spotify-import`` has drained every safe match, the queue still holds
``rejected`` near misses and ``not_found`` tracks. The matcher never reads the
album field, so a mix-CD album name cannot be why a track was rejected: the
usual culprits are the duration hard-veto (a burned rip drifts from the
canonical runtime) or a score a hair under the threshold. Either way the right
candidate is very often already recorded in the queue row -- resolution is a
human decision, not a new search.

Work is split by how much attention each track needs:

Tier 1
    Rejected rows whose stored candidate re-scores at least ``TIER1_BAR`` on
    both title and artist. Reviewed as one list and accepted in bulk. Costs
    no Spotify requests.
Tier 2
    Everything else, one side-by-side card at a time. Only the ``[c]andidates``
    action -- fetch fresh alternatives -- spends quota, so authorization is
    deferred until the first time it is chosen; a session that never asks for
    candidates never touches the network.
Tier 3
    ``[u]navailable`` records that a track simply is not on Spotify. Those
    rows keep their line in the queue file but leave the import's work loop,
    so later resumes stop spending searches proving the same absence.

The resolver only edits verdicts. Accepted tracks become ``matched`` rows and
ride into the playlist through ``spotify-import``'s existing delivery path,
keeping the batch and crash-safety guarantees in one place.
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Union

from .importer import LOCAL, PENDING, QUEUE_FIELDS, SETTLED, UNAVAILABLE, _write
from .matcher import (
    MatchScore,
    artist_similarity,
    candidate_artists,
    title_similarity,
    top_candidates,
)

# Both title and artist must independently re-score this high against the
# stored candidate for a rejection to be presented as a bulk-acceptable near
# miss. One weak axis is exactly the doubt that needs a human card.
TIER1_BAR = 0.85

CANDIDATE_COUNT = 5

_RESOLVABLE = ("rejected", "not_found")


def _fmt_duration(raw: "Union[str, int, None]") -> str:
    try:
        ms = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "?:??"
    seconds = ms // 1000
    return "{}:{:02d}".format(seconds // 60, seconds % 60)


def stored_scores(row: "dict[str, Any]") -> "Optional[tuple[float, float]]":
    """Re-score the recorded candidate from the queue row alone.

    The queue keeps the candidate's title and artist precisely so this needs
    no network. Artist credits were joined with ", " when recorded; splitting
    on the same separator is lossy for names that contain one, but similarity
    against the parts tolerates that.
    """
    if not (row.get("spotify_uri") or "").strip():
        return None
    spotify_title = row.get("spotify_title", "")
    spotify_artists = [a for a in (row.get("spotify_artist") or "").split(", ") if a]
    if not spotify_title or not spotify_artists:
        return None
    return (
        title_similarity(row.get("title", ""), spotify_title),
        artist_similarity(row.get("artist", ""), spotify_artists),
    )


def is_tier1(row: "dict[str, Any]") -> bool:
    if row.get("status") != "rejected":
        return False
    scores = stored_scores(row)
    return scores is not None and min(scores) >= TIER1_BAR


def resolvable(
    queue: "list[dict[str, Any]]", include_unavailable: bool = False
) -> "list[dict[str, Any]]":
    """The rows a resolve session should look at, in queue order.

    ``include_unavailable`` reopens the settled verdicts too -- both
    unavailable and locally-managed rows -- for the session that wants to
    reconsider them.
    """
    wanted = _RESOLVABLE + (SETTLED if include_unavailable else ())
    return [r for r in queue if r.get("status") in wanted]


def accept(row: "dict[str, Any]") -> None:
    """Promote the stored candidate to a match; delivery is import's job."""
    row["status"] = "matched"


def accept_candidate(
    row: "dict[str, Any]", candidate: "dict[str, Any]", score: MatchScore
) -> None:
    """Record a hand-picked candidate exactly as the matcher would have."""
    row["status"] = "matched"
    row["score"] = "{:.3f}".format(score.total)
    row["spotify_uri"] = candidate.get("uri", "")
    row["spotify_title"] = candidate.get("name", "")
    row["spotify_artist"] = ", ".join(candidate_artists(candidate))
    row["spotify_album"] = (candidate.get("album") or {}).get("name", "")
    row["spotify_duration_ms"] = str(candidate.get("duration_ms") or "")


def mark_unavailable(row: "dict[str, Any]") -> None:
    row["status"] = UNAVAILABLE


def mark_local(row: "dict[str, Any]") -> None:
    """The owner's circuit breaker: stop searching, manage this one locally."""
    row["status"] = LOCAL


def requeue(row: "dict[str, Any]") -> None:
    """Send a row back to the import's search loop as if never tried."""
    row["status"] = PENDING


def _card(row: "dict[str, Any]", position: int, total: int) -> str:
    lines = [
        "",
        "[{:>3}/{}] {} {}".format(
            position, total, row.get("status", ""), row.get("score", "")
        ).rstrip(),
        "  iTunes : {} -- {}".format(row.get("title", ""), row.get("artist", "")),
        "           album: {:<28} {}".format(
            row.get("album", ""), _fmt_duration(row.get("duration_ms"))
        ),
    ]
    if (row.get("spotify_uri") or "").strip():
        lines += [
            "  Spotify: {} -- {}".format(
                row.get("spotify_title", ""), row.get("spotify_artist", "")
            ),
            "           album: {:<28} {}".format(
                row.get("spotify_album", ""), _fmt_duration(row.get("spotify_duration_ms"))
            ),
        ]
    else:
        lines.append("  Spotify: (no candidate recorded)")
    return "\n".join(lines)


def _tier1_line(index: int, row: "dict[str, Any]") -> str:
    return "  [{:>3}] {}  {} -- {}  ->  {} -- {}  ({})".format(
        index,
        row.get("score", "") or "?",
        row.get("title", ""),
        row.get("artist", ""),
        row.get("spotify_title", ""),
        row.get("spotify_artist", ""),
        row.get("spotify_album", "") or "album unknown",
    )


def _parse_skips(text: str, count: int) -> "Optional[set[int]]":
    """Read ``skip 3,7`` / ``3 7`` into indices, or None if unparseable."""
    text = text.strip().lower()
    if text.startswith("skip"):
        text = text[4:]
    parts = [p for p in text.replace(",", " ").split() if p]
    if not parts:
        return None
    picked = set()
    for part in parts:
        if not part.isdigit() or not 1 <= int(part) <= count:
            return None
        picked.add(int(part))
    return picked


def _resolve_tier1(
    rows: "list[dict[str, Any]]",
    save: "Callable[[], None]",
    input_fn: "Callable[[str], str]",
    print_fn: "Callable[[str], None]",
) -> int:
    """Review-then-bulk: one list, one confirmation, zero requests."""
    print_fn(
        "\nTier 1 -- {} near miss(es) where the recorded candidate re-scores "
        ">= {:.2f} on both title and artist:".format(len(rows), TIER1_BAR)
    )
    for index, row in enumerate(rows, start=1):
        print_fn(_tier1_line(index, row))
    while True:
        answer = input_fn(
            "Accept all {}? [y]es / [s]kip this list / 'skip 3,7' to leave "
            "some out > ".format(len(rows))
        ).strip().lower()
        if answer in ("y", "yes"):
            skips: "set[int]" = set()
        elif answer in ("s", "skip", "n", "no"):
            print_fn("Left for the walkthrough.")
            return 0
        else:
            parsed = _parse_skips(answer, len(rows))
            if parsed is None:
                continue
            skips = parsed
        accepted = 0
        for index, row in enumerate(rows, start=1):
            if index not in skips:
                accept(row)
                accepted += 1
        save()
        print_fn(
            "Accepted {} track(s){}.".format(
                accepted, ", left {} out".format(len(skips)) if skips else ""
            )
        )
        return accepted


def _pick_candidate(
    row: "dict[str, Any]",
    get_client: "Callable[[], Any]",
    input_fn: "Callable[[str], str]",
    print_fn: "Callable[[str], None]",
) -> bool:
    """Fetch fresh candidates and let the user choose one. True if accepted."""
    try:
        ranked = top_candidates(row, get_client(), CANDIDATE_COUNT)
    except Exception as exc:  # auth, quota, API -- the session goes on offline
        print_fn("Could not search Spotify: {}".format(exc))
        return False
    if not ranked:
        print_fn("Spotify returned no candidates at all.")
        return False
    for index, (candidate, score) in enumerate(ranked, start=1):
        print_fn(
            "  [{}] {:.3f}  {} -- {}  ({}, {})".format(
                index,
                score.total,
                candidate.get("name", ""),
                ", ".join(candidate_artists(candidate)),
                (candidate.get("album") or {}).get("name", "") or "album unknown",
                _fmt_duration(candidate.get("duration_ms")),
            )
        )
    while True:
        answer = input_fn("Pick 1-{} or press Enter to go back > ".format(len(ranked))).strip()
        if not answer:
            return False
        if answer.isdigit() and 1 <= int(answer) <= len(ranked):
            candidate, score = ranked[int(answer) - 1]
            accept_candidate(row, candidate, score)
            return True


def run_resolve(
    queue: "list[dict[str, Any]]",
    queue_path,
    get_client: "Callable[[], Any]",
    input_fn: "Optional[Callable[[str], str]]" = None,
    print_fn: "Optional[Callable[[str], None]]" = None,
    include_unavailable: bool = False,
) -> "dict[str, int]":
    """Walk the unresolved remainder, editing verdicts in the queue file.

    The queue is rewritten after every decision, so quitting -- or crashing --
    mid-session keeps everything decided so far. ``get_client`` is called at
    most once, and only if the user asks for fresh candidates: everything else
    works from what previous runs already recorded.
    """
    # Resolved at call time, not definition time, so tests (and anything
    # else) can swap builtins.input/print and be honored.
    input_fn = input_fn or input
    print_fn = print_fn or print
    tally = {"accepted": 0, "unavailable": 0, "local": 0, "requeued": 0, "kept": 0}

    def save() -> None:
        _write(queue_path, QUEUE_FIELDS, queue)

    client_box: "dict[str, Any]" = {}

    def client() -> Any:
        if "client" not in client_box:
            client_box["client"] = get_client()
        return client_box["client"]

    todo = resolvable(queue, include_unavailable)
    tier1 = [r for r in todo if is_tier1(r)]
    if tier1:
        tally["accepted"] += _resolve_tier1(tier1, save, input_fn, print_fn)

    cards = [r for r in todo if r.get("status") in _RESOLVABLE + SETTLED]
    if cards:
        print_fn("\nTier 2 -- {} track(s) to look at one by one.".format(len(cards)))
    for position, row in enumerate(cards, start=1):
        print_fn(_card(row, position, len(cards)))
        has_candidate = bool((row.get("spotify_uri") or "").strip())
        settled = row.get("status") in SETTLED
        keys = []
        if has_candidate:
            keys.append("[a]ccept")
        keys.append("[c]andidates")
        if settled:
            # Revisiting a settled verdict: the useful verb is "give it back
            # to the search loop" -- or leave it as it is with [k]eep.
            keys.append("[r]equeue")
        else:
            keys += ["[u]navailable", "[l]ocal"]
        keys += ["[k]eep", "[q]uit"]
        legend = "  " + "  ".join(keys) + " > "
        while True:
            answer = input_fn(legend).strip().lower()
            if answer == "a" and has_candidate:
                accept(row)
                tally["accepted"] += 1
            elif answer == "c":
                if not _pick_candidate(row, client, input_fn, print_fn):
                    continue
                tally["accepted"] += 1
            elif answer == "u" and not settled:
                mark_unavailable(row)
                tally["unavailable"] += 1
            elif answer == "l" and not settled:
                mark_local(row)
                tally["local"] += 1
            elif answer == "r" and settled:
                requeue(row)
                tally["requeued"] += 1
            elif answer == "k":
                tally["kept"] += 1
            elif answer == "q":
                save()
                return tally
            else:
                continue
            save()
            break
    return tally
