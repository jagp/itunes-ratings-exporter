"""Spotify OAuth for a desktop CLI: Authorization Code with PKCE.

A command-line tool is a public client and cannot keep a client secret, so
PKCE is the right flow -- the user registers an app and supplies only its
Client ID. Consent happens in the browser and comes back to a loopback
listener that accepts exactly one request.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Callable, Optional

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES = "playlist-modify-private playlist-modify-public"

# Spotify no longer accepts "localhost" as a redirect host; the literal
# loopback IP is required, and the port must match the registered URI.
CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORT = 8888

CONSENT_TIMEOUT_SECONDS = 300
_EXPIRY_MARGIN_SECONDS = 60

_SUCCESS_PAGE = b"""<!doctype html><meta charset="utf-8">
<title>Authorized</title>
<body style="font-family:system-ui;padding:3rem">
<h1>Spotify authorized</h1><p>You can close this tab and return to the terminal.</p>
"""
_FAILURE_PAGE = b"""<!doctype html><meta charset="utf-8">
<title>Authorization failed</title>
<body style="font-family:system-ui;padding:3rem">
<h1>Authorization failed</h1><p>Return to the terminal for details.</p>
"""


class AuthError(RuntimeError):
    """Authorization could not be completed."""


def default_cache_path() -> Path:
    return Path.home() / ".itunes-ratings-exporter" / "spotify-token.json"


def generate_verifier() -> str:
    """A high-entropy PKCE code verifier (RFC 7636 allows 43-128 chars)."""
    return secrets.token_urlsafe(64)[:128]


def code_challenge(verifier: str) -> str:
    """S256 transform: base64url(sha256(verifier)), padding stripped."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def authorize_url(client_id: str, verifier: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "code_challenge_method": "S256",
        "code_challenge": code_challenge(verifier),
    }
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    result: "dict[str, str]" = {}

    def do_GET(self):  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        type(self).result = {k: v[0] for k, v in query.items()}
        ok = "code" in type(self).result
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(_SUCCESS_PAGE if ok else _FAILURE_PAGE)

    def log_message(self, *args):
        """Silence the default stderr access log."""


def wait_for_callback(state: str, timeout: float = CONSENT_TIMEOUT_SECONDS) -> str:
    """Serve the loopback redirect once and return the authorization code.

    The ``state`` value is verified before the code is accepted, so a request
    forged by another page in the user's browser cannot inject a code.
    """
    _CallbackHandler.result = {}
    server = http.server.HTTPServer((CALLBACK_HOST, CALLBACK_PORT), _CallbackHandler)
    server.timeout = 1.0
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            server.handle_request()
            if _CallbackHandler.result:
                break
        else:
            raise AuthError(
                "Timed out after {:.0f}s waiting for Spotify authorization.".format(timeout)
            )
    finally:
        server.server_close()

    result = _CallbackHandler.result
    _CallbackHandler.result = {}
    return validate_callback(result, state)


def validate_callback(result: "dict[str, str]", state: str) -> str:
    """Check a redirect's query parameters and return the code.

    Verifying ``state`` before accepting the code is what stops another page
    in the user's browser from driving a code into our loopback listener.
    """
    if result.get("error"):
        raise AuthError("Spotify authorization denied: " + result["error"])
    if result.get("state") != state:
        raise AuthError("Authorization state mismatch; ignoring the response.")
    if "code" not in result:
        raise AuthError("Spotify redirect carried no authorization code.")
    return result["code"]


def _post_form(
    fields: "dict[str, str]", transport: "Optional[Callable[..., Any]]" = None
) -> "dict[str, Any]":
    from .client import urllib_transport

    body = urllib.parse.urlencode(fields).encode("ascii")
    request = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    status, _headers, raw = (transport or urllib_transport)(request)
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError:
        payload = {}
    if not 200 <= status < 300:
        detail = payload.get("error_description") or payload.get("error") or "unknown error"
        raise AuthError("Token request failed ({}): {}".format(status, detail))
    return payload


def exchange_code(client_id: str, code: str, verifier: str, transport=None) -> "dict[str, Any]":
    return _post_form(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": verifier,
        },
        transport,
    )


def refresh_access_token(client_id: str, refresh_token: str, transport=None) -> "dict[str, Any]":
    return _post_form(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        },
        transport,
    )


def load_tokens(path: Path) -> "Optional[dict[str, Any]]":
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_tokens(path: Path, payload: "dict[str, Any]") -> None:
    """Persist tokens readable only by the owner where the OS supports it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Best effort; Windows filesystems may not honour POSIX modes.


def _store(response: "dict[str, Any]", previous_refresh: str, now: float) -> "dict[str, Any]":
    # Spotify omits refresh_token from some refresh responses; keep the old one.
    return {
        "access_token": response.get("access_token", ""),
        "refresh_token": response.get("refresh_token") or previous_refresh,
        "expires_at": now + float(response.get("expires_in", 3600)),
    }


def get_access_token(
    client_id: str,
    cache_path: "Optional[Path]" = None,
    transport=None,
    open_browser: bool = True,
    now: "Callable[[], float]" = time.time,
    announce: "Callable[[str], None]" = print,
) -> str:
    """Return a usable access token, refreshing or re-authorizing as needed."""
    if not client_id:
        raise AuthError(
            "No Spotify client ID. Create an app at "
            "https://developer.spotify.com/dashboard, add the redirect URI "
            "{} to it, then set SPOTIFY_CLIENT_ID or pass --client-id.".format(REDIRECT_URI)
        )
    cache_path = cache_path or default_cache_path()
    cached = load_tokens(cache_path) or {}

    if cached.get("access_token") and cached.get("expires_at", 0) > now() + _EXPIRY_MARGIN_SECONDS:
        return cached["access_token"]

    if cached.get("refresh_token"):
        try:
            response = refresh_access_token(client_id, cached["refresh_token"], transport)
            tokens = _store(response, cached["refresh_token"], now())
            save_tokens(cache_path, tokens)
            return tokens["access_token"]
        except AuthError:
            # The stored grant is dead (revoked, expired, or scope changed).
            # Drop it and fall through to a fresh browser consent.
            try:
                cache_path.unlink()
            except OSError:
                pass

    verifier = generate_verifier()
    state = secrets.token_urlsafe(16)
    url = authorize_url(client_id, verifier, state)
    announce("Opening Spotify authorization in your browser...")
    announce("If it does not open, visit:\n" + url)
    if open_browser:
        webbrowser.open(url)
    code = wait_for_callback(state)
    response = exchange_code(client_id, code, verifier, transport)
    tokens = _store(response, "", now())
    if not tokens["access_token"]:
        raise AuthError("Spotify returned no access token.")
    save_tokens(cache_path, tokens)
    return tokens["access_token"]
