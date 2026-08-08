import csv

from itunes_ratings_exporter.spotify.importer import (
    PENDING,
    QUEUE_FIELDS,
    UNAVAILABLE,
    _write,
    read_queue,
)
from itunes_ratings_exporter.spotify.resolver import (
    is_tier1,
    resolvable,
    run_resolve,
    stored_scores,
)


def queue_row(**overrides):
    row = {k: "" for k in QUEUE_FIELDS}
    row.update(
        {
            "persistent_id": "1",
            "title": "Thunder Road",
            "artist": "Bruce Springsteen",
            "album": "Summer Mix 2003",
            "duration_ms": "289000",
            "status": "rejected",
            "score": "0.680",
            "spotify_uri": "spotify:track:tr",
            "spotify_title": "Thunder Road",
            "spotify_artist": "Bruce Springsteen",
            "spotify_album": "Born to Run",
            "spotify_duration_ms": "288000",
            "attempts": "1",
        }
    )
    row.update(overrides)
    return row


def write_queue(tmp_path, rows):
    path = tmp_path / "spotify_import_queue.csv"
    _write(path, QUEUE_FIELDS, rows)
    return path


class Script:
    """input()/print() doubles: scripted answers, recorded output."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.lines = []

    def input(self, prompt):
        self.lines.append(prompt)
        return self.answers.pop(0)

    def print(self, text):
        self.lines.append(text)

    @property
    def transcript(self):
        return "\n".join(self.lines)


class FakeClient:
    def __init__(self, results=None):
        self.results = results or []
        self.queries = []

    def search_tracks(self, query, limit=5):
        self.queries.append(query)
        return self.results


def no_client():
    raise AssertionError("this session should never build a client")


def resolve(queue, tmp_path, script, get_client=no_client, **kwargs):
    path = write_queue(tmp_path, queue)
    tally = run_resolve(
        queue, path, get_client, input_fn=script.input, print_fn=script.print, **kwargs
    )
    return tally, read_queue(path)


# --- classification -----------------------------------------------------


def test_close_title_and_artist_is_tier1():
    assert is_tier1(queue_row())


def test_weak_artist_is_not_tier1():
    assert not is_tier1(queue_row(spotify_artist="Melissa Etheridge"))


def test_not_found_rows_are_never_tier1():
    assert not is_tier1(queue_row(status="not_found", spotify_uri=""))


def test_rows_without_a_recorded_candidate_score_nothing():
    assert stored_scores(queue_row(spotify_uri="")) is None


def test_resolvable_skips_matched_pending_and_unavailable():
    queue = [
        queue_row(status="matched"),
        queue_row(status=PENDING),
        queue_row(status=UNAVAILABLE),
        queue_row(),
        queue_row(status="not_found", spotify_uri=""),
    ]
    assert [r["status"] for r in resolvable(queue)] == ["rejected", "not_found"]


def test_resolvable_can_revisit_unavailable():
    queue = [queue_row(status=UNAVAILABLE)]
    assert resolvable(queue, include_unavailable=True) == queue


# --- tier 1 bulk review -------------------------------------------------


def test_tier1_accept_all_promotes_to_matched(tmp_path):
    queue = [queue_row(), queue_row(persistent_id="2", title="Atlantic City",
                                    spotify_title="Atlantic City")]
    script = Script("y")
    tally, saved = resolve(queue, tmp_path, script)
    assert tally["accepted"] == 2
    assert [r["status"] for r in saved] == ["matched", "matched"]
    # The URI recorded at rejection time is what will be delivered.
    assert saved[0]["spotify_uri"] == "spotify:track:tr"


def test_tier1_skip_syntax_leaves_rows_out(tmp_path):
    queue = [queue_row(), queue_row(persistent_id="2")]
    script = Script("skip 2", "k")  # then keep the leftover in the walkthrough
    tally, saved = resolve(queue, tmp_path, script)
    assert tally["accepted"] == 1
    assert saved[0]["status"] == "matched"
    assert saved[1]["status"] == "rejected"


def test_tier1_can_be_skipped_entirely(tmp_path):
    queue = [queue_row()]
    script = Script("s", "k")
    tally, saved = resolve(queue, tmp_path, script)
    assert tally["accepted"] == 0
    assert saved[0]["status"] == "rejected"


def test_tier1_reprompts_on_nonsense(tmp_path):
    queue = [queue_row()]
    script = Script("what", "y")
    tally, _ = resolve(queue, tmp_path, script)
    assert tally["accepted"] == 1


# --- tier 2 walkthrough -------------------------------------------------


def low_scoring_row(**overrides):
    """A rejected row whose stored candidate is too different for tier 1."""
    return queue_row(
        spotify_title="Thunder", spotify_artist="Imagine Dragons",
        spotify_album="Evolve", **overrides
    )


def test_accept_key_promotes_the_stored_candidate(tmp_path):
    queue = [low_scoring_row()]
    script = Script("a")
    tally, saved = resolve(queue, tmp_path, script)
    assert tally["accepted"] == 1
    assert saved[0]["status"] == "matched"


def test_unavailable_key_is_a_durable_verdict(tmp_path):
    queue = [low_scoring_row()]
    script = Script("u")
    tally, saved = resolve(queue, tmp_path, script)
    assert tally["unavailable"] == 1
    assert saved[0]["status"] == UNAVAILABLE


def test_keep_changes_nothing(tmp_path):
    queue = [low_scoring_row()]
    script = Script("k")
    tally, saved = resolve(queue, tmp_path, script)
    assert tally["kept"] == 1
    assert saved[0]["status"] == "rejected"


def test_quit_saves_earlier_decisions(tmp_path):
    queue = [low_scoring_row(), low_scoring_row(persistent_id="2")]
    script = Script("u", "q")
    tally, saved = resolve(queue, tmp_path, script)
    assert tally["unavailable"] == 1
    assert saved[0]["status"] == UNAVAILABLE
    assert saved[1]["status"] == "rejected"


def test_not_found_rows_hide_the_accept_key(tmp_path):
    queue = [queue_row(status="not_found", spotify_uri="", spotify_title="",
                       spotify_artist="")]
    script = Script("a", "k")  # "a" must be refused, then keep
    tally, saved = resolve(queue, tmp_path, script)
    assert tally["kept"] == 1
    assert saved[0]["status"] == "not_found"
    assert "[a]ccept" not in script.transcript


def test_unavailable_rows_offer_requeue_when_revisited(tmp_path):
    queue = [queue_row(status=UNAVAILABLE)]
    script = Script("r")  # unavailable rows are never tier 1
    tally, saved = resolve(queue, tmp_path, script, include_unavailable=True)
    assert tally["requeued"] == 1
    assert saved[0]["status"] == PENDING


# --- fresh candidates ---------------------------------------------------


def spotify_track(name, artist, uri, duration_ms=288_000, album="Born to Run"):
    return {
        "name": name,
        "artists": [{"name": artist}],
        "uri": uri,
        "duration_ms": duration_ms,
        "album": {"name": album},
    }


def test_candidates_lists_choices_and_records_the_pick(tmp_path):
    client = FakeClient(
        [
            spotify_track("Thunder Road", "Bruce Springsteen", "spotify:track:new"),
            spotify_track("Thunder Road - Live", "Bruce Springsteen", "spotify:track:live"),
        ]
    )
    queue = [queue_row(status="not_found", spotify_uri="", spotify_title="",
                       spotify_artist="", spotify_album="", spotify_duration_ms="")]
    script = Script("c", "1")
    tally, saved = resolve(queue, tmp_path, script, get_client=lambda: client)
    assert tally["accepted"] == 1
    assert saved[0]["status"] == "matched"
    assert saved[0]["spotify_uri"] == "spotify:track:new"
    assert saved[0]["spotify_album"] == "Born to Run"
    assert client.queries  # the only action that searched


def test_offline_actions_never_build_a_client(tmp_path):
    queue = [low_scoring_row(), low_scoring_row(persistent_id="2")]
    script = Script("a", "u")
    resolve(queue, tmp_path, script, get_client=no_client)


def test_failed_search_keeps_the_session_alive(tmp_path):
    def broken_client():
        raise RuntimeError("quota spent")

    queue = [low_scoring_row()]
    script = Script("c", "k")
    tally, saved = resolve(queue, tmp_path, script, get_client=broken_client)
    assert tally["kept"] == 1
    assert saved[0]["status"] == "rejected"
    assert "quota spent" in script.transcript


def test_no_candidates_returns_to_the_card(tmp_path):
    queue = [low_scoring_row()]
    script = Script("c", "u")
    tally, saved = resolve(queue, tmp_path, script, get_client=lambda: FakeClient([]))
    assert tally["unavailable"] == 1


# --- old queue files ----------------------------------------------------


def test_pre_schema_queue_rows_fall_through_to_tier2(tmp_path):
    """Rows written before the candidate columns existed still resolve."""
    old_fields = [f for f in QUEUE_FIELDS
                  if f not in ("spotify_album", "spotify_duration_ms")]
    path = tmp_path / "spotify_import_queue.csv"
    row = {k: v for k, v in queue_row(spotify_uri="").items() if k in old_fields}
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=old_fields)
        writer.writeheader()
        writer.writerow(row)
    queue = read_queue(path)
    assert not is_tier1(queue[0])
    script = Script("u")
    tally = run_resolve(queue, path, no_client,
                        input_fn=script.input, print_fn=script.print)
    assert tally["unavailable"] == 1
