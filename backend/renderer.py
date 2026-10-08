"""Runs Manim renders inside throwaway, locked-down Docker containers."""

import ast
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import config


class RenderError(Exception):
    def __init__(self, message: str, line: int | None = None):
        super().__init__(message)
        self.line = line


@dataclass
class Output:
    scene: str
    kind: str  # "video" or "image"
    file: str  # filename inside the job's out/ folder


@dataclass
class RenderResult:
    ok: bool
    outputs: list[Output] = field(default_factory=list)
    log: str = ""
    error: str = ""
    error_line: int | None = None


def check_code(code: str) -> None:
    """Reject code we know can't work, with a message that says why."""
    if re.search(r"^\s*(from|import)\s+manimlib\b", code, re.MULTILINE):
        raise RenderError(
            "This code is for 3Blue1Brown's ManimGL (`manimlib`). This sandbox runs Manim Community. "
            "Change the import to `from manim import *`; most other code needs small changes too."
        )


def find_scenes(code: str) -> list[str]:
    """Return the names of the renderable Scene classes, in file order.

    A class counts if it inherits from anything named *Scene (Scene, ThreeDScene,
    MovingCameraScene, ...), directly or through other classes in the same file.
    Helper base classes without a construct() are skipped when there are real scenes.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise RenderError(f"SyntaxError: {e.msg}", e.lineno) from e

    classes = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            bases = [b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", "") for b in node.bases]
            has_construct = any(
                isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "construct" for n in node.body
            )
            classes[node.name] = (bases, has_construct)

    def is_scene(name: str, seen: frozenset = frozenset()) -> bool:
        if name in seen:
            return False
        for base in classes[name][0]:
            if base in classes and base != name:
                if is_scene(base, seen | {name}):
                    return True
            elif base.endswith("Scene"):
                return True
        return False

    def has_construct(name: str, seen: frozenset = frozenset()) -> bool:
        if name in seen or name not in classes:
            return False
        bases, own = classes[name]
        return own or any(has_construct(b, seen | {name}) for b in bases)

    scenes = [name for name in classes if is_scene(name)]
    with_construct = [name for name in scenes if has_construct(name)]
    return with_construct or scenes


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


def docker_command(container_name: str, host_job_dir: str, scenes: list[str]) -> list[str]:
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
        "-e", "COLUMNS=200",
        "-v", f"{host_job_dir}:/manim",     # only the job folder is visible
        "-w", "/manim",
        config.MANIM_IMAGE,
        "manim", "render", "scene.py", *scenes,
    ]


def safe_asset_name(name: str) -> str:
    name = Path(name.replace("\\", "/")).name
    if not re.fullmatch(r"[\w][\w .\-]{0,100}", name) or name in ("scene.py", "manim.cfg"):
        raise RenderError(f"Can't use file name '{name}'. Use letters, numbers, dots, dashes or underscores.")
    return name


def render(
    job_id: str,
    code: str,
    scenes: list[str],
    quality: str,
    orientation: str,
    assets: dict[str, bytes] | None = None,
) -> RenderResult:
    job_dir = config.JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    job_dir.chmod(0o777)  # container runs as uid 1000, which may not own this folder
    (job_dir / "scene.py").write_text(code, encoding="utf-8")
    (job_dir / "manim.cfg").write_text(_manim_cfg(quality, orientation), encoding="utf-8")
    for name, data in (assets or {}).items():
        (job_dir / safe_asset_name(name)).write_bytes(data)

    per_scene = config.FINAL_TIMEOUT if quality == "final" else config.PREVIEW_TIMEOUT
    timeout = per_scene * max(1, len(scenes))
    container_name = f"manim-job-{job_id}-{uuid.uuid4().hex[:6]}"
    host_job_dir = str(Path(config.HOST_JOBS_DIR) / job_id)
    cmd = docker_command(container_name, host_job_dir, scenes)

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
        )
        log, code_ = _tail(proc.stdout + proc.stderr), proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired as e:
        subprocess.run(["docker", "kill", container_name], capture_output=True)
        log, code_, timed_out = _tail(_text(e.stdout) + _text(e.stderr)), -1, True
    except FileNotFoundError:
        return RenderResult(False, error="The docker command was not found. Is Docker installed and running?")

    outputs = _collect_outputs(job_dir, scenes)
    shutil.rmtree(job_dir / "media", ignore_errors=True)  # partial files and TeX cache

    if timed_out:
        error, line = f"Render took longer than {timeout}s and was stopped.", None
    elif code_ == 137:
        error, line = f"Render was killed for using too much memory (limit {config.MEMORY_LIMIT}).", None
    elif code_ != 0 or not outputs:
        error, line = summarize_error(log)
    else:
        return RenderResult(True, outputs, log)
    return RenderResult(False, outputs, log, error, line)


def _collect_outputs(job_dir: Path, scenes: list[str]) -> list[Output]:
    """Move each scene's finished video (or still image) into out/."""
    media, out = job_dir / "media", job_dir / "out"
    out.mkdir(exist_ok=True)
    outputs = []
    for scene in scenes:
        video = next(
            (p for p in (media / "videos").rglob(f"{scene}.mp4") if "partial_movie_files" not in p.parts), None
        )
        # Scenes without any animation are saved as a single PNG frame instead.
        image = next(iter(sorted((media / "images").rglob(f"{scene}_ManimCE_*.png"))), None)
        if video:
            shutil.move(str(video), out / f"{scene}.mp4")
            outputs.append(Output(scene, "video", f"{scene}.mp4"))
        elif image:
            shutil.move(str(image), out / f"{scene}.png")
            outputs.append(Output(scene, "image", f"{scene}.png"))
    return outputs


_EXC_LINE = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception|Exit)|KeyboardInterrupt)\b:?(.*)$")
_USER_FRAME = re.compile(r"/manim/scene\.py:(\d+)")


def summarize_error(log: str) -> tuple[str, int | None]:
    """Pull 'NameError: name x is not defined' and the user's line number out of a traceback."""
    lines = [ln.strip() for ln in log.splitlines()]
    error = ""
    for i in range(len(lines) - 1, -1, -1):
        m = _EXC_LINE.match(lines[i])
        if m:
            # Rich wraps long messages; glue the continuation lines back on.
            rest = [ln for ln in lines[i + 1 : i + 6] if ln and not ln.startswith(("[", "╭", "│", "╰"))]
            error = " ".join([lines[i], *rest]).strip()
            break
    frames = _USER_FRAME.findall(log)
    line = int(frames[-1]) if frames else None
    tex = re.search(r"LaTeX compilation error: (.*?)\s*(?:\S+\.py:\d+)?$", log, re.MULTILINE)
    if tex or "error converting to dvi" in log:
        context = next((ln[3:].strip() for ln in lines if ln.startswith("-> ")), "")
        error = "LaTeX error" + (f": {tex.group(1).strip()}" if tex else "")
        if context:
            error += f" in `{context}`"
    if not error:
        error = "Render failed. See the log below."
    if line:
        error += f"  (line {line})"
    return error, line


def _text(output: str | bytes | None) -> str:
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output or ""


def _tail(text: str, lines: int = 400) -> str:
    return "\n".join(text.splitlines()[-lines:])
