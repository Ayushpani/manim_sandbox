"""FastAPI backend: receives code, queues renders, serves the finished videos."""

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
    scene: str
    quality: str
    orientation: str
    status: str = "queued"  # queued -> rendering -> done | failed
    error: str = ""
    log: str = ""
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


class RenderRequest(BaseModel):
    code: str
    scene: str = ""
    quality: Literal["preview", "final"] = "preview"
    orientation: Literal["landscape", "vertical"] = "landscape"


class ScenesRequest(BaseModel):
    code: str


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
        return {"scenes": [], "error": str(e)}


@app.post("/api/render", dependencies=[Depends(check_token)])
def submit(req: RenderRequest):
    if len(req.code.encode("utf-8")) > config.MAX_CODE_BYTES:
        raise HTTPException(413, f"Code is larger than {config.MAX_CODE_BYTES} bytes.")
    try:
        found = renderer.find_scenes(req.code)
    except renderer.RenderError as e:
        raise HTTPException(400, str(e))
    if not found:
        raise HTTPException(400, "No Scene class found. Define e.g. `class MyScene(Scene):`.")
    scene = req.scene or found[0]
    if scene not in found:
        raise HTTPException(400, f"Scene '{scene}' not found. Available: {', '.join(found)}")
    if work_queue.qsize() >= config.MAX_QUEUED_JOBS:
        raise HTTPException(429, "Too many renders waiting. Try again in a minute.")

    job = Job(uuid.uuid4().hex[:12], req.code, scene, req.quality, req.orientation)
    with jobs_lock:
        jobs[job.id] = job
    work_queue.put(job.id)
    return {"job_id": job.id, "scene": scene}


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
        "scene": job.scene,
        "quality": job.quality,
        "orientation": job.orientation,
        "queue_position": position,
        "elapsed": round((job.finished or now) - job.started, 1) if job.started else 0,
        "error": job.error,
        "log": job.log,
    }


@app.get("/api/jobs/{job_id}/video", dependencies=[Depends(check_token)])
def job_video(job_id: str, download: bool = False):
    job = _get_job(job_id)
    path = config.JOBS_DIR / job.id / "output.mp4"
    if job.status != "done" or not path.exists():
        raise HTTPException(404, "Video not ready.")
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(job.finished))
    name = f"{job.scene}_{job.quality}_{job.orientation}_{stamp}.mp4"
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=name if download else None,
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
            result = renderer.render(job.id, job.code, job.scene, job.quality, job.orientation)
            job.log, job.error = result.log, result.error
            job.status = "done" if result.ok else "failed"
        except Exception as e:  # never let one bad job kill the worker
            job.status, job.error = "failed", f"Internal error: {e}"
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
