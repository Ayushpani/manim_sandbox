#!/usr/bin/env bash
# Step 2 on macOS/Linux: start the sandbox at http://localhost:8000
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r backend/requirements.txt
fi
docker image inspect manimcommunity/manim:v0.19.0 >/dev/null 2>&1 || docker pull manimcommunity/manim:v0.19.0
echo "Open http://localhost:8000"
exec .venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
