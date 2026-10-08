"""Settings, all overridable with environment variables."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


# Docker image used for every render. Pin a version so renders are reproducible.
MANIM_IMAGE = os.environ.get("MANIM_IMAGE", "manimcommunity/manim:v0.19.0")

# Where job folders live, as seen by this backend process.
JOBS_DIR = Path(os.environ.get("JOBS_DIR", ROOT / "jobs")).resolve()
# The same folder as seen by the Docker daemon. Only differs when the backend
# itself runs inside a container (see docker-compose.yml).
HOST_JOBS_DIR = os.environ.get("HOST_JOBS_DIR", str(JOBS_DIR))

# Safety limits for each throwaway render container.
MEMORY_LIMIT = os.environ.get("MEMORY_LIMIT", "2g")
CPU_LIMIT = os.environ.get("CPU_LIMIT", "2")
PIDS_LIMIT = _int("PIDS_LIMIT", 256)
TMPFS_SIZE = os.environ.get("TMPFS_SIZE", "512m")
PREVIEW_TIMEOUT = _int("PREVIEW_TIMEOUT", 120)  # seconds
FINAL_TIMEOUT = _int("FINAL_TIMEOUT", 900)  # seconds
MAX_CODE_BYTES = _int("MAX_CODE_BYTES", 100_000)
MAX_CONCURRENT_RENDERS = _int("MAX_CONCURRENT_RENDERS", 1)
MAX_QUEUED_JOBS = _int("MAX_QUEUED_JOBS", 10)
JOB_TTL_HOURS = _int("JOB_TTL_HOURS", 24)

# Optional shared password. Leave empty on your laptop; set it before exposing
# the sandbox to the internet.
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN", "")

# (pixel_width, pixel_height, fps) per quality/orientation.
RESOLUTIONS = {
    ("preview", "landscape"): (854, 480, 15),
    ("preview", "vertical"): (480, 854, 15),
    ("final", "landscape"): (1920, 1080, 60),
    ("final", "vertical"): (1080, 1920, 60),
}
