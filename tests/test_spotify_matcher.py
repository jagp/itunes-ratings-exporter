from itunes_ratings_exporter.spotify.matcher import (
    MatchScore,
    accept_match,
    artist_similarity,
    duration_similarity,
    find_match,
    normalize,
    row_interpretations,
    score_candidate,
    search_queries,
    strip_variant_suffix,
    title_similarity,
)


def track(name, artists, duration_ms=240_000, uri="spotify:track:abc"):
    return {
        "name": name,
        "artists": [{"name": a} for a in artists],
        "duration_ms": duration_ms,
        "uri": uri,
    }


class FakeClient:
    """Returns canned results per query, recording what was asked."""

    def __init__(self, by_query=None, default=()):
        self.by_query = by_query or {}
        self.default = list(default)
        self.queries = []

    def search_tracks(self, query, limit=5):
        self.queries.append(query)
        return list(self.by_query.get(query, self.default))


def test_normalize_folds_case_punctuation_and_diacritics():
    assert normalize("Björk — Jóga!") == normalize("bjork joga")


def test_normalize_drops_trailing_feature_credit():
    assert normalize("Numb feat. Jay-Z") == normalize("Numb")


def test_strip_variant_suffix_removes_remaster_marker():
    assert strip_variant_suffix("Let It Be (Remastered 2009)") == "Let It Be"
    assert strip_variant_suffix("Sunday Bloody Sunday - Live") == "Sunday Bloody Sunday"


def test_strip_variant_suffix_keeps_meaningful_parentheticals():
    # "(Reprise)" names a genuinely different track, so it must survive.
    assert strip_variant_suffix("Sgt. Pepper's (Reprise)") == "Sgt. Pepper's (Reprise)"


def test_title_similarity_ignores_remaster_suffix():
    assert title_similarity("Let It Be", "Let It Be - Remastered 2009") == 1.0


def test_artist_similarity_matches_a_single_credited_artist():
    # iTunes crams collaborations into one field; Spotify splits them out.
    assert artist_similarity("Queen & David Bowie", ["Queen", "David Bowie"]) == 1.0


def test_artist_similarity_rejects_an_unrelated_artist():
    assert artist_similarity("Radiohead", ["Taylor Swift"]) < 0.4


def test_duration_similarity_scores_near_exact_and_far_apart():
    assert duration_similarity(240_000, 241_000) == 1.0
    assert duration_similarity(240_000, 300_000) == 0.0
    assert duration_similarity(None, 240_000) == 0.5  # unknown is neutral


def test_score_candidate_on_an_exact_match():
    row = {"title": "Karma Police", "artist": "Radiohead", "duration_ms": "263000"}
    score = score_candidate(row, track("Karma Police", ["Radiohead"], 263_000))
    assert score.total == 1.0


def test_accept_match_requires_both_title_and_artist_to_be_plausible():
    # A perfect title with the wrong artist must not squeak past on total
    # alone -- that is exactly the silent false positive we care about.
    wrong_artist = MatchScore(title=1.0, artist=0.2, duration=1.0, total=0.82)
    assert accept_match(wrong_artist) is False


def test_accept_match_vetoes_an_impossible_duration_gap():
    # Perfect title and artist score 0.80 on their own, which would clear the
    # threshold. A known, irreconcilable runtime gap must still veto it.
    different_recording = MatchScore(title=1.0, artist=1.0, duration=0.0, total=0.80)
    assert accept_match(different_recording) is False
    assert accept_match(different_recording, min_score=0.1) is False


def test_accept_match_honours_a_custom_threshold():
    borderline = MatchScore(title=0.8, artist=0.8, duration=0.5, total=0.70)
    assert accept_match(borderline) is False
    assert accept_match(borderline, min_score=0.65) is True


def test_search_queries_go_from_specific_to_loose():
    queries = search_queries({"title": "Let It Be (Remastered)", "artist": "The Beatles"})
    assert queries[0] == 'track:"Let It Be (Remastered)" artist:"The Beatles"'
    assert queries[1] == 'track:"Let It Be" artist:"The Beatles"'
    assert queries[-1] == "Let It Be (Remastered) The Beatles"


def test_find_match_accepts_a_clean_hit_without_trying_looser_queries():
    row = {"title": "Karma Police", "artist": "Radiohead", "duration_ms": "263000"}
    client = FakeClient(default=[track("Karma Police", ["Radiohead"], 263_000)])
    result = find_match(row, client)
    assert result.status == "matched"
    assert len(client.queries) == 1


def test_find_match_falls_through_to_a_looser_query():
    row = {"title": "Let It Be (Remastered 2009)", "artist": "The Beatles", "duration_ms": "243000"}
    strict, loose = search_queries(row)[0], search_queries(row)[1]
    client = FakeClient({strict: [], loose: [track("Let It Be", ["The Beatles"], 243_000)]})
    result = find_match(row, client)
    assert result.status == "matched"
    assert client.queries[:2] == [strict, loose]


