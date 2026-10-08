#!/usr/bin/env bash
# Step 1 check on macOS/Linux: renders examples/hello.py with the official Docker image.
# Usage: ./scripts/test-render.sh [quality l|m|h|k] [file] [scene]
set -euo pipefail
QUALITY="${1:-m}"; FILE="${2:-hello.py}"; SCENE="${3:-HelloScene}"
EXAMPLES="$(cd "$(dirname "$0")/../examples" && pwd)"
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$EXAMPLES:/manim" \
  manimcommunity/manim:v0.19.0 manim "-q$QUALITY" "$FILE" "$SCENE"
echo; echo "Rendered:"; find "$EXAMPLES/media/videos" -name "$SCENE.mp4" -not -path "*partial*"
