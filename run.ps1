# Step 2 on Windows: start the sandbox at http://localhost:8000
# First run creates a virtual environment, installs FastAPI and pulls the Manim image.
# Note: no $ErrorActionPreference = "Stop" here. Windows PowerShell 5.1 treats anything
# a program writes to stderr (docker, pip) as an error under "Stop", so we check exit codes instead.
Set-Location $PSScriptRoot
$image = "manimcommunity/manim:v0.19.0"

function Fail($msg) { Write-Host $msg -ForegroundColor Red; exit 1 }

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Setting up Python environment..." -ForegroundColor Cyan
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { Fail "Could not create .venv. Is Python installed and on PATH?" }
    .\.venv\Scripts\python -m pip install -r backend\requirements.txt
    if ($LASTEXITCODE -ne 0) { Remove-Item -Recurse -Force .venv; Fail "pip install failed (see above)." }
}

docker version --format "{{.Server.Version}}" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "Docker isn't running. Start Docker Desktop, wait until it says 'running', then run .\run.ps1 again." }

docker image inspect $image 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Downloading the Manim image (about 1 GB, first time only)..." -ForegroundColor Cyan
    docker pull $image
    if ($LASTEXITCODE -ne 0) { Fail "docker pull failed (see above)." }
}

Write-Host "`nSandbox running. Open http://localhost:8000  (Ctrl+C to stop)`n" -ForegroundColor Green
.\.venv\Scripts\python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
