import csv

import pytest

from itunes_ratings_exporter.spotify.client import SpotifyApiError
from itunes_ratings_exporter.spotify.importer import (
    PENDING,
    UNAVAILABLE,
    QUEUE_FIELDS,
    ImportInputError,
    _write,
    build_queue,
    default_log_path,
    default_playlist_name,
    default_queue_path,
    playlist_from_log,
    read_log,
    read_queue,
    read_rows,
    run_import,
)

HEADER = "persistent_id,title,artist,album,rating_stars,duration_ms\n"
ROWS = (
    "1,Karma Police,Radiohead,OK Computer,5,263000\n"
    "2,Creep,Radiohead,Pablo Honey,3,238000\n"
    "3,Idioteque,Radiohead,Kid A,4,228000\n"
)


def write_csv(tmp_path, body=HEADER + ROWS, name="rated.csv"):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def spotify_track(name, artist, duration_ms, uri):
    return {
        "name": name,
        "artists": [{"name": artist}],
        "duration_ms": duration_ms,
        "uri": uri,
    }


class FakeClient:
    def __init__(self, catalogue=None, fail_on_add=False):
        self.catalogue = catalogue or {}
        self.fail_on_add = fail_on_add
        self.created = []
        self.added = []
        self.batches = []
        self.queries = []

    def search_tracks(self, query, limit=5):
        self.queries.append(query)
        for title, result in self.catalogue.items():
            if title.lower() in query.lower():
                return [result]
        return []

    def current_user(self):
        return {"id": "user1"}

    def create_playlist(self, name, public=False, description=""):
        self.created.append({"name": name, "public": public})
        return {"id": "pl1", "external_urls": {"spotify": "https://open.spotify.com/pl1"}}

    def add_tracks(self, playlist_id, uris):
        if self.fail_on_add:
            raise SpotifyApiError(502, "upstream exploded")
        self.batches.append(list(uris))
        self.added.extend(uris)
        return len(uris)


def full_catalogue():
    return {
        "Karma Police": spotify_track("Karma Police", "Radiohead", 263_000, "spotify:track:kp"),
        "Creep": spotify_track("Creep", "Radiohead", 238_000, "spotify:track:cr"),
        "Idioteque": spotify_track("Idioteque", "Radiohead", 228_000, "spotify:track:id"),
    }


def paths(tmp_path):
    csv_path = write_csv(tmp_path)
    return csv_path, default_queue_path(csv_path), default_log_path(csv_path)


def do_import(tmp_path, client, csv_path=None, min_stars=0, **kwargs):
    csv_path = csv_path or write_csv(tmp_path)
    rows = read_rows(csv_path, min_stars=min_stars)
    return run_import(
        rows, client, default_queue_path(csv_path), default_log_path(csv_path), **kwargs
    )


# --- reading the export -------------------------------------------------


def test_read_rows_filters_by_minimum_stars(tmp_path):
    rows = read_rows(write_csv(tmp_path), min_stars=4)
    assert [r["title"] for r in rows] == ["Karma Police", "Idioteque"]


def test_read_rows_with_zero_stars_keeps_everything(tmp_path):
    assert len(read_rows(write_csv(tmp_path), min_stars=0)) == 3


def test_read_rows_applies_the_limit_after_filtering(tmp_path):
    rows = read_rows(write_csv(tmp_path), min_stars=4, limit=1)
    assert [r["title"] for r in rows] == ["Karma Police"]


def test_read_rows_names_the_missing_file(tmp_path):
    with pytest.raises(ImportInputError) as exc:
        read_rows(tmp_path / "absent.csv")
    assert "absent.csv" in str(exc.value)


def test_read_rows_names_missing_required_columns(tmp_path):
    path = write_csv(tmp_path, "title,album\nSong,Album\n")
    with pytest.raises(ImportInputError) as exc:
        read_rows(path)
    assert "artist" in str(exc.value)


