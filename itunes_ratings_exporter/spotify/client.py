"""A small Spotify Web API client built on the standard library.

The project takes no third-party dependencies, so this wraps ``urllib``
directly. Every request goes through an injectable transport, which is what
makes the rest of the feature testable without touching the network.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional

API_BASE = "https://api.spotify.com/v1"

# Spotify accepts at most 100 track URIs per add-to-playlist call.
ADD_TRACKS_BATCH = 100

# Search used to allow 50 results per query; the February 2026 API caps it
# at 10 and rejects anything larger.
MAX_SEARCH_LIMIT = 10

_MAX_RATE_LIMIT_RETRIES = 5
_MAX_SERVER_ERROR_RETRIES = 3

# Spotify answers a short burst limit with a Retry-After of a few seconds,
# which is worth waiting out. A blown rolling quota comes back with hours --
# longer than the access token lives, so sleeping through it guarantees a 401
# on the other side. Past this threshold, stop and tell the user when to
# come back rather than appearing to hang.
MAX_RETRY_WAIT_SECONDS = 120

# Backing off only once Spotify has said 429 is already too late: the limit is
# a rolling window, so the answer to a spent one is a Retry-After measured in
# hours. Pacing every request costs minutes across a whole library and is the
# only thing that keeps a thousand-search run from reaching that wall at all.
# The trade is lopsided enough that the default errs slow.
DEFAULT_REQUESTS_PER_SECOND = 2.0

# One burst limit means the chosen pace is wrong for the rest of the run, not
# merely for the request that tripped it -- so the interval widens and stays
# widened rather than springing back.
_SLOWDOWN_FACTOR = 1.5
_MAX_INTERVAL_SECONDS = 10.0


class SpotifyApiError(RuntimeError):
    """A Spotify request failed in a way retrying will not fix."""

    def __init__(self, status: int, message: str):
        super().__init__("Spotify API error {}: {}".format(status, message))
        self.status = status


class SpotifyQuotaError(SpotifyApiError):
    """The account's rolling request quota is spent."""

    def __init__(self, retry_after: float, message: str):
        super().__init__(429, message)
        self.retry_after = retry_after


def _format_duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return "{} minutes".format(max(minutes, 1))
    return "{:.1f} hours".format(seconds / 3600.0)


def urllib_transport(request: "urllib.request.Request"):
    """Default transport: perform the request, returning status even on error."""
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()


