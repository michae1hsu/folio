$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
if (-not (Test-Path -LiteralPath "$PSScriptRoot\dist\index.html")) {
    npm ci
    if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
}
$folioPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $folioPython)) { $folioPython = 'python' }
& $folioPython -m uvicorn backend.app:app --host 127.0.0.1 --port 8765
