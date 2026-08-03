import json

import pytest

import urllib.parse

from itunes_ratings_exporter.spotify.client import (
    ADD_TRACKS_BATCH,
    MAX_SEARCH_LIMIT,
    SpotifyApiError,
    SpotifyClient,
    SpotifyQuotaError,
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
        # Pacing off: these tests assert on the exact sleeps a *response*
        # provoked, and the throttle would mix its own in. It has its own
        # tests below, driven by a fake clock.
        SpotifyClient(
            "tok", transport=transport, sleep=recorded.append, requests_per_second=0
        ),
        transport,
    )


class FakeClock:
    """A monotonic clock that only moves when something sleeps."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(round(seconds, 6))
        self.now += seconds

    def __call__(self):
        return self.now


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


def test_a_short_rate_limit_wait_is_announced():
    said = []
    transport = FakeTransport([(429, {"Retry-After": "7"}, {}), ok({"tracks": {"items": []}})])
    client = SpotifyClient("tok", transport=transport, sleep=lambda s: None, announce=said.append)
    client.search_tracks("q")
    assert "waiting 7s" in said[0]


def test_a_quota_length_wait_fails_fast_instead_of_sleeping():
    # Spotify answers a spent rolling quota with hours. That outlives the
    # access token, so sleeping through it only defers the failure.
    sleeps = []
    client, transport = client_with([(429, {"Retry-After": "10148"}, {})], sleeps)
    with pytest.raises(SpotifyQuotaError) as exc:
        client.search_tracks("q")
    assert sleeps == []
    assert len(transport.requests) == 1
    assert exc.value.retry_after == 10148.0
    assert exc.value.status == 429
    assert "2.8 hours" in str(exc.value)


def test_a_quota_error_is_still_a_spotify_api_error():
    # Callers that only catch SpotifyApiError must keep working.
    client, _ = client_with([(429, {"Retry-After": "9999"}, {})])
    with pytest.raises(SpotifyApiError):
        client.search_tracks("q")


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


def test_create_playlist_posts_to_the_me_endpoint():
    client, transport = client_with([ok({"id": "pl1"})])
    client.create_playlist("My Ratings", public=False, description="d")
    request = transport.requests[0]
    assert request.method == "POST"
    # POST /users/{id}/playlists was retired in February 2026 and now 403s.
    assert request.full_url == "https://api.spotify.com/v1/me/playlists"
    assert json.loads(request.data) == {
        "name": "My Ratings",
        "public": False,
        "description": "d",
    }


def test_add_tracks_posts_to_the_items_endpoint():
    client, transport = client_with([ok({})])
    client.add_tracks("pl1", ["spotify:track:a"])
    # /playlists/{id}/tracks was renamed to /items and the old path now 403s.
    assert transport.requests[0].full_url == "https://api.spotify.com/v1/playlists/pl1/items"


def test_add_tracks_splits_into_api_sized_batches():
    uris = ["spotify:track:{}".format(i) for i in range(ADD_TRACKS_BATCH + 5)]
    client, transport = client_with([ok({}), ok({})])
    assert client.add_tracks("pl1", uris) == len(uris)
    assert len(transport.requests) == 2
    assert len(json.loads(transport.requests[0].data)["uris"]) == ADD_TRACKS_BATCH
    assert len(json.loads(transport.requests[1].data)["uris"]) == 5


def test_search_limit_is_clamped_to_the_api_maximum():
    client, transport = client_with([ok({"tracks": {"items": []}})])
    client.search_tracks("q", limit=50)
    query = urllib.parse.parse_qs(urllib.parse.urlparse(transport.requests[0].full_url).query)
    assert query["limit"] == [str(MAX_SEARCH_LIMIT)]


def test_add_tracks_with_nothing_makes_no_requests():
    client, transport = client_with([])
    assert client.add_tracks("pl1", []) == 0
    assert transport.requests == []


def paced_client(responses, requests_per_second=2.0):
    clock = FakeClock()
    transport = FakeTransport(responses)
    client = SpotifyClient(
        "tok",
        transport=transport,
        sleep=clock.sleep,
        monotonic=clock,
        requests_per_second=requests_per_second,
        announce=lambda m: None,
    )
    return client, transport, clock


def test_requests_are_spaced_out_without_waiting_for_a_429():
    # The whole point: a spent rolling quota costs hours, so the client paces
    # itself rather than discovering the limit by hitting it.
    client, _, clock = paced_client([ok({"id": "a"}), ok({"id": "b"}), ok({"id": "c"})])
    for _ in range(3):
        client.current_user()
    # The first request goes straight out; each later one waits its turn.
    assert clock.sleeps == [0.5, 0.5]


def test_a_slow_response_counts_towards_its_own_spacing():
    # Pacing is about request *rate*, not about adding delay on top of it. If
    # a call already took longer than the interval, the next one owes nothing.
    clock = FakeClock()

    class SlowTransport(FakeTransport):
        def __call__(self, request):
            clock.now += 5.0  # the network took longer than the interval
            return super().__call__(request)

    transport = SlowTransport([ok({"id": "a"}), ok({"id": "b"})])
    client = SpotifyClient(
        "tok", transport=transport, sleep=clock.sleep, monotonic=clock, requests_per_second=2.0
    )
    client.current_user()
    client.current_user()
    assert clock.sleeps == []


def test_a_burst_limit_widens_the_pace_for_the_rest_of_the_run():
    # Getting throttled once says the pace is wrong going forward, not just
    # for the request that tripped it -- so the interval must not spring back.
    client, _, clock = paced_client(
        [(429, {"Retry-After": "3"}, {}), ok({"id": "a"}), ok({"id": "b"})]
    )
    client.current_user()
    clock.sleeps.clear()
    client.current_user()
    # 0.5s widened by 1.5 once the 429 landed.
    assert clock.sleeps == [0.75]


def test_the_widened_pace_is_announced_so_a_long_run_explains_itself():
    said = []
    clock = FakeClock()
    transport = FakeTransport([(429, {"Retry-After": "3"}, {}), ok({"id": "a"})])
    client = SpotifyClient(
        "tok",
        transport=transport,
        sleep=clock.sleep,
        monotonic=clock,
        requests_per_second=2.0,
        announce=said.append,
    )
    client.current_user()
    assert any("1.33 requests/second" in message for message in said)


def test_pacing_never_widens_past_a_floor():
    # Repeated throttling must not compound into an effectively frozen client.
    client, _, clock = paced_client(
        [(429, {"Retry-After": "1"}, {})] * 5 + [ok({"id": "a"})], requests_per_second=2.0
    )
    client.current_user()
    assert client._interval <= 10.0


def test_a_quota_error_does_not_widen_the_pace():
    # Nothing survives a spent quota, so there is no "rest of the run" to
    # slow down -- the run stops instead.
    client, _, _ = paced_client([(429, {"Retry-After": "10148"}, {})])
    before = client._interval
    with pytest.raises(SpotifyQuotaError):
        client.current_user()
    assert client._interval == before