def test_read_rows_explains_when_ratings_are_absent(tmp_path):
    path = write_csv(tmp_path, "title,artist\nSong,Band\n")
    with pytest.raises(ImportInputError) as exc:
        read_rows(path, min_stars=4)
    assert "--min-stars 0" in str(exc.value)


def test_unrated_rows_survive_when_no_filter_is_applied(tmp_path):
    path = write_csv(tmp_path, "title,artist\nSong,Band\n")
    assert len(read_rows(path, min_stars=0)) == 1


# --- the queue ----------------------------------------------------------


def test_the_queue_is_built_from_the_export_on_the_first_run(tmp_path):
    csv_path, queue_path, log_path = paths(tmp_path)
    queue = build_queue(read_rows(csv_path, 0), queue_path, log_path)
    assert [r["title"] for r in queue] == ["Karma Police", "Creep", "Idioteque"]
    assert all(r["status"] == PENDING for r in queue)
    # Everything matching needs later has to survive into the queue.
    assert queue[0]["duration_ms"] == "263000"
    assert queue_path.is_file()


def test_the_export_itself_is_never_modified(tmp_path):
    csv_path, _, _ = paths(tmp_path)
    before = csv_path.read_text(encoding="utf-8")
    do_import(tmp_path, FakeClient(full_catalogue()), csv_path=csv_path)
    assert csv_path.read_text(encoding="utf-8") == before


def test_an_existing_queue_wins_over_the_export(tmp_path):
    csv_path, queue_path, log_path = paths(tmp_path)
    build_queue(read_rows(csv_path, 0), queue_path, log_path)
    # A second call must not reset the work already recorded.
    queue = read_queue(queue_path)
    queue[0]["status"] = "not_found"
    from itunes_ratings_exporter.spotify.importer import QUEUE_FIELDS, _write

    _write(queue_path, QUEUE_FIELDS, queue)
    reloaded = build_queue(read_rows(csv_path, 0), queue_path, log_path)
    assert reloaded[0]["status"] == "not_found"


def test_restart_rebuilds_the_queue_but_keeps_the_log(tmp_path):
    csv_path, queue_path, log_path = paths(tmp_path)
    client = FakeClient(full_catalogue())
    do_import(tmp_path, client, csv_path=csv_path)
    assert read_queue(queue_path) == []
    assert len(read_log(log_path)) == 3

    # Restarting re-queues only what is not already in Spotify -- which here
    # is nothing, so it must not import the same three tracks twice.
    second = FakeClient(full_catalogue())
    do_import(tmp_path, second, csv_path=csv_path, restart=True)
    assert second.added == []


def test_persistent_id_survives_into_the_queue_and_log(tmp_path):
    # The Spotify URI goes in its own column; overwriting persistent_id would
    # destroy the only stable key back to the iTunes library.
    csv_path, _, log_path = paths(tmp_path)
    do_import(tmp_path, FakeClient(full_catalogue()), csv_path=csv_path)
    log = read_log(log_path)
    by_title = {r["title"]: r for r in log}
    assert by_title["Karma Police"]["persistent_id"] == "1"
    assert by_title["Karma Police"]["spotify_uri"] == "spotify:track:kp"


# --- draining the queue -------------------------------------------------


def test_a_complete_run_empties_the_queue_into_the_log(tmp_path):
    csv_path, queue_path, log_path = paths(tmp_path)
    client = FakeClient(full_catalogue())
    summary = do_import(tmp_path, client, csv_path=csv_path)
    assert summary["added"] == 3
    assert summary["queued"] == 0
    assert read_queue(queue_path) == []
    assert [r["title"] for r in read_log(log_path)] == ["Karma Police", "Creep", "Idioteque"]
    assert client.added == ["spotify:track:kp", "spotify:track:cr", "spotify:track:id"]