def test_find_match_rejects_a_live_version_on_duration():
    row = {"title": "Creep", "artist": "Radiohead", "duration_ms": "238000"}
    client = FakeClient(default=[track("Creep - Live", ["Radiohead"], 310_000)])
    result = find_match(row, client)
    assert result.status == "rejected"
    # The near miss is still reported so the user can judge it themselves.
    assert result.candidate["name"] == "Creep - Live"


def test_find_match_reports_not_found_when_search_is_empty():
    row = {"title": "Obscure Demo", "artist": "Nobody", "duration_ms": "100000"}
    result = find_match(row, FakeClient())
    assert result.status == "not_found"
    assert result.candidate is None and result.score is None


def test_find_match_keeps_the_best_of_several_bad_candidates():
    row = {"title": "Hallelujah", "artist": "Jeff Buckley", "duration_ms": "413000"}
    client = FakeClient(
        default=[
            track("Completely Different Song", ["Someone Else"], 200_000),
            track("Hallelujah", ["Leonard Cohen"], 400_000),
        ]
    )
    result = find_match(row, client)
    assert result.status == "rejected"
    assert result.candidate["name"] == "Hallelujah"


# --- experience-tuned readings (cases lifted from a real resolve session) --


def test_blank_artist_is_neutral_when_title_and_duration_are_certain():
    # "Immortality" from a burned-CD playlist: artist blank, title and
    # runtime exact. Missing data must not veto what the other two signals
    # jointly prove.
    row = {"title": "Immortality", "artist": "", "duration_ms": "328000"}
    client = FakeClient(default=[track("Immortality", ["Pearl Jam"], 328_000)])
    assert find_match(row, client).status == "matched"


def test_blank_artist_does_not_accept_a_near_title():
    # The karaoke/cover trap: a decorated title within seconds of the real
    # runtime. Without artist evidence, anything short of near-certain title
    # agreement stays a reject.
    row = {"title": "Pink Moon", "artist": "", "duration_ms": "122000"}
    cover = track("Pink Moon (In the Style of Nick Drake)", ["Zoom Karaoke"], 120_000)
    assert find_match(row, FakeClient(default=[cover])).status == "rejected"


def test_blank_artist_does_not_accept_a_loose_duration():
    row = {"title": "Immortality", "artist": "", "duration_ms": "328000"}
    client = FakeClient(default=[track("Immortality", ["Pearl Jam"], 336_000)])
    assert find_match(row, client).status == "rejected"


def test_filename_title_is_parsed_into_artist_and_title():
    # "Nick Drake - Pink Moon" with a blank artist field: the parsed reading
    # searches properly and passes the full gates.
    row = {"title": "Nick Drake - Pink Moon", "artist": "", "duration_ms": "124000"}
    parsed_query = 'track:"Pink Moon" artist:"Nick Drake"'
    client = FakeClient({parsed_query: [track("Pink Moon", ["Nick Drake"], 124_000)]})
    result = find_match(row, client)
    assert result.status == "matched"
    assert parsed_query in client.queries


def test_filename_title_with_track_number_is_parsed():
    # "Mogwai - 05 - Helicon": the number segment is ripper noise.
    row = {"title": "Mogwai - 05 - Helicon", "artist": "Mogwai", "duration_ms": "360000"}
    parsed = [r for r, kind in row_interpretations(row) if kind == "parsed"]
    assert parsed and parsed[0]["title"] == "Helicon" and parsed[0]["artist"] == "Mogwai"


def test_dashed_title_with_a_different_artist_is_not_parsed():
    # "Dark & Long - Remastered" by Underworld: the dash is part of the
    # title, and the filled artist field proves it.
    row = {"title": "Dark & Long - Remastered", "artist": "Underworld"}
    assert [kind for _, kind in row_interpretations(row)] == ["original", "swapped"]


def test_swapped_fields_match_only_on_near_exact_duration():
    # Title and artist entered the wrong way round. The swap is worth one
    # search, and only a runtime agreeing almost exactly may confirm it.
    row = {"title": "Radiohead", "artist": "Karma Police", "duration_ms": "263000"}
    swap_query = 'track:"Karma Police" artist:"Radiohead"'
    client = FakeClient({swap_query: [track("Karma Police", ["Radiohead"], 263_000)]})
    assert find_match(row, client).status == "matched"

    drifted = FakeClient({swap_query: [track("Karma Police", ["Radiohead"], 273_000)]})
    assert find_match(row, drifted).status == "rejected"


def test_swap_spends_exactly_one_extra_search():
    row = {"title": "Radiohead", "artist": "Karma Police", "duration_ms": "263000"}
    client = FakeClient()  # nothing matches anywhere
    find_match(row, client)
    swap_queries = [q for q in client.queries if q == 'track:"Karma Police" artist:"Radiohead"']
    assert len(swap_queries) == 1
    assert len(client.queries) == len(set(client.queries))  # no query repeated
