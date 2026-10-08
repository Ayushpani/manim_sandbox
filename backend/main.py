"""FastAPI backend: receives code, queues renders, serves the finished videos."""

import base64
import binascii
import queue
import re
import secrets
import shutil
import subprocess
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, renderer


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    for _ in range(config.MAX_CONCURRENT_RENDERS):
        threading.Thread(target=_worker, daemon=True).start()
    threading.Thread(target=_janitor, daemon=True).start()
    yield


app = FastAPI(title="Manim Sandbox", lifespan=lifespan)


@dataclass
class Job:
    id: str
    code: str
    scenes: list[str]
    quality: str
    orientation: str
    assets: dict[str, bytes]
    status: str = "queued"  # queued -> rendering -> done | failed
    error: str = ""
    error_line: int | None = None
    log: str = ""
    outputs: list[renderer.Output] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None


jobs: dict[str, Job] = {}
jobs_lock = threading.Lock()
work_queue: "queue.Queue[str]" = queue.Queue()


def check_token(
    x_access_token: str = Header(default=""), token: str = Query(default="")
) -> None:
    if not config.ACCESS_TOKEN:
        return
    supplied = x_access_token or token
    if not secrets.compare_digest(supplied.encode(), config.ACCESS_TOKEN.encode()):
        raise HTTPException(401, "Wrong or missing access token.")


class Asset(BaseModel):
    name: str
    data: str  # base64


class RenderRequest(BaseModel):
    code: str
    scenes: list[str] = []  # empty = first scene in the file
    quality: Literal["preview", "final"] = "preview"
    orientation: Literal["landscape", "vertical"] = "landscape"
    assets: list[Asset] = []


class ScenesRequest(BaseModel):
    code: str


def _bad_code(e: renderer.RenderError) -> HTTPException:
    return HTTPException(400, {"error": str(e), "line": e.line})


@app.get("/api/health")
def health():
    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", config.MANIM_IMAGE], capture_output=True, timeout=10
        )
        docker_ok, image_ok = True, proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        docker_ok = image_ok = False
    return {
        "docker": docker_ok,
        "image": config.MANIM_IMAGE,
        "image_pulled": image_ok,
        "auth_required": bool(config.ACCESS_TOKEN),
    }


@app.post("/api/scenes", dependencies=[Depends(check_token)])
def scenes(req: ScenesRequest):
    try:
        return {"scenes": renderer.find_scenes(req.code)}
    except renderer.RenderError as e:
        return {"scenes": [], "error": str(e), "line": e.line}


@app.post("/api/render", dependencies=[Depends(check_token)])
def submit(req: RenderRequest):
    if len(req.code.encode("utf-8")) > config.MAX_CODE_BYTES:
        raise HTTPException(413, {"error": f"Code is larger than {config.MAX_CODE_BYTES} bytes."})
    try:
        renderer.check_code(req.code)
        found = renderer.find_scenes(req.code)
    except renderer.RenderError as e:
        raise _bad_code(e)
    if not found:
        raise HTTPException(400, {"error": "No Scene class found. Define one like `class MyScene(Scene):` "
                                           "with a `def construct(self):` method."})
    chosen = req.scenes or found[:1]
    missing = [s for s in chosen if s not in found]
    if missing:
        raise HTTPException(400, {"error": f"Scene '{missing[0]}' not found. Available: {', '.join(found)}"})

    assets, total = {}, 0
    for a in req.assets:
        try:
            name = renderer.safe_asset_name(a.name)
            data = base64.b64decode(a.data, validate=True)
        except renderer.RenderError as e:
            raise _bad_code(e)
        except (binascii.Error, ValueError):
            raise HTTPException(400, {"error": f"File '{a.name}' could not be read."})
        total += len(data)
        assets[name] = data
    if total > config.MAX_ASSET_BYTES:
        raise HTTPException(413, {"error": f"Attached files are larger than {config.MAX_ASSET_BYTES // 1_000_000} MB."})

    if work_queue.qsize() >= config.MAX_QUEUED_JOBS:
        raise HTTPException(429, {"error": "Too many renders waiting. Try again in a minute."})

    job = Job(uuid.uuid4().hex[:12], req.code, chosen, req.quality, req.orientation, assets)
    with jobs_lock:
        jobs[job.id] = job
    work_queue.put(job.id)
    return {"job_id": job.id, "scenes": chosen}