class SpotifyClient:
    """Authenticated access to the handful of endpoints this feature needs."""

    def __init__(
        self,
        access_token: str,
        transport: "Optional[Callable[..., Any]]" = None,
        sleep: "Callable[[float], None]" = time.sleep,
        announce: "Optional[Callable[[str], None]]" = None,
        requests_per_second: float = DEFAULT_REQUESTS_PER_SECOND,
        monotonic: "Callable[[], float]" = time.monotonic,
    ):
        self._token = access_token
        self._transport = transport or urllib_transport
        self._sleep = sleep
        # Waiting silently is indistinguishable from hanging, so a caller can
        # pass ``announce`` to surface backoffs as they happen.
        self._announce = announce or (lambda message: None)
        self._search_cache: "dict[str, list[dict]]" = {}
        self._monotonic = monotonic
        # A rate of zero disables pacing outright, which is what tests about
        # retry behaviour want -- they assert on the exact sleeps a response
        # provoked, and a throttle would add its own.
        self._interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._next_request_at = 0.0

    def _pace(self) -> None:
        """Hold each request back until the chosen interval has elapsed.

        Deliberately blind to how long the request itself took: the clock is
        read again afterwards, so a slow response counts toward its own
        spacing and only genuinely-too-fast calls wait.
        """
        if self._interval <= 0.0:
            return
        now = self._monotonic()
        wait = self._next_request_at - now
        if wait > 0.0:
            self._sleep(wait)
            now += wait
        self._next_request_at = now + self._interval

    def _slow_down(self) -> None:
        """Widen the pace permanently after Spotify signals a burst limit."""
        if self._interval <= 0.0:
            return
        widened = min(self._interval * _SLOWDOWN_FACTOR, _MAX_INTERVAL_SECONDS)
        if widened > self._interval:
            self._interval = widened
            self._announce(
                "Easing off to {:.2f} requests/second for the rest of the run".format(
                    1.0 / widened
                )
            )

    def _request(
        self,
        method: str,
        url: str,
        payload: "Optional[dict[str, Any]]" = None,
    ) -> "dict[str, Any]":
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Authorization": "Bearer " + self._token,
            "Content-Type": "application/json",
        }
        rate_limit_retries = 0
        server_error_retries = 0
        while True:
            self._pace()
            request = urllib.request.Request(url, data=body, headers=headers, method=method)
            status, response_headers, raw = self._transport(request)
            if 200 <= status < 300:
                return json.loads(raw.decode("utf-8")) if raw else {}
            if status == 429:
                wait = _retry_after(response_headers)
                if wait > MAX_RETRY_WAIT_SECONDS:
                    # A wait this long is a spent rolling quota, not a burst
                    # limit. It outlives the access token, so sleeping it off
                    # only trades a visible failure now for a 401 later.
                    raise SpotifyQuotaError(
                        wait,
                        "request quota exhausted; Spotify asks for {} before "
                        "retrying. Nothing further will succeed until then.".format(
                            _format_duration(wait)
                        ),
                    )
                if rate_limit_retries < _MAX_RATE_LIMIT_RETRIES:
                    rate_limit_retries += 1
                    self._announce(
                        "Rate limited; waiting {:.0f}s (retry {}/{})".format(
                            wait, rate_limit_retries, _MAX_RATE_LIMIT_RETRIES
                        )
                    )
                    self._sleep(wait)
                    # Being throttled at all means the pace was too fast.
                    self._slow_down()
                    continue
            if status >= 500 and server_error_retries < _MAX_SERVER_ERROR_RETRIES:
                server_error_retries += 1
                self._sleep(2 ** server_error_retries)
                continue
            raise SpotifyApiError(status, _error_message(raw))

    def search_tracks(self, query: str, limit: int = 5) -> "list[dict[str, Any]]":
        """Search the track catalogue, memoizing repeats within this run."""
        if query in self._search_cache:
            return self._search_cache[query]
        params = urllib.parse.urlencode(
            {"q": query, "type": "track", "limit": min(limit, MAX_SEARCH_LIMIT)}
        )
        data = self._request("GET", "{}/search?{}".format(API_BASE, params))
        items = data.get("tracks", {}).get("items", [])
        self._search_cache[query] = items
        return items

    def current_user(self) -> "dict[str, Any]":
        return self._request("GET", API_BASE + "/me")

    def create_playlist(
        self, name: str, public: bool = False, description: str = ""
    ) -> "dict[str, Any]":
        """Create a playlist for the authenticated user.

        The February 2026 API retired ``POST /users/{id}/playlists`` in favour
        of ``POST /me/playlists``; the old path answers 403 rather than 404,
        which makes calling it look like a permissions problem.
        """
        return self._request(
            "POST",
            API_BASE + "/me/playlists",
            {"name": name, "public": public, "description": description},
        )

    def add_tracks(self, playlist_id: str, uris: "list[str]") -> int:
        """Add URIs in API-sized batches. Returns how many were sent.

        ``/playlists/{id}/tracks`` became ``/playlists/{id}/items`` in the
        February 2026 API, and the old path now answers 403.
        """
        added = 0
        for start in range(0, len(uris), ADD_TRACKS_BATCH):
            batch = uris[start : start + ADD_TRACKS_BATCH]
            self._request(
                "POST",
                "{}/playlists/{}/items".format(API_BASE, urllib.parse.quote(playlist_id)),
                {"uris": batch},
            )
            added += len(batch)
        return added


def _retry_after(headers: "dict[str, Any]") -> float:
    for key, value in (headers or {}).items():
        if key.lower() == "retry-after":
            try:
                return float(value)
            except (TypeError, ValueError):
                break
    return 1.0


def _error_message(raw: bytes) -> str:
    try:
        payload = json.loads(raw.decode("utf-8"))
        return payload.get("error", {}).get("message") or raw.decode("utf-8")
    except Exception:
        return (raw or b"").decode("utf-8", "replace")[:200]