def test_unresolved_tracks_stay_queued(tmp_path):
    csv_path, queue_path, _ = paths(tmp_path)
    catalogue = full_catalogue()
    catalogue["Creep"] = spotify_track("Creep", "Stone Temple Pilots", 238_000, "spotify:track:x")
    del catalogue["Idioteque"]
    summary = do_import(tmp_path, FakeClient(catalogue), csv_path=csv_path)

    assert (summary["added"], summary["queued"]) == (1, 2)
    assert (summary["rejected"], summary["not_found"]) == (1, 1)
    left = {r["title"]: r for r in read_queue(queue_path)}
    assert set(left) == {"Creep", "Idioteque"}
    # A rejected row keeps the near miss *and* its URI, so spotify-resolve
    # can accept it without re-searching. Status gates delivery, not the URI.
    assert left["Creep"]["spotify_artist"] == "Stone Temple Pilots"
    assert left["Creep"]["spotify_uri"] == "spotify:track:x"
    assert left["Creep"]["status"] == "rejected"
    assert left["Idioteque"]["score"] == ""


def test_re_running_retries_only_what_is_still_queued(tmp_path):
    csv_path, queue_path, _ = paths(tmp_path)
    catalogue = full_catalogue()
    catalogue["Creep"] = spotify_track("Creep", "Stone Temple Pilots", 238_000, "spotify:track:x")
    del catalogue["Idioteque"]
    do_import(tmp_path, FakeClient(catalogue), csv_path=csv_path)
    # Only Karma Police got through; the other two are still work.
    assert [r["title"] for r in read_queue(queue_path)] == ["Creep", "Idioteque"]

    second = FakeClient(full_catalogue())
    summary = do_import(tmp_path, second, csv_path=csv_path)
    assert summary["searched"] == 2  # Creep and Idioteque, not Karma Police
    assert second.added == ["spotify:track:cr", "spotify:track:id"]
    assert second.created == []  # tops up the playlist named in the log
    assert read_queue(queue_path) == []


def test_matches_are_delivered_in_api_sized_batches(tmp_path):
    body = HEADER + "".join(
        "{0},Song{0},Radiohead,Album,5,200000\n".format(i) for i in range(250)
    )
    csv_path = write_csv(tmp_path, body)

    class Everything(FakeClient):
        def search_tracks(self, query, limit=5):
            self.queries.append(query)
            title = query.split('"')[1] if '"' in query else query
            return [spotify_track(title, "Radiohead", 200_000, "spotify:track:" + title)]

    client = Everything()
    summary = do_import(tmp_path, client, csv_path=csv_path)
    assert [len(b) for b in client.batches] == [100, 100, 50]
    assert summary["added"] == 250
    assert len(client.created) == 1


def test_an_interruption_leaves_delivered_tracks_in_the_log(tmp_path):
    csv_path, queue_path, log_path = paths(tmp_path)
    client = FakeClient(full_catalogue(), fail_on_add=True)
    with pytest.raises(SpotifyApiError):
        do_import(tmp_path, client, csv_path=csv_path)
    # Nothing reached Spotify, so nothing is logged -- but the matching work
    # is checkpointed in the queue rather than thrown away.
    assert read_log(log_path) == []
    assert [r["status"] for r in read_queue(queue_path)] == ["matched", "matched", "matched"]


def test_matching_survives_when_the_add_fails_and_is_reused_next_run(tmp_path):
    csv_path, _, _ = paths(tmp_path)
    with pytest.raises(SpotifyApiError):
        do_import(tmp_path, FakeClient(full_catalogue(), fail_on_add=True), csv_path=csv_path)

    second = FakeClient(full_catalogue())
    summary = do_import(tmp_path, second, csv_path=csv_path)
    # Already matched, so the second run spends no search quota at all.
    assert second.queries == []
    assert summary["searched"] == 0
    assert summary["added"] == 3


def test_carried_over_matches_go_out_before_more_searching(tmp_path):
    csv_path, queue_path, _ = paths(tmp_path)
    catalogue = full_catalogue()
    del catalogue["Idioteque"]
    with pytest.raises(SpotifyApiError):
        do_import(
            tmp_path, FakeClient(catalogue, fail_on_add=True), csv_path=csv_path
        )

    second = FakeClient(full_catalogue())
    do_import(tmp_path, second, csv_path=csv_path)
    # Quota is scarce: deliver what is already matched, then search.
    assert second.added[0] == "spotify:track:kp"
    assert second.queries


