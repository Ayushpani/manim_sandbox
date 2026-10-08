"""Runs one Manim render inside a throwaway, locked-down Docker container."""

import ast
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import config


class RenderError(Exception):
    pass


@dataclass
class RenderResult:
    ok: bool
    video: Path | None
    log: str
    error: str = ""


def find_scenes(code: str) -> list[str]:
    """Return the names of classes that look like Manim scenes, in file order."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise RenderError(f"Python syntax error on line {e.lineno}: {e.msg}") from e
    scenes = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
            if name.endswith("Scene"):
                scenes.append(node.name)
                break
    return scenes


def _manim_cfg(quality: str, orientation: str) -> str:
    width, height, fps = config.RESOLUTIONS[(quality, orientation)]
    # Keep the short side at 8 scene units in both orientations, so a Square()
    # is the same size on screen whether you render for YouTube or Reels.
    if orientation == "vertical":
        frame_w, frame_h = 8.0, 8.0 * height / width
    else:
        frame_w, frame_h = 8.0 * width / height, 8.0
    return (
        "[CLI]\n"
        f"pixel_width = {width}\n"
        f"pixel_height = {height}\n"
        f"frame_rate = {fps}\n"
        f"frame_width = {frame_w:.6f}\n"
        f"frame_height = {frame_h:.6f}\n"
        "progress_bar = none\n"
    )


def docker_command(container_name: str, host_job_dir: str, scene: str) -> list[str]:
    return [
        "docker", "run", "--rm",
        "--name", container_name,
        "--network", "none",                # no internet access from user code
        "--memory", config.MEMORY_LIMIT,
        "--memory-swap", config.MEMORY_LIMIT,  # no extra swap beyond the limit
        "--cpus", config.CPU_LIMIT,
        "--pids-limit", str(config.PIDS_LIMIT),  # stops fork bombs
        "--read-only",                      # image filesystem can't be modified
        "--tmpfs", f"/tmp:rw,size={config.TMPFS_SIZE}",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", "1000:1000",
        "-e", "HOME=/tmp",
        "-e", "COLUMNS=120",
        "-v", f"{host_job_dir}:/manim",     # only the job folder is visible
        "-w", "/manim",
        config.MANIM_IMAGE,
        "manim", "render", "-o", "output", "scene.py", scene,
    ]


def render(job_id: str, code: str, scene: str, quality: str, orientation: str) -> RenderResult:
    job_dir = config.JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    job_dir.chmod(0o777)  # container runs as uid 1000, which may not own this folder
    (job_dir / "scene.py").write_text(code, encoding="utf-8")
    (job_dir / "manim.cfg").write_text(_manim_cfg(quality, orientation), encoding="utf-8")

    timeout = config.FINAL_TIMEOUT if quality == "final" else config.PREVIEW_TIMEOUT
    container_name = f"manim-job-{job_id}-{uuid.uuid4().hex[:6]}"
    host_job_dir = str(Path(config.HOST_JOBS_DIR) / job_id)
    cmd = docker_command(container_name, host_job_dir, scene)

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
        )
    except subprocess.TimeoutExpired as e:
        subprocess.run(["docker", "kill", container_name], capture_output=True)
        log = _tail(_text(e.stdout) + _text(e.stderr))
        return RenderResult(False, None, log, f"Render took longer than {timeout}s and was stopped.")
    except FileNotFoundError:
        return RenderResult(False, None, "", "The docker command was not found. Is Docker installed and running?")

    log = _tail(proc.stdout + proc.stderr)
    media = job_dir / "media"
    videos = [p for p in media.rglob("output.mp4") if "partial_movie_files" not in p.parts]
    if proc.returncode != 0 or not videos:
        if proc.returncode == 137:
            error = f"Render was killed (out of memory? limit is {config.MEMORY_LIMIT})."
        else:
            error = "Render failed. See the log for the Python traceback."
        return RenderResult(False, None, log, error)

    final = job_dir / "output.mp4"
    shutil.move(str(videos[0]), final)
    shutil.rmtree(media, ignore_errors=True)  # partial files and TeX cache
    return RenderResult(True, final, log)


def _text(output: str | bytes | None) -> str:
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output or ""


def _tail(text: str, lines: int = 300) -> str:
    return "\n".join(text.splitlines()[-lines:])
