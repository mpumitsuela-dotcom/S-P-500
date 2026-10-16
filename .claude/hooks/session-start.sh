#!/bin/bash
# Installs the Python dependencies at the start of a Claude Code on the web
# session, so the test suite (pytest, no network calls) can run before any
# change to the trading code is pushed. Does nothing on your own computer.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"
python3 -m pip install --quiet --disable-pip-version-check --root-user-action=ignore -r requirements.txt