def test_a_later_run_tops_up_the_same_playlist(tmp_path):
    csv_path, _, log_path = paths(tmp_path)
    catalogue = full_catalogue()
    del catalogue["Idioteque"]
    do_import(tmp_path, FakeClient(catalogue), csv_path=csv_path)
    assert playlist_from_log(read_log(log_path)) == "pl1"

    second = FakeClient(full_catalogue())
    summary = do_import(tmp_path, second, csv_path=csv_path)
    assert second.created == []
    assert "pl1" in summary["playlist_url"]


def test_dry_run_matches_but_touches_nothing(tmp_path):
    csv_path, queue_path, log_path = paths(tmp_path)
    client = FakeClient(full_catalogue())
    summary = do_import(tmp_path, client, csv_path=csv_path, dry_run=True)
    assert summary["added"] == 0
    assert client.created == [] and client.added == []
    assert not log_path.is_file()
    # The matching is still recorded, so a real run afterwards is free.
    assert [r["status"] for r in read_queue(queue_path)] == ["matched"] * 3


def test_no_playlist_is_created_when_nothing_matches(tmp_path):
    csv_path, _, _ = paths(tmp_path)
    client = FakeClient({})
    summary = do_import(tmp_path, client, csv_path=csv_path)
    assert summary["added"] == 0
    assert client.created == []


def test_public_flag_reaches_the_api(tmp_path):
    csv_path, _, _ = paths(tmp_path)
    client = FakeClient(full_catalogue())
    do_import(tmp_path, client, csv_path=csv_path, min_stars=5, public=True)
    assert client.created[0]["public"] is True


def test_on_track_sees_every_track_as_it_is_decided(tmp_path):
    # The per-track log is the only record left behind when a run dies mid-way,
    # so it fires for each track rather than at an interval.
    csv_path, _, _ = paths(tmp_path)
    seen = []
    do_import(
        tmp_path,
        FakeClient(full_catalogue()),
        csv_path=csv_path,
        on_track=lambda count, total, item: seen.append((count, total, item["title"])),
    )
    assert seen == [(1, 3, "Karma Police"), (2, 3, "Creep"), (3, 3, "Idioteque")]


def test_default_playlist_name_carries_the_date():
    from datetime import date

    assert default_playlist_name(date(2026, 8, 2)) == "iTunes Ratings 2026-08-02"


def test_a_resume_searches_unseen_tracks_before_retrying_near_misses(tmp_path):
    # After a spent quota, requests -- not tracks -- are the scarce resource.
    # Re-examining a recorded near miss costs the same searches as a
    # track nobody has looked at, but only the latter can add anything to the
    # playlist, so the unseen work has to go first.
    csv_path, queue_path, _ = paths(tmp_path)
    _write(
        queue_path,
        QUEUE_FIELDS,
        [
            {
                "persistent_id": "2",
                "title": "Creep",
                "artist": "Radiohead",
                "duration_ms": "238000",
                "status": "rejected",
                "score": "0.700",
            },
            {
                "persistent_id": "1",
                "title": "Karma Police",
                "artist": "Radiohead",
                "duration_ms": "263000",
                "status": PENDING,
            },
        ],
    )
    client = FakeClient(full_catalogue())
    do_import(tmp_path, client, csv_path=csv_path)
    # Searched first despite sitting second in the queue file.
    assert "Karma Police" in client.queries[0]
    assert any("Creep" in q for q in client.queries)


