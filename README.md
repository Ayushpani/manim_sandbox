# Manim Sandbox

A small web app for rendering [Manim](https://www.manim.community/) videos: paste code in the browser, click **Render**, get an MP4.
Each render runs in a throwaway Docker container (official `manimcommunity/manim` image) with no network and capped memory, CPU and time.

```
browser (editor + player)  ──►  FastAPI backend  ──►  docker run --rm manimcommunity/manim  ──►  output.mp4
```

| Toggle | Output |
|---|---|
| Preview + Landscape | 854×480, 15 fps (fast checks) |
| Preview + Vertical | 480×854, 15 fps |
| Final + Landscape | 1920×1080, 60 fps (YouTube) |
| Final + Vertical | 1080×1920, 60 fps (Reels / Shorts / TikTok) |

In vertical mode the frame is 8 units wide and ~14.2 tall (landscape is ~14.2 wide and 8 tall), so a `Square()` is the same size on screen either way. Stack things vertically for Reels; see `examples/vertical_short.py`.

---

## Step 1: Get Manim running (Windows)

1. Install **Docker Desktop** (https://www.docker.com/products/docker-desktop/), keep the default WSL 2 backend, and start it.
2. Pull the image (about 1 GB, once):
   ```powershell
   docker pull manimcommunity/manim:v0.19.0
   ```
3. Clone this repo and render a test video:
   ```powershell
   git clone https://github.com/ayushpani/manim_sandbox.git
   cd manim_sandbox
   .\scripts\test-render.ps1                     # examples\hello.py, 720p
   .\scripts\test-render.ps1 -Quality h -File graph.py -Scene SineWave   # 1080p60
   ```
   The video opens automatically. Files go to `examples\media\videos\`.

If PowerShell blocks the script, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

## Step 2: Run the sandbox locally

Needs Python 3.10+ (https://www.python.org/downloads/; tick "Add python.exe to PATH") and Docker Desktop running.

```powershell
.\run.ps1          # Windows
./run.sh           # macOS / Linux
```

Open **http://localhost:8000**, paste any Manim Community code, press **Render** (or Ctrl+Enter). The first run sets up a `.venv` folder.

What the page handles:
- **Any scene type:** `Scene`, `ThreeDScene`, `MovingCameraScene`, `ZoomedScene`, and your own base classes.
- **Several scenes in one file:** pick one from the dropdown, or **All scenes** to render every one and click between them.
- **Still images:** a scene with no animation (only `self.add`) shows as a PNG.
- **Your own files:** images, SVGs and sounds. Attach them with **Files…** (or drag them onto the editor), then use them by name: `ImageMobject("photo.png")`, `SVGMobject("logo.svg")`, `self.add_sound("click.wav")`.
- **Opening a `.py` file:** use **Files…** or drop it onto the editor.
- **Errors:** you see the one-line cause, e.g. `NameError: name 'grpah' is not defined (line 9)`, with the line highlighted and a link to jump to it. The full log is underneath.
- **`config` lines at the top of a file:** e.g. `config.background_color = ...`. Leftover `if __name__ == "__main__":` blocks are ignored.

`examples/gallery.py` has 17 scenes from the official Manim gallery. Paste it and pick **All scenes** to see everything working.
ManimGL code (`from manimlib import *`, the 3Blue1Brown version) is a different library and is rejected with a clear message.

To check a setup end to end, run `python tests/smoke_test.py` while the server is running. It renders every example plus the edge cases.

Alternative: run it fully in Docker with `docker compose up -d --build` (same as Step 4).

### Safety limits on each render

| Limit | Default | Env var |
|---|---|---|
| No network access | always | |
| Memory (no extra swap) | 2 GB | `MEMORY_LIMIT` |
| CPUs | 2 | `CPU_LIMIT` |
| Max processes | 256 | `PIDS_LIMIT` |
| Time limit per scene, preview / final | 120 s / 1800 s | `PREVIEW_TIMEOUT` / `FINAL_TIMEOUT` |
| Code size / attached files | 200 KB / 50 MB | `MAX_CODE_BYTES` / `MAX_ASSET_BYTES` |
| Renders at once / max waiting | 1 / 10 | `MAX_CONCURRENT_RENDERS` / `MAX_QUEUED_JOBS` |
| Videos kept for | 24 h | `JOB_TTL_HOURS` |

Containers also run read-only, as a non-root user, with all Linux capabilities dropped, and can only see their own job folder.
All settings are in `backend/config.py`.

## Step 3: Make content

- Iterate in **Preview**, then switch to **Final** for the clip you'll edit (1080p60).
- Pick **Vertical** for Reels and Shorts, **Landscape** for YouTube.
- Import the MP4s into DaVinci Resolve or CapCut for voiceover, music and captions.
  In Resolve, set the timeline to 60 fps (and 1080×1920 for vertical) before importing.
- Keep a note of features you miss while using it; build those next.

## Step 4: Deploy to an Oracle Cloud free VM

The image supports both `amd64` and `arm64`, so the free **Ampere A1** shape works (4 OCPU / 24 GB is plenty).

1. Create an Ubuntu VM on Oracle Cloud and SSH in.
2. Install Docker:
   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   sudo usermod -aG docker $USER && newgrp docker
   ```
3. Get the project and configure it:
   ```bash
   git clone https://github.com/ayushpani/manim_sandbox.git && cd manim_sandbox
   docker pull manimcommunity/manim:v0.19.0
   cp .env.example .env
   nano .env   # set ACCESS_TOKEN to a long random string, BIND_ADDR=0.0.0.0
   docker compose up -d --build
   ```
   Generate a token with `openssl rand -hex 24`. The browser asks for it once and remembers it.
4. Open port 8000 in **both** places (Oracle blocks it twice):
   - Oracle console: VCN, then Security List, then add an ingress rule for TCP 8000 from `0.0.0.0/0`.
   - On the VM (Oracle's Ubuntu image has a reject rule in iptables):
     ```bash
     sudo iptables -I INPUT 6 -p tcp --dport 8000 -j ACCEPT
     sudo netfilter-persistent save
     ```
5. Visit `http://<vm-public-ip>:8000`.

Optional: for HTTPS, point a domain at the VM and put [Caddy](https://caddyserver.com/docs/quick-starts/reverse-proxy) in front
(`caddy reverse-proxy --from yourdomain.com --to localhost:8000`, keeping `BIND_ADDR=127.0.0.1` and opening 80/443 instead of 8000).
With more cores you can raise `MAX_CONCURRENT_RENDERS` and `CPU_LIMIT` in `.env`.

Update later with `git pull && docker compose up -d --build`.

---

## Project layout

```
backend/main.py       FastAPI app: API, job queue, cleanup, serves the frontend
backend/renderer.py   Builds and runs the locked-down `docker run` for one render
backend/config.py     All limits and settings
frontend/index.html   Editor (CodeMirror), toggles, video player
examples/             Test scenes (gallery.py = 17 official gallery examples)
tests/smoke_test.py   End-to-end check against a running server
scripts/              Step 1 test-render scripts
run.ps1 / run.sh      Step 2 launchers
Dockerfile, docker-compose.yml, .env.example   Step 4 deployment
```

### API

- `POST /api/render` `{code, scenes?: [names], quality: "preview"|"final", orientation: "landscape"|"vertical", assets?: [{name, data(base64)}]}` returns `{job_id}`
- `GET /api/jobs/{id}` returns status (`queued`, `rendering`, `done`, `failed`), queue position, `outputs` (one per scene, video or image), error, error line and log
- `GET /api/jobs/{id}/files/{Scene}.mp4|png[?download=true]` returns a result file
- `GET /api/health` reports whether Docker is reachable and the image is pulled

When `ACCESS_TOKEN` is set, send it as the `X-Access-Token` header or `?token=` query parameter.

### Notes

- Jobs are kept in memory, so restarting the server forgets the job list. Old video files are still cleaned up.
- Only the standard Manim image's packages are available. Renders can't `pip install`, because they have no network.
- If the editor's CDN can't load, the page falls back to a plain text box.
