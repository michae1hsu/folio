$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 'Python 3.11 or newer is required.')"
if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.11+ and add it to PATH.' }
node -e 'const [major, minor] = process.versions.node.split(".").map(Number); if (!(major > 22 || major === 22 && minor >= 12 || major === 20 && minor >= 19)) process.exit(1);'
if ($LASTEXITCODE -ne 0) { throw 'Node.js 20.19+ or 22.12+ is required.' }
if (Test-Path -LiteralPath '.venv/bin/python') { throw 'Use a separate checkout for each operating system.' }
python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
$folioPython = Join-Path (Get-Location) '.venv/Scripts/python.exe'
& $folioPython -m pip install --upgrade 'pip>=26.2.0'
if ($LASTEXITCODE -ne 0) { throw 'pip security update failed.' }
& $folioPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
& $folioPython scripts/install_gitleaks.py
if ($LASTEXITCODE -ne 0) { throw 'Verified secret scanner installation failed.' }
& $folioPython -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw 'Chromium installation failed.' }
npm ci
if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' }
npm run build
if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
& $folioPython -c 'from backend.tokenizer import load_encoding; load_encoding()'
if ($LASTEXITCODE -ne 0) { throw 'Bundled tokenizer verification failed.' }
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
if (Test-Path -LiteralPath '.git') {
    git config core.hooksPath .githooks
    if ($LASTEXITCODE -ne 0) { throw 'Could not enable local secret guards.' }
}
Write-Host 'Setup complete. Configure .env and the separate Google JSON path, then run ./start.ps1.'
Write-Host 'LibreOffice and FFmpeg/ffprobe are required for Office and media conversion; see README.md.'
