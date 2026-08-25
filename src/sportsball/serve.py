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

# How stale the cached MFL state may get before a request refreshes it. The
# page polls faster than this; the interval is what protects MFL from us.
DEFAULT_REFRESH = 20.0


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
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def enabled(self) -> bool:
        return bool(self.league or self.from_dir)

    def snapshot(self) -> dict:
        """Current state, refreshed if the cached copy has gone stale."""
        if not self.enabled:
            return {"live": False}
        with self._lock:
            if time.time() - self.fetched_at >= self.refresh:
                self._refresh()
            out = dict(self.payload)
        out["live"] = True
        out["fetchedAt"] = self.fetched_at
        out["stale"] = time.time() - self.fetched_at
        if self.error:
            out["error"] = self.error
        return out

    def _refresh(self) -> None:
        try:
            notes: list[str] = []
            payloads = gather(host=self.host, year=self.year, league=self.league,
                              apikey=self.apikey, from_dir=self.from_dir,
                              on_note=notes.append)
            seed, seed_notes = build_seed_state(
                read_players(payloads.get("players")),
                read_auction(payloads.get("auctionResults")),
                read_franchises(payloads.get("league")),
                read_salary_cap(payloads.get("league")),
                read_roster_salaries(payloads.get("rosters")),
                self.me,
            )
            self.payload = {"sales": seed["sales"],
                            "openBids": seed.get("openBids", {}),
                            "teamEdits": seed["teamEdits"],
                            "notes": notes + seed_notes}
            self.error = None
        except (MFLError, OSError, ValueError) as exc:
            # Keep serving the last good state; a blip should not blank the app
            # in the middle of a draft.
            self.error = str(exc)
        finally:
            self.fetched_at = time.time()


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
        print(f"  syncing from {source} every {state.refresh:.0f}s")
        snap = state.snapshot()
        if snap.get("error"):
            print(f"  WARNING: first sync failed — {snap['error']}")
        else:
            print(f"  {len(snap.get('sales', []))} sales, "
                  f"{len(snap.get('teamEdits', {}))} franchises")
    else:
        print("  no league given, so the page keeps whatever it was built with")
    print("  ctrl-c to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