def test_a_tried_track_sinks_below_one_that_has_not_been_tried(tmp_path):
    # The rotation is persisted, not just applied to a run's iteration order:
    # the file itself has to come out in the order the next run should use, so
    # that a run killed by a spent quota still leaves a correct work list.
    csv_path, queue_path, _ = paths(tmp_path)
    _write(
        queue_path,
        QUEUE_FIELDS,
        [
            {
                "persistent_id": "2",
                "title": "Creep",
                "artist": "Radiohead",
                "duration_ms": "238000",
                "status": "rejected",
                "attempts": "1",
            },
            {
                "persistent_id": "3",
                "title": "Idioteque",
                "artist": "Radiohead",
                "duration_ms": "228000",
                "status": PENDING,
                "attempts": "0",
            },
        ],
    )
    # A catalogue with nothing in it, so neither track leaves the queue.
    do_import(tmp_path, FakeClient({}), csv_path=csv_path)
    left = read_queue(queue_path)
    # Idioteque was searched once and Creep twice, so Idioteque now leads.
    assert [r["persistent_id"] for r in left] == ["3", "2"]
    assert [r["attempts"] for r in left] == ["1", "2"]


def test_the_rotation_cycles_rather_than_re_attacking_the_same_head(tmp_path):
    # Three runs over a queue nothing ever matches: each run must start on the
    # track the previous run left least-tried, so the backlog rotates evenly
    # instead of burning every run's budget on the same first track.
    csv_path, queue_path, _ = paths(tmp_path)
    for _ in range(3):
        client = FakeClient({})
        do_import(tmp_path, client, csv_path=csv_path)

    left = read_queue(queue_path)
    # Nothing resolved, so all three are still queued and evenly tried.
    assert {r["attempts"] for r in left} == {"3"}
    assert len(left) == 3


def test_attempts_are_inferred_for_a_queue_written_before_the_column(tmp_path):
    # An existing queue file has no attempts column. Re-sorting it as though
    # every row were untried would send already-tried work back to the front.
    from itunes_ratings_exporter.spotify.importer import attempts, order_queue

    legacy_tried = {"title": "Creep", "status": "rejected"}
    legacy_untried = {"title": "Idioteque", "status": PENDING}
    assert attempts(legacy_tried) == 1
    assert attempts(legacy_untried) == 0
    assert [r["title"] for r in order_queue([legacy_tried, legacy_untried])] == [
        "Idioteque",
        "Creep",
    ]


# --- resolve statuses ---------------------------------------------------


def test_unavailable_rows_are_left_alone(tmp_path):
    """A human's 'not on Spotify' verdict survives any number of resumes."""
    csv_path, queue_path, _ = paths(tmp_path)
    client = FakeClient(full_catalogue())
    do_import(tmp_path, FakeClient({}), csv_path=csv_path)  # everything misses
    queue = read_queue(queue_path)
    queue[0]["status"] = UNAVAILABLE
    _write(queue_path, QUEUE_FIELDS, queue)
    summary = do_import(tmp_path, client, csv_path=csv_path)
    assert summary["unavailable"] == 1
    assert summary["searched"] == 2  # the unavailable track was not retried
    left = {r["title"]: r["status"] for r in read_queue(queue_path)}
    assert left == {queue[0]["title"]: UNAVAILABLE}


def test_rejected_rows_with_a_uri_are_not_delivered(tmp_path):
    """The URI kept for spotify-resolve must not leak into the playlist."""
    csv_path, queue_path, _ = paths(tmp_path)
    catalogue = full_catalogue()
    catalogue["Creep"] = spotify_track(
        "Creep", "Stone Temple Pilots", 238_000, "spotify:track:x"
    )
    client = FakeClient(catalogue)
    do_import(tmp_path, client, csv_path=csv_path)
    assert "spotify:track:x" not in client.added
    # ...until a resolve session flips the verdict; then a plain re-run
    # delivers it before spending any searches.
    queue = read_queue(queue_path)
    creep = next(r for r in queue if r["title"] == "Creep")
    creep["status"] = "matched"
    _write(queue_path, QUEUE_FIELDS, queue)
    resumed = FakeClient(catalogue)
    do_import(tmp_path, resumed, csv_path=csv_path)
    assert "spotify:track:x" in resumed.added
