import json

import pytest

from itunes_ratings_exporter.spotify.client import (
    SAVE_TRACKS_BATCH,
    SpotifyApiError,
    SpotifyClient,
)


class FakeTransport:
    """Replays a queue of canned responses and records every request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        status, headers, payload = self.responses.pop(0)
        return status, headers, json.dumps(payload).encode("utf-8")


def ok(payload):
    return (200, {}, payload)


def client_with(responses, sleeps=None):
    transport = FakeTransport(responses)
    # Bind the caller's list directly: `sleeps or []` would swap in a throwaway
    # list whenever the caller passed an (empty, therefore falsy) one.
    recorded = [] if sleeps is None else sleeps
    return (
        SpotifyClient("tok", transport=transport, sleep=recorded.append),
        transport,
    )


def test_search_sends_a_bearer_token_and_track_type():
    client, transport = client_with([ok({"tracks": {"items": [{"uri": "spotify:track:1"}]}})])
    items = client.search_tracks("track:\"X\"")
    assert items == [{"uri": "spotify:track:1"}]
    request = transport.requests[0]
    assert request.headers["Authorization"] == "Bearer tok"
    assert "type=track" in request.full_url


def test_search_results_are_cached_within_a_run():
    client, transport = client_with([ok({"tracks": {"items": []}})])
    client.search_tracks("same query")
    client.search_tracks("same query")
    assert len(transport.requests) == 1


def test_rate_limit_is_retried_after_the_requested_delay():
    sleeps = []
    client, transport = client_with(
        [(429, {"Retry-After": "7"}, {}), ok({"tracks": {"items": []}})], sleeps
    )
    client.search_tracks("q")
    assert sleeps == [7.0]
    assert len(transport.requests) == 2


def test_server_errors_back_off_then_succeed():
    sleeps = []
    client, _ = client_with([(500, {}, {}), (503, {}, {}), ok({"id": "me"})], sleeps)
    assert client.current_user() == {"id": "me"}
    assert sleeps == [2, 4]  # exponential


def test_persistent_failure_raises_with_the_api_message():
    client, _ = client_with([(403, {}, {"error": {"message": "Insufficient scope"}})])
    with pytest.raises(SpotifyApiError) as exc:
        client.current_user()
    assert exc.value.status == 403
    assert "Insufficient scope" in str(exc.value)


def test_save_tracks_puts_ids_to_the_library():
    client, transport = client_with([ok({})])
    client.save_tracks(["id1", "id2"])
    request = transport.requests[0]
    assert request.method == "PUT"
    assert request.full_url == "https://api.spotify.com/v1/me/tracks"
    assert json.loads(request.data) == {"ids": ["id1", "id2"]}


def test_save_tracks_splits_into_api_sized_batches():
    ids = ["id{}".format(i) for i in range(SAVE_TRACKS_BATCH + 5)]
    client, transport = client_with([ok({}), ok({})])
    assert client.save_tracks(ids) == len(ids)
    assert len(transport.requests) == 2
    assert len(json.loads(transport.requests[0].data)["ids"]) == SAVE_TRACKS_BATCH
    assert len(json.loads(transport.requests[1].data)["ids"]) == 5


def test_save_tracks_with_nothing_makes_no_requests():
    client, transport = client_with([])
    assert client.save_tracks([]) == 0
    assert transport.requests == []
