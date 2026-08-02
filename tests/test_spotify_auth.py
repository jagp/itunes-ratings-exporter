import base64
import hashlib
import json
import urllib.parse

import pytest

from itunes_ratings_exporter.spotify import auth


def token_transport(responses):
    calls = []

    def transport(request):
        calls.append(urllib.parse.parse_qs(request.data.decode("ascii")))
        status, payload = responses.pop(0)
        return status, {}, json.dumps(payload).encode("utf-8")

    transport.calls = calls
    return transport


def test_code_challenge_matches_the_s256_definition():
    verifier = "a-known-verifier-value"
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    assert auth.code_challenge(verifier) == expected
    assert "=" not in auth.code_challenge(verifier)


def test_generated_verifiers_are_unique_and_correctly_sized():
    a, b = auth.generate_verifier(), auth.generate_verifier()
    assert a != b
    assert 43 <= len(a) <= 128


def test_authorize_url_carries_pkce_and_scopes():
    url = auth.authorize_url("client123", "verifier", "state123")
    params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert params["client_id"] == ["client123"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["code_challenge"] == [auth.code_challenge("verifier")]
    assert params["state"] == ["state123"]
    assert params["redirect_uri"] == [auth.REDIRECT_URI]
    assert "playlist-modify-private" in params["scope"][0]
    # The verifier itself must never travel in the authorize request.
    assert "verifier" not in url


def test_callback_with_a_forged_state_is_rejected():
    forged = {"code": "attacker-code", "state": "wrong"}
    with pytest.raises(auth.AuthError) as exc:
        auth.validate_callback(forged, "expected")
    assert "state mismatch" in str(exc.value)


def test_callback_surfaces_a_denied_consent():
    with pytest.raises(auth.AuthError) as exc:
        auth.validate_callback({"error": "access_denied", "state": "s"}, "s")
    assert "access_denied" in str(exc.value)


def test_callback_without_a_code_is_rejected():
    with pytest.raises(auth.AuthError):
        auth.validate_callback({"state": "s"}, "s")


def test_callback_accepts_a_matching_state():
    assert auth.validate_callback({"code": "c", "state": "s"}, "s") == "c"


def test_token_cache_round_trips_and_reports_missing_files(tmp_path):
    path = tmp_path / "nested" / "token.json"
    assert auth.load_tokens(path) is None
    auth.save_tokens(path, {"access_token": "a", "refresh_token": "r", "expires_at": 1})
    assert auth.load_tokens(path)["refresh_token"] == "r"


def test_corrupt_cache_is_treated_as_absent(tmp_path):
    path = tmp_path / "token.json"
    path.write_text("{not json", encoding="utf-8")
    assert auth.load_tokens(path) is None


def test_unexpired_cached_token_is_reused_without_any_request(tmp_path):
    path = tmp_path / "token.json"
    auth.save_tokens(path, {"access_token": "cached", "refresh_token": "r", "expires_at": 5000})
    transport = token_transport([])
    assert auth.get_access_token("cid", path, transport, now=lambda: 1000) == "cached"
    assert transport.calls == []


def test_expired_token_is_refreshed_and_the_refresh_token_is_preserved(tmp_path):
    path = tmp_path / "token.json"
    auth.save_tokens(path, {"access_token": "old", "refresh_token": "r", "expires_at": 100})
    # Spotify often omits refresh_token from a refresh response.
    transport = token_transport([(200, {"access_token": "fresh", "expires_in": 3600})])
    assert auth.get_access_token("cid", path, transport, now=lambda: 1000) == "fresh"
    assert transport.calls[0]["grant_type"] == ["refresh_token"]
    assert auth.load_tokens(path)["refresh_token"] == "r"


def test_missing_client_id_explains_the_setup(tmp_path):
    with pytest.raises(auth.AuthError) as exc:
        auth.get_access_token("", tmp_path / "token.json")
    assert "developer.spotify.com" in str(exc.value)
    assert auth.REDIRECT_URI in str(exc.value)


def test_a_dead_refresh_token_is_discarded_before_re_authorizing(tmp_path, monkeypatch):
    path = tmp_path / "token.json"
    auth.save_tokens(path, {"access_token": "old", "refresh_token": "dead", "expires_at": 1})
    transport = token_transport(
        [(400, {"error": "invalid_grant"}), (200, {"access_token": "new", "expires_in": 3600})]
    )
    monkeypatch.setattr(auth, "wait_for_callback", lambda state, **kw: "the-code")
    token = auth.get_access_token(
        "cid", path, transport, open_browser=False, now=lambda: 1000, announce=lambda m: None
    )
    assert token == "new"
    assert transport.calls[1]["grant_type"] == ["authorization_code"]
    # The full flow ran, meaning the stale cache did not block re-consent.
    assert "code_verifier" in transport.calls[1]


def test_full_flow_exchanges_the_code_and_caches_the_result(tmp_path, monkeypatch):
    path = tmp_path / "token.json"
    transport = token_transport(
        [(200, {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600})]
    )
    monkeypatch.setattr(auth, "wait_for_callback", lambda state, **kw: "the-code")
    token = auth.get_access_token(
        "cid", path, transport, open_browser=False, now=lambda: 0, announce=lambda m: None
    )
    assert token == "AT"
    assert transport.calls[0]["code"] == ["the-code"]
    cached = auth.load_tokens(path)
    assert cached["refresh_token"] == "RT"
    assert cached["expires_at"] == 3600
