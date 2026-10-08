# Step 1 check on Windows: renders examples\hello.py with the official Docker image.
# Run from the project folder:   .\scripts\test-render.ps1
# Optional quality flag:         .\scripts\test-render.ps1 -Quality h   (l, m, h, k)
param([string]$Quality = "m", [string]$File = "hello.py", [string]$Scene = "HelloScene")
# No $ErrorActionPreference = "Stop": manim logs to stderr, which Windows PowerShell 5.1
# would treat as a fatal error. Exit codes are checked instead.
$image = "manimcommunity/manim:v0.19.0"
$examples = (Resolve-Path (Join-Path $PSScriptRoot "..\examples")).Path

docker run --rm -v "${examples}:/manim" $image manim "-q$Quality" $File $Scene
if ($LASTEXITCODE -ne 0) { Write-Host "Render failed (is Docker Desktop running?)" -ForegroundColor Red; exit 1 }

$video = Get-ChildItem -Path (Join-Path $examples "media\videos") -Recurse -Filter "$Scene.mp4" |
    Where-Object { $_.FullName -notmatch "partial_movie_files" } |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
Write-Host "`nRendered: $($video.FullName)" -ForegroundColor Green
Start-Process $video.FullName
