"""The local server that keeps the app in step with a live auction.

The page cannot call MFL itself — a file:// page or a published artifact is
blocked from cross-origin requests — so the server it was served from is the
one thing it may talk to, and that server talks to MFL. These cover the state
machine and the two endpoints.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from sportsball.serve import LiveState, make_handler

FIXTURES = Path(__file__).parent / "fixtures" / "mfl"


@pytest.fixture
def state():
    return LiveState(from_dir=str(FIXTURES), me="Jeffrey Smar", refresh=0.0)


def test_a_server_without_a_league_reports_itself_inert():
    assert LiveState().snapshot() == {"live": False}


def test_the_snapshot_carries_the_room(state):
    snap = state.snapshot()
    assert snap["live"] is True
    assert len(snap["sales"]) == 5
    assert snap["teamEdits"]["you"]["budget"] == 700
    assert "error" not in snap


def test_a_fetch_failure_keeps_the_last_good_state(state):
    """A blip must not blank the app in the middle of a draft."""
    good = state.snapshot()
    state.from_dir = "/nonexistent/path"
    state.fetched_at = 0.0
    degraded = state.snapshot()
    assert degraded["sales"] == good["sales"]
    assert degraded["error"]


def test_the_cache_spares_the_league_a_request_per_poll():
    """The page polls faster than the refresh interval on purpose."""
    slow = LiveState(from_dir=str(FIXTURES), me="Jeffrey Smar", refresh=3600.0)
    first = slow.snapshot()
    stamp = slow.fetched_at
    slow.from_dir = "/nonexistent/path"   # would fail if it refetched
    second = slow.snapshot()
    assert slow.fetched_at == stamp
    assert second["sales"] == first["sales"]
    assert "error" not in second


@pytest.fixture
def server(state, tmp_path):
    app = tmp_path / "app.html"
    app.write_text("<title>Draft</title><p>hello</p>")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, state))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def get(url):
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.status, response.read().decode()


def test_the_root_serves_the_app(server):
    status, body = get(server + "/")
    assert status == 200
    assert "<title>Draft</title>" in body


def test_live_serves_the_room_as_json(server):
    status, body = get(server + "/live")
    assert status == 200
    data = json.loads(body)
    assert data["live"] is True
    assert len(data["sales"]) == 5
    assert "stale" in data


def test_anything_else_is_a_404(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(server + "/secrets")
    assert exc.value.code == 404


def test_the_app_is_not_cached(server):
    """A draft that shows a stale board is worse than no board."""
    with urllib.request.urlopen(server + "/", timeout=10) as response:
        assert response.headers["Cache-Control"] == "no-store"


def test_a_missing_app_file_says_so(state, tmp_path):
    missing = tmp_path / "gone.html"
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(missing, state))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            get(f"http://127.0.0.1:{httpd.server_address[1]}/")
        assert exc.value.code == 500
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_api_key_never_reaches_the_page(state, server):
    """It is passed to this process and stays here."""
    state.apikey = "SUPERSECRET"
    state.fetched_at = 0.0
    _, body = get(server + "/live")
    assert "SUPERSECRET" not in body
