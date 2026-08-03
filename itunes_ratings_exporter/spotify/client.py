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

# Spotify accepts at most 50 track IDs per Save Tracks call.
SAVE_TRACKS_BATCH = 50

_MAX_RATE_LIMIT_RETRIES = 5
_MAX_SERVER_ERROR_RETRIES = 3


class SpotifyApiError(RuntimeError):
    """A Spotify request failed in a way retrying will not fix."""

    def __init__(self, status: int, message: str):
        super().__init__("Spotify API error {}: {}".format(status, message))
        self.status = status


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
    ):
        self._token = access_token
        self._transport = transport or urllib_transport
        self._sleep = sleep
        self._search_cache: "dict[str, list[dict]]" = {}

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
            request = urllib.request.Request(url, data=body, headers=headers, method=method)
            status, response_headers, raw = self._transport(request)
            if 200 <= status < 300:
                return json.loads(raw.decode("utf-8")) if raw else {}
            if status == 429 and rate_limit_retries < _MAX_RATE_LIMIT_RETRIES:
                rate_limit_retries += 1
                self._sleep(_retry_after(response_headers))
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
        params = urllib.parse.urlencode({"q": query, "type": "track", "limit": limit})
        data = self._request("GET", "{}/search?{}".format(API_BASE, params))
        items = data.get("tracks", {}).get("items", [])
        self._search_cache[query] = items
        return items

    def current_user(self) -> "dict[str, Any]":
        return self._request("GET", API_BASE + "/me")

    def save_tracks(self, track_ids: "list[str]") -> int:
        """Add tracks to the user's Liked Songs, batched at the API limit."""
        added = 0
        for start in range(0, len(track_ids), SAVE_TRACKS_BATCH):
            batch = track_ids[start : start + SAVE_TRACKS_BATCH]
            self._request("PUT", API_BASE + "/me/tracks", {"ids": batch})
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
