# Builds the Windows executable and installer.
# Requirements: Python 3.12+ (64-bit), Inno Setup 6 (ISCC.exe on PATH or default location), internet access.
# Usage (PowerShell):  .\packaging\build_windows.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "==> Creating virtual environment"
if (-not (Test-Path .venv)) { py -3 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

Write-Host "==> Running tests"
.\.venv\Scripts\python.exe -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Tests failed - aborting build" }

Write-Host "==> Generating icon"
.\.venv\Scripts\python.exe packaging\make_icon.py

Write-Host "==> PyInstaller"
# Prefer Windows DLLs (notably Qt's system ICU) over unrelated tools on PATH.
$env:PATH = "$env:SystemRoot\System32;$env:PATH"
.\.venv\Scripts\pyinstaller.exe packaging\dogsapka.spec --noconfirm --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed - aborting build" }
$tools = "dist\DOGSAPKA AI CLIPPER\tools"
New-Item -ItemType Directory -Force $tools | Out-Null

Write-Host "==> Bundling FFmpeg (GPL build from gyan.dev, includes NVENC + libass)"
$ff = "build\ffmpeg.zip"
if (-not (Test-Path $ff)) { Invoke-WebRequest "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile $ff }
Expand-Archive $ff -DestinationPath build\ffmpeg -Force
$bin = Get-ChildItem build\ffmpeg -Recurse -Filter ffmpeg.exe | Select-Object -First 1
Copy-Item $bin.FullName $tools
Copy-Item (Join-Path $bin.DirectoryName "ffprobe.exe") $tools

Write-Host "==> Bundling Streamlink (portable)"
$rel = Invoke-RestMethod "https://api.github.com/repos/streamlink/windows-builds/releases/latest"
$asset = $rel.assets | Where-Object { $_.name -like "*x86_64.zip" -and $_.name -notlike "*py3*-*-*-*" } | Select-Object -First 1
if (-not $asset) { $asset = $rel.assets | Where-Object { $_.name -like "*x86_64.zip" } | Select-Object -First 1 }
Invoke-WebRequest $asset.browser_download_url -OutFile build\streamlink.zip
Expand-Archive build\streamlink.zip -DestinationPath build\streamlink -Force
$sl = Get-ChildItem build\streamlink -Directory | Select-Object -First 1
Copy-Item $sl.FullName "$tools\streamlink" -Recurse -Force

Write-Host "==> Building installer"
$iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) { $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $iscc)) { $iscc = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $iscc)) { throw "Inno Setup 6 not found" }
$version = (.\.venv\Scripts\python.exe -c "import dogsapka_clipper; print(dogsapka_clipper.__version__)").Trim()
Write-Host "Version $version"
# antivirus scanners sometimes lock the new Setup.exe for a moment ("EndUpdateResource failed"): retry
$ok = $false
foreach ($attempt in 1..3) {
    & $iscc "/DAppVersion=$version" packaging\installer.iss
    if ($LASTEXITCODE -eq 0) { $ok = $true; break }
    Write-Host "Installer compile failed (attempt $attempt) - retrying in 10 s"
    Start-Sleep -Seconds 10
}
if (-not $ok) { throw "Inno Setup failed - see the messages above" }
Write-Host "Done: dist\installer\"
