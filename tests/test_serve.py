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
    """Primed once, the way start() primes it, but with no thread running."""
    live = LiveState(from_dir=str(FIXTURES), me="Jeffrey Smar", refresh=3600.0)
    live.refresh_once()
    return live


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
    state.refresh_once()
    degraded = state.snapshot()
    assert degraded["sales"] == good["sales"]
    assert degraded["error"]


def test_a_snapshot_never_waits_on_the_league(state):
    """The page polls several times per sync cycle on purpose.

    If a poll could trigger a fetch, a short refresh interval would just move
    the stall from the server into the browser.
    """
    first = state.snapshot()
    stamp = state.fetched_at
    state.from_dir = "/nonexistent/path"   # would fail if it refetched
    for _ in range(5):
        assert state.snapshot()["sales"] == first["sales"]
    assert state.fetched_at == stamp
    assert "error" not in state.snapshot()


def test_failures_back_off_instead_of_hammering_a_league_that_is_down(state):
    state.from_dir = "/nonexistent/path"
    for expected in (1, 2, 3):
        state.refresh_once()
        assert state.failures == expected
    state.from_dir = str(FIXTURES)
    state.refresh_once()
    assert state.failures == 0
    assert state.snapshot().get("error") is None


def test_the_static_endpoints_are_fetched_once(monkeypatch):
    """The player dictionary is the whole league's universe and cannot change
    mid-draft; refetching it every few seconds is what would make a fast cycle
    expensive."""
    from sportsball import serve as serve_mod

    calls = []

    def fake_gather(**kwargs):
        calls.append(kwargs.get("kinds"))
        return {k: json.loads((FIXTURES / f"{k}.json").read_text())
                for k in ("league", "players", "auctionResults", "rosters")
                if (FIXTURES / f"{k}.json").exists()}

    monkeypatch.setattr(serve_mod, "gather", fake_gather)
    live = LiveState(league="36570", me="Jeffrey Smar", refresh=3600.0)
    live.refresh_once()
    live.refresh_once()
    live.refresh_once()
    assert calls[0] is None                      # first cycle takes everything
    assert all(c == serve_mod.VOLATILE for c in calls[1:])


def test_the_page_is_told_the_sync_cycle(state):
    """It cannot judge what counts as stale without knowing the interval."""
    assert state.snapshot()["refresh"] == state.refresh


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
    state.refresh_once()
    _, body = get(server + "/live")
    assert "SUPERSECRET" not in body


def test_serving_a_missing_app_stops_before_it_starts(state, tmp_path):
    """Otherwise the server comes up and 500s on every page load instead."""
    from sportsball.serve import serve

    with pytest.raises(SystemExit) as exc:
        serve(tmp_path / "not-built.html", state, port=0)
    assert "build_app.py" in str(exc.value)