@app.get("/api/jobs/{job_id}", dependencies=[Depends(check_token)])
def job_status(job_id: str):
    job = _get_job(job_id)
    position = 0
    if job.status == "queued":
        with jobs_lock:
            waiting = sorted((j for j in jobs.values() if j.status == "queued"), key=lambda j: j.created)
        position = next((i + 1 for i, j in enumerate(waiting) if j.id == job.id), 0)
    now = time.time()
    return {
        "id": job.id,
        "status": job.status,
        "scenes": job.scenes,
        "quality": job.quality,
        "orientation": job.orientation,
        "queue_position": position,
        "elapsed": round((job.finished or now) - job.started, 1) if job.started else 0,
        "error": job.error,
        "error_line": job.error_line,
        "log": job.log,
        "outputs": [
            {"scene": o.scene, "kind": o.kind, "url": f"/api/jobs/{job.id}/files/{o.file}"} for o in job.outputs
        ],
    }


@app.get("/api/jobs/{job_id}/files/{name}", dependencies=[Depends(check_token)])
def job_file(job_id: str, name: str, download: bool = False):
    job = _get_job(job_id)
    output = next((o for o in job.outputs if o.file == name), None)
    path = config.JOBS_DIR / job.id / "out" / name
    if not output or not path.exists():
        raise HTTPException(404, "File not found.")
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(job.finished))
    ext = path.suffix
    return FileResponse(
        path,
        media_type="video/mp4" if ext == ".mp4" else "image/png",
        filename=f"{output.scene}_{job.quality}_{job.orientation}_{stamp}{ext}" if download else None,
        content_disposition_type="attachment" if download else "inline",
    )


def _get_job(job_id: str) -> Job:
    if not re.fullmatch(r"[0-9a-f]{12}", job_id):
        raise HTTPException(404, "No such job.")
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "No such job.")
    return job


def _worker() -> None:
    while True:
        job_id = work_queue.get()
        with jobs_lock:
            job = jobs.get(job_id)
        if job is None:
            continue
        job.status, job.started = "rendering", time.time()
        try:
            result = renderer.render(job.id, job.code, job.scenes, job.quality, job.orientation, job.assets)
            job.log, job.error, job.error_line, job.outputs = result.log, result.error, result.error_line, result.outputs
            job.status = "done" if result.ok else "failed"
        except Exception as e:  # never let one bad job kill the worker
            job.status, job.error = "failed", f"Internal error: {e}"
        job.assets = {}  # free memory; the files are on disk now
        job.finished = time.time()


def _janitor() -> None:
    """Delete jobs (and their videos) older than JOB_TTL_HOURS."""
    while True:
        cutoff = time.time() - config.JOB_TTL_HOURS * 3600
        with jobs_lock:
            old = [j.id for j in jobs.values() if j.finished and j.finished < cutoff]
            for job_id in old:
                del jobs[job_id]
        for job_id in old:
            shutil.rmtree(config.JOBS_DIR / job_id, ignore_errors=True)
        # Leftover folders from a previous run of the server.
        if config.JOBS_DIR.exists():
            for d in config.JOBS_DIR.iterdir():
                if d.is_dir() and d.name not in jobs and d.stat().st_mtime < cutoff:
                    shutil.rmtree(d, ignore_errors=True)
        time.sleep(600)


app.mount("/", StaticFiles(directory=config.ROOT / "frontend", html=True), name="frontend")
