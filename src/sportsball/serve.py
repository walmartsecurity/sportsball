"""A local server that keeps the draft app in step with MFL.

The published app is a static page: the board and the draft state are baked in
when it is built. That is the right shape for something you open on a phone at
a table, and the wrong shape for a draft that is moving, where you would have
to rebuild after every sale.

This closes the loop. It serves the app from your own machine and polls MFL
behind it, so sales and franchise budgets arrive on their own. Two problems
solve themselves by putting the server in the middle:

* **The browser cannot call MFL directly.** A page served from a file, or from
  a published artifact, is blocked from cross-origin requests -- and the
  artifact's content policy forbids them outright. Here the page only ever
  talks to this server, which is the same origin it came from, and the server
  talks to MFL.
* **Credentials stay put.** A private league's API key is passed to this
  process and never reaches the page.

    sportsball serve --league 36570 --host www43 --me "Your Franchise"

Then open the address it prints. Without a league id it serves the app with
whatever it was built with, which is still useful for driving it by hand.

Fetching happens on a background thread, not inside a request, so the page
never waits on MFL and can poll as often as it likes. Only the endpoints that
move during a draft are refetched; the player dictionary and the franchise
list are read once. Between them that is what makes a few seconds an
affordable cycle to hold for the length of an auction.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .mfl import (
    MFLError, build_seed_state, gather, read_auction, read_franchises,
    read_players, read_roster_salaries, read_salary_cap,
)

# Seconds between MFL fetches. A refresher thread keeps to this on its own, so
# the page is never waiting on MFL -- it reads whatever the last cycle left.
DEFAULT_REFRESH = 5.0

# Of the endpoints we read, only these two move while a draft is running. The
# other three -- the player dictionary above all, which is every player in the
# league's universe -- are fetched once and reused, which is what makes a
# five-second cycle cheap enough to run for three hours.
VOLATILE = ("auctionResults", "rosters")

# After a failure, wait longer each time rather than hammering a league that is
# down or rate-limiting us. Resets on the first success.
MAX_BACKOFF = 60.0


@dataclass
class LiveState:
    """The draft as MFL last reported it."""

    league: str | None = None
    host: str = "www43"
    year: int = 2026
    apikey: str | None = None
    me: str | None = None
    from_dir: str | None = None
    refresh: float = DEFAULT_REFRESH

    payload: dict = field(default_factory=dict)
    fetched_at: float = 0.0
    error: str | None = None
    failures: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _static: dict = field(default_factory=dict, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def enabled(self) -> bool:
        return bool(self.league or self.from_dir)

    def snapshot(self) -> dict:
        """Whatever the last cycle left, without ever waiting on MFL.

        The page polls this several times a second's worth of times a minute;
        if it also had to sit through a round trip to MFL on a cache miss, a
        short refresh interval would just move the stall into the browser.
        """
        if not self.enabled:
            return {"live": False}
        with self._lock:
            out = dict(self.payload)
            fetched, error = self.fetched_at, self.error
        out["live"] = True
        out["fetchedAt"] = fetched
        out["stale"] = time.time() - fetched if fetched else None
        # The page cannot know what counts as late without knowing the cycle.
        out["refresh"] = self.refresh
        if error:
            out["error"] = error
        return out

    def start(self) -> threading.Thread | None:
        """Prime the cache, then keep it warm on a background thread."""
        if not self.enabled:
            return None
        self.refresh_once()
        thread = threading.Thread(target=self._loop, name="mfl-refresh", daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            # Back off after a failure, so a league that is down or rate
            # limiting us is not hit at full speed for the rest of the draft.
            wait = min(self.refresh * (2 ** self.failures), MAX_BACKOFF)
            if self._stop.wait(wait):
                return
            self.refresh_once()

    def refresh_once(self) -> None:
        """One fetch-and-parse cycle. Safe to call from anywhere."""
        try:
            notes: list[str] = []
            payloads = self._gather(notes.append)
            seed, seed_notes = build_seed_state(
                read_players(payloads.get("players")),
                read_auction(payloads.get("auctionResults")),
                read_franchises(payloads.get("league")),
                read_salary_cap(payloads.get("league")),
                read_roster_salaries(payloads.get("rosters")),
                self.me,
            )
            payload = {"sales": seed["sales"],
                       "openBids": seed.get("openBids", {}),
                       "teamEdits": seed["teamEdits"],
                       "notes": notes + seed_notes}
            with self._lock:
                self.payload = payload
                self.error = None
                self.failures = 0
                self.fetched_at = time.time()
        except (MFLError, OSError, ValueError) as exc:
            # Keep serving the last good state; a blip should not blank the app
            # in the middle of a draft.
            with self._lock:
                self.error = str(exc)
                self.failures += 1

    def _gather(self, note) -> dict:
        """Fetch what has changed, reuse what cannot have.

        Saved files are cheap to re-read and may have been re-dumped, so that
        path always reads everything.
        """
        if self.from_dir:
            return gather(from_dir=self.from_dir, on_note=note)
        if not self._static:
            everything = gather(host=self.host, year=self.year, league=self.league,
                                apikey=self.apikey, on_note=note)
            self._static = {k: v for k, v in everything.items() if k not in VOLATILE}
            return everything
        fresh = gather(host=self.host, year=self.year, league=self.league,
                       apikey=self.apikey, kinds=VOLATILE, on_note=note)
        return {**self._static, **fresh}


def make_handler(app_path: Path, state: LiveState):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - required by the base class
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                try:
                    body = app_path.read_bytes()
                except OSError as exc:
                    self._send(f"cannot read {app_path}: {exc}".encode(),
                               "text/plain", 500)
                    return
                self._send(body, "text/html; charset=utf-8")
            elif path == "/live":
                self._send(json.dumps(state.snapshot()).encode(),
                           "application/json")
            else:
                self._send(b"not found", "text/plain", 404)

        def log_message(self, fmt: str, *args: Any) -> None:
            # One line per sale is useful; one per poll is not.
            if "/live" not in (args[0] if args else ""):
                super().log_message(fmt, *args)

    return Handler


def serve(app_path: Path, state: LiveState, port: int = 8765,
          address: str = "127.0.0.1") -> None:
    handler = make_handler(app_path, state)
    server = ThreadingHTTPServer((address, port), handler)
    where = f"http://{address}:{server.server_address[1]}/"
    print(f"draft room serving {app_path.name} at {where}")
    if state.enabled:
        source = state.from_dir or f"MFL league {state.league} on {state.host}"
        print(f"  syncing from {source} every {state.refresh:g}s")
        state.start()
        snap = state.snapshot()
        if snap.get("error"):
            print(f"  WARNING: first sync failed — {snap['error']}")
        else:
            print(f"  {len(snap.get('sales', []))} sales, "
                  f"{len(snap.get('openBids', {}))} under bidding, "
                  f"{len(snap.get('teamEdits', {}))} franchises")
    else:
        print("  no league given, so the page keeps whatever it was built with")
    print("  ctrl-c to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        state.stop()
        server.server_close()
