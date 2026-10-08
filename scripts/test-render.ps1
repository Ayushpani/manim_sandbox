# Step 1 check on Windows: renders examples\hello.py with the official Docker image.
# Run from the project folder:   .\scripts\test-render.ps1
# Optional quality flag:         .\scripts\test-render.ps1 -Quality h   (l, m, h, k)
param([string]$Quality = "m", [string]$File = "hello.py", [string]$Scene = "HelloScene")
$ErrorActionPreference = "Stop"
$image = "manimcommunity/manim:v0.19.0"
$examples = Join-Path $PSScriptRoot "..\examples" | Resolve-Path

docker run --rm -v "${examples}:/manim" $image manim "-q$Quality" $File $Scene
if ($LASTEXITCODE -ne 0) { throw "Render failed (is Docker Desktop running?)" }

$video = Get-ChildItem -Path (Join-Path $examples "media\videos") -Recurse -Filter "$Scene.mp4" |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
Write-Host "`nRendered: $($video.FullName)" -ForegroundColor Green
Start-Process $video.FullName
