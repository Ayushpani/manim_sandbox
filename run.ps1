# Step 2 on Windows: start the sandbox at http://localhost:8000
# First run creates a virtual environment and installs FastAPI.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv")) {
    python -m venv .venv
    .\.venv\Scripts\python -m pip install -r backend\requirements.txt
}
docker image inspect manimcommunity/manim:v0.19.0 *> $null
if ($LASTEXITCODE -ne 0) { docker pull manimcommunity/manim:v0.19.0 }
Write-Host "Open http://localhost:8000" -ForegroundColor Green
.\.venv\Scripts\python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
