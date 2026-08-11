"""Match one exported iTunes track against Spotify's catalogue.

iTunes and Spotify disagree about metadata constantly: remaster suffixes,
where featured artists live, "Pt. 2" versus "Part 2", explicit and clean
variants. This module normalizes both sides, scores the candidates Spotify
returns, and decides which are close enough to accept.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, NamedTuple, Optional

DEFAULT_MIN_SCORE = 0.72

# Weights sum to 1.0. Title and artist carry the match; duration is the
# tie-breaker that catches right-song-wrong-version.
_W_TITLE = 0.45
_W_ARTIST = 0.35
_W_DURATION = 0.20

# A duration this close is certainly the same recording; this far apart is
# certainly not. In between the score falls off linearly.
_DURATION_EXACT_MS = 3_000
_DURATION_HOPELESS_MS = 15_000

_PAREN_SUFFIX = re.compile(r"\s*[(\[][^)\]]*[)\]]\s*$")
_DASH_SUFFIX = re.compile(r"\s+[-–—]\s+[^-–—]*$")
_FEAT_CLAUSE = re.compile(r"\s*\b(?:feat|ft|featuring)\b\.?\s.*$", re.IGNORECASE)
_PUNCTUATION = re.compile(r"[^\w\s]")
_WHITESPACE = re.compile(r"\s+")
_ARTIST_SPLIT = re.compile(r"\s*(?:,|&|\+|/|\band\b|\bfeat\b\.?|\bft\b\.?|\bwith\b)\s*", re.IGNORECASE)

# Words that mark a trailing clause as a *variant qualifier* rather than part
# of the song's actual name. Without this check, stripping every parenthetical
# would turn "Sgt. Pepper's Lonely Hearts Club Band (Reprise)" -- a distinct
# track -- into the wrong song.
_VARIANT_HINT = re.compile(
    r"\b(remaster(ed)?|re-?master|mono|stereo|live|acoustic|demo|edit|version|"
    r"mix|remix|deluxe|bonus|anniversary|explicit|clean|instrumental|karaoke|"
    r"radio|album|single|feat|ft|featuring|with|\d{4})\b",
    re.IGNORECASE,
)


class MatchScore(NamedTuple):
    """The three signals behind a match decision, plus their weighted total."""

    title: float
    artist: float
    duration: float
    total: float


def strip_diacritics(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def strip_variant_suffix(title: str) -> str:
    """Drop trailing "(Remastered 2011)" / " - Live" style qualifiers.

    Only clauses containing a variant hint word are removed, so genuinely
    distinct titles like "Something (Reprise)" survive intact.
    """
    previous = None
    while previous != title:
        previous = title
        for pattern in (_PAREN_SUFFIX, _DASH_SUFFIX):
            match = pattern.search(title)
            if match and _VARIANT_HINT.search(match.group(0)):
                title = title[: match.start()].strip()
                break
    return title.strip()


def normalize(text: str) -> str:
    """Reduce a title or artist to a comparable core string."""
    if not text:
        return ""
    text = strip_diacritics(text)
    text = _FEAT_CLAUSE.sub("", text)
    text = _PUNCTUATION.sub(" ", text)
    text = _WHITESPACE.sub(" ", text)
    return text.strip().casefold()


def similarity(left: str, right: str) -> float:
    left, right = normalize(left), normalize(right)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def title_similarity(csv_title: str, spotify_title: str) -> float:
    """Compare titles both verbatim and with variant suffixes removed.

    The better of the two wins, so "Let It Be" matches Spotify's
    "Let It Be - Remastered 2009" without penalising an exact match.
    """
    direct = similarity(csv_title, spotify_title)
    stripped = similarity(strip_variant_suffix(csv_title), strip_variant_suffix(spotify_title))
    return max(direct, stripped)


def _artist_tokens(text: str) -> "list[str]":
    parts = [normalize(p) for p in _ARTIST_SPLIT.split(text or "")]
    return [p for p in parts if p]


def artist_similarity(csv_artist: str, spotify_artists: "list[str]") -> float:
    """Compare against the whole credit and each individual artist.

    iTunes stores "Queen & David Bowie" in one field while Spotify lists two
    artists, so a match on any single credited artist counts. Comparing the
    joined form too keeps duo names that contain "and" from being split apart.
    """
    if not spotify_artists:
        return 0.0
    joined = ", ".join(spotify_artists)
    best = similarity(csv_artist, joined)
    csv_parts = _artist_tokens(csv_artist) or [normalize(csv_artist)]
    for csv_part in csv_parts:
        for spotify_artist in spotify_artists:
            for spotify_part in _artist_tokens(spotify_artist) or [normalize(spotify_artist)]:
                best = max(best, similarity(csv_part, spotify_part))
    return best


def duration_similarity(csv_ms: Optional[int], spotify_ms: Optional[int]) -> float:
    """Score how close two runtimes are, neutral (0.5) when either is unknown."""
    if not csv_ms or not spotify_ms:
        return 0.5
    delta = abs(int(csv_ms) - int(spotify_ms))
    if delta <= _DURATION_EXACT_MS:
        return 1.0
    if delta >= _DURATION_HOPELESS_MS:
        return 0.0
    span = _DURATION_HOPELESS_MS - _DURATION_EXACT_MS
    return 1.0 - (delta - _DURATION_EXACT_MS) / span


def candidate_artists(candidate: "dict[str, Any]") -> "list[str]":
    return [a.get("name", "") for a in candidate.get("artists", []) if a.get("name")]


def score_candidate(row: "dict[str, Any]", candidate: "dict[str, Any]") -> MatchScore:
    title = title_similarity(row.get("title", ""), candidate.get("name", ""))
    artist = artist_similarity(row.get("artist", ""), candidate_artists(candidate))
    duration = duration_similarity(
        _as_int(row.get("duration_ms")), candidate.get("duration_ms")
    )
    total = _W_TITLE * title + _W_ARTIST * artist + _W_DURATION * duration
    return MatchScore(title, artist, duration, total)


def accept_match(
    score: MatchScore,
    min_score: float = DEFAULT_MIN_SCORE,
    *,
    artist_blank: bool = False,
    swapped: bool = False,
) -> bool:
    """Decide whether a scored candidate is really the same recording.

    This is the feature's central value judgement. A false positive puts a
    wrong song in the playlist silently and may go unnoticed for months; a
    false negative drops a right song but lands in the report CSV alongside
    the near miss, where the user can see and fix it. Misses are visible and
    recoverable, bad matches are not -- so this leans conservative and
    requires *both* title and artist to be independently plausible rather than
    letting a very strong signal on one carry a weak signal on the other.

    Two narrower doors exist beside that main gate, both tuned on a real
    resolve session (docs/superpowers/specs/2026-08-07-spotify-resolve-design.md):

    ``artist_blank``
        An empty artist field is missing data, not disagreement -- the same
        reasoning as duration_similarity's neutral 0.5 for an unknown runtime.
        But karaoke and cover versions of well-known songs often sit within
        seconds of the original, so without artist evidence the other two
        signals must be near-certain on their own.
    ``swapped``
        The row is being read with title and artist exchanged. Title and
        artist agreeing is partly circular when the fields themselves produced
        the query, so only a near-exact runtime is deterministic enough to
        accept on.
    """
    if artist_blank:
        return score.title >= 0.95 and score.duration >= 0.8
    if score.title < 0.6 or score.artist < 0.6:
        return False
    if swapped and score.duration < 0.95:
        return False
    # A duration score of exactly 0 means both runtimes are known and are
    # _DURATION_HOPELESS_MS or more apart. That is proof of a different
    # recording -- a live take, an extended mix, a radio edit -- and no
    # amount of title and artist agreement should outvote it. Weighting alone
    # cannot express this: a perfect title and artist score 0.80 on their own,
    # which would otherwise clear the default threshold.
    if score.duration <= 0.0:
        return False
    return score.total >= min_score


def search_queries(row: "dict[str, Any]") -> "list[str]":
    """Progressively looser Spotify queries for one track."""
    title = (row.get("title") or "").strip()
    artist = (row.get("artist") or "").strip()
    queries = []
    if title and artist:
        queries.append('track:"{}" artist:"{}"'.format(title, artist))
        bare = strip_variant_suffix(title)
        if bare and bare != title:
            queries.append('track:"{}" artist:"{}"'.format(bare, artist))
        queries.append("{} {}".format(title, artist))
    elif title:
        queries.append('track:"{}"'.format(title))
    seen, unique = set(), []
    for query in queries:
        if query not in seen:
            seen.add(query)
            unique.append(query)
    return unique


class MatchResult(NamedTuple):
    status: str  # "matched", "rejected", or "not_found"
    candidate: "Optional[dict[str, Any]]"
    score: Optional[MatchScore]


# "Artist - Title" and "Artist - 05 - Title": how ripping tools write a
# filename when the tags are empty.
_FILENAME_SPLIT = re.compile(r"\s*-\s+")


def row_interpretations(row: "dict[str, Any]") -> "list[tuple[dict[str, Any], str]]":
    """Alternate readings of a library row, most literal first.

    Real libraries hold rows whose fields are not what they claim: a title
    that is really "Artist - Title" from a filename (artist field blank or
    duplicating the prefix), or a title and artist that were entered swapped.
    Each reading is a candidate interpretation to search and score; the
    stricter acceptance gates for the speculative ones live in accept_match.
    """
    interpretations = [(row, "original")]
    title = (row.get("title") or "").strip()
    artist = (row.get("artist") or "").strip()

    parts = [part for part in _FILENAME_SPLIT.split(title) if part]
    if len(parts) >= 3 and parts[1].isdigit():
        parts = [parts[0], " - ".join(parts[2:])]  # drop a track-number segment
    if len(parts) == 2 and (not artist or parts[0].casefold() == artist.casefold()):
        parsed = dict(row)
        parsed["artist"], parsed["title"] = parts[0], parts[1]
        interpretations.append((parsed, "parsed"))

    if title and artist:
        swapped = dict(row)
        swapped["title"], swapped["artist"] = artist, title
        interpretations.append((swapped, "swapped"))
    return interpretations


def find_match(row, client, min_score: float = DEFAULT_MIN_SCORE) -> MatchResult:
    """Search Spotify for one CSV row and return the best decision.

    Tries each reading of the row in turn -- as written, then parsed from a
    filename-style title, then with title and artist swapped. Stops at the
    first query that produces an acceptable match; if none do, reports the
    highest-scoring candidate seen so the report can show the near miss.

    The speculative readings are kept cheap: queries already asked are never
    repeated, and the swap is a single-search gamble rather than a full pass,
    since it only pays off when the runtime then agrees almost exactly.
    """
    best_candidate = None
    best_score = None
    asked: "set[str]" = set()
    for interpretation, reading in row_interpretations(row):
        queries = search_queries(interpretation)
        if reading == "swapped":
            queries = queries[:1]
        artist_blank = not normalize(interpretation.get("artist") or "")
        for query in queries:
            if query in asked:
                continue
            asked.add(query)
            for candidate in client.search_tracks(query):
                score = score_candidate(interpretation, candidate)
                if best_score is None or score.total > best_score.total:
                    best_candidate, best_score = candidate, score
                if accept_match(
                    score, min_score,
                    artist_blank=artist_blank, swapped=(reading == "swapped"),
                ):
                    return MatchResult("matched", candidate, score)
    if best_candidate is None:
        return MatchResult("not_found", None, None)
    return MatchResult("rejected", best_candidate, best_score)


def top_candidates(row, client, n: int = 5) -> "list[tuple[dict[str, Any], MatchScore]]":
    """Every candidate the queries surface, best first, one entry per URI.

    ``find_match`` stops at the first acceptable candidate because the import
    only needs a verdict. Manual resolution needs the field: a human choosing
    from a list can recognise the right recording at a score no automatic
    threshold could safely accept.
    """
    seen: "dict[str, tuple[dict[str, Any], MatchScore]]" = {}
    for query in search_queries(row):
        for candidate in client.search_tracks(query):
            uri = candidate.get("uri", "")
            score = score_candidate(row, candidate)
            kept = seen.get(uri)
            if kept is None or score.total > kept[1].total:
                seen[uri] = (candidate, score)
    ranked = sorted(seen.values(), key=lambda pair: pair[1].total, reverse=True)
    return ranked[:n]


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
