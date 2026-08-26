#!/usr/bin/env bash
# Set up and start the draft room in one go.
#
#   ./draft.sh --league 36570 --host www43 --me "Your Franchise"
#
# Everything after ./draft.sh is passed through to `sportsball serve`, so
# --apikey, --refresh, --port and --from-dir all work here too. Safe to re-run:
# it reuses the virtualenv and rebuilds the board each time.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
  echo "need Python 3.10 or newer; found: $("$PY" --version 2>&1 || echo none)" >&2
  echo "install it from https://www.python.org/downloads/ and try again" >&2
  exit 1
fi

if [ ! -d .venv ]; then
  echo "==> creating .venv"
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> installing"
pip install --quiet --upgrade pip
pip install --quiet -e '.[solver]'

echo "==> building the board"
python tools/build_app.py --out app.html

echo "==> starting"
exec sportsball serve "$@"
