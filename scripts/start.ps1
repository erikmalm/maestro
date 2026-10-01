param([switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
$repoDirectory = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $repoDirectory '.venv\Scripts\python.exe'
$frontendDirectory = Join-Path $repoDirectory 'frontend'
$dataDirectory = if ($env:MAESTRO_DATA_DIR) { [IO.Path]::GetFullPath($env:MAESTRO_DATA_DIR) } else { Join-Path $env:LOCALAPPDATA 'Maestro\preview' }
$repoAbsolute = [IO.Path]::GetFullPath($repoDirectory).TrimEnd('\')
$dataAbsolute = [IO.Path]::GetFullPath($dataDirectory).TrimEnd('\')
if ($dataAbsolute -eq $repoAbsolute -or $dataAbsolute.StartsWith($repoAbsolute + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Choose MAESTRO_DATA_DIR outside the Git checkout.'
}
$previewUrl = 'http://127.0.0.1:8765'
try {
    $health = Invoke-RestMethod -Uri "$previewUrl/health" -TimeoutSec 2
    if ($health.application -eq 'maestro' -and $health.mode -eq 'preview') {
        Write-Output "Maestro is already running at $previewUrl"
        if (-not $NoBrowser) { Start-Process $previewUrl }
        return
    }
    throw 'Port 8765 is occupied by another application.'
} catch {
    if ($_.Exception.Message -eq 'Port 8765 is occupied by another application.') { throw }
}

Push-Location $repoDirectory
try {
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is required.' }
    }
    & $pythonPath -c "from pathlib import Path; import sys; repo = Path(sys.argv[1]).resolve(); data = Path(sys.argv[2]).expanduser().resolve(); sys.exit(1 if data == repo or repo in data.parents else 0)" $repoDirectory $dataDirectory
    if ($LASTEXITCODE -ne 0) { throw 'The resolved private data directory must be outside the Git checkout.' }
    & $pythonPath -m pip install --quiet --disable-pip-version-check -r backend/requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Could not install backend dependencies.' }
    Push-Location $frontendDirectory
    try {
        if (-not (Test-Path -LiteralPath (Join-Path $frontendDirectory 'node_modules'))) {
            npm ci --no-fund --no-audit
            if ($LASTEXITCODE -ne 0) { throw 'Could not install frontend dependencies. Node 20.19+ or 22.12+ is required.' }
        }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw 'The frontend build failed.' }
    } finally { Pop-Location }
    New-Item -ItemType Directory -Force -Path $dataDirectory | Out-Null
    $env:MAESTRO_DATA_DIR = $dataDirectory
    $env:MAESTRO_PORT = '8765'
    $serverProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', '8765', '--no-access-log') -WorkingDirectory $repoDirectory -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $dataDirectory 'server.stdout.log') -RedirectStandardError (Join-Path $dataDirectory 'server.stderr.log')
    Set-Content -LiteralPath (Join-Path $dataDirectory 'server.pid') -Value $serverProcess.Id
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri "$previewUrl/health" -TimeoutSec 1
            if ($health.application -eq 'maestro') { $ready = $true; break }
        } catch { Start-Sleep -Milliseconds 200 }
        if ($serverProcess.HasExited) { throw "Maestro could not start. Check $dataDirectory\server.stderr.log" }
    }
    if (-not $ready) { throw "Maestro did not become ready. Check $dataDirectory\server.stderr.log" }
    Write-Output "Maestro is running at $previewUrl"
    Write-Output 'Chat/task runs are simulated. Opt-in background reflection can use paid API calls when configured.'
    if (-not $NoBrowser) { Start-Process $previewUrl }
} finally { Pop-Location }
