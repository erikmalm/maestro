param(
    [switch]$NoBrowser,
    [ValidateRange(0, 65535)][int]$Port
)

$ErrorActionPreference = 'Stop'
$portSpecified = $PSBoundParameters.ContainsKey('Port') -or -not [string]::IsNullOrEmpty($env:MAESTRO_PORT)
if (-not $PSBoundParameters.ContainsKey('Port')) {
    $Port = 8765
    if (-not [string]::IsNullOrEmpty($env:MAESTRO_PORT)) {
        if ($env:MAESTRO_PORT -notmatch '^[0-9]{1,5}$' -or [int]$env:MAESTRO_PORT -gt 65535) {
            throw 'MAESTRO_PORT must be between 0 and 65535; 0 chooses an available port.'
        }
        $Port = [int]$env:MAESTRO_PORT
    }
}
$repoDirectory = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $repoDirectory '.venv\Scripts\python.exe'
$frontendDirectory = Join-Path $repoDirectory 'frontend'
$dataDirectory = if ($env:MAESTRO_DATA_DIR) { [IO.Path]::GetFullPath($env:MAESTRO_DATA_DIR) } else { Join-Path $env:LOCALAPPDATA 'Maestro\preview' }
$repoAbsolute = [IO.Path]::GetFullPath($repoDirectory).TrimEnd('\')
$dataAbsolute = [IO.Path]::GetFullPath($dataDirectory).TrimEnd('\')
if ($dataAbsolute -eq $repoAbsolute -or $dataAbsolute.StartsWith($repoAbsolute + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Choose MAESTRO_DATA_DIR outside the Git checkout.'
}
$identityPython = if (Test-Path -LiteralPath $pythonPath) { $pythonPath } else { 'python' }
$workspaceId = & $identityPython -c "from pathlib import Path; import hashlib, os, sys; repo = Path(sys.argv[1]).resolve(); data = Path(sys.argv[2]).expanduser().resolve(); sys.exit(1) if data == repo or repo in data.parents else None; print(hashlib.sha256(os.path.normcase(str(data)).encode()).hexdigest())" $repoDirectory $dataDirectory
if ($LASTEXITCODE -ne 0) { throw 'The resolved private data directory must be outside the Git checkout.' }
$archiveId = & $identityPython -c @'
from pathlib import Path
import hashlib, os
raw = os.environ.get('MAESTRO_CONTEXT_ARCHIVE_DIR', '')
try:
    identity = 'none'
    if raw:
        path = Path(raw).expanduser()
        identity = 'path:' + os.path.normcase(str(path.resolve())) if path.is_absolute() else 'invalid:' + raw
except (OSError, ValueError, RuntimeError):
    identity = 'invalid:' + raw
print(hashlib.sha256(identity.encode()).hexdigest())
'@
if ($LASTEXITCODE -ne 0) { throw 'Could not identify the configured source archive. Check MAESTRO_CONTEXT_ARCHIVE_DIR.' }
$previewUrl = "http://127.0.0.1:$Port"
$runtimePath = Join-Path $dataDirectory 'server.json'
$runtime = $null
if (Test-Path -LiteralPath $runtimePath) {
    try {
        if ((Get-Item -LiteralPath $runtimePath).Length -le 4096) {
            $candidate = Get-Content -Raw -LiteralPath $runtimePath | ConvertFrom-Json
            if ($candidate.port -is [int] -and $candidate.port -ge 1 -and $candidate.port -le 65535 -and
                $candidate.process_id -is [int] -and $candidate.process_id -gt 0 -and
                $candidate.launch_id -cmatch '^[a-f0-9]{32}$' -and
                $candidate.workspace_id -ceq $workspaceId) {
                $recordedPid = Join-Path $dataDirectory 'server.pid'
                $recordedProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($candidate.process_id)"
                if ((Test-Path -LiteralPath $recordedPid) -and
                    ([string](Get-Content -Raw -LiteralPath $recordedPid)).Trim() -ceq [string]$candidate.process_id -and
                    $recordedProcess -and $recordedProcess.ExecutablePath -eq $pythonPath -and
                    $recordedProcess.CommandLine -match '(?:uvicorn backend\.app:app|\-m backend\.native_server(?:\s|$))') {
                    $runtime = $candidate
                    $previewUrl = "http://127.0.0.1:$($runtime.port)"
                }
            }
        }
    } catch { } # An incomplete/stale record is never authority to stop a process.
}
try { $health = Invoke-RestMethod -Uri "$previewUrl/health" -TimeoutSec 2; $healthAvailable = $true } catch { $healthAvailable = $false }
if ($healthAvailable) {
    if ($health.application -ne 'maestro' -or $health.mode -notin @('local', 'preview')) { throw "Port $(([uri]$previewUrl).Port) is occupied by another application." }
    if ($health.workspace_id -ne $workspaceId) { throw 'Maestro is already running with a different or unverified private data directory. Stop that instance using its original MAESTRO_DATA_DIR before starting this workspace.' }
    if ($health.archive_id -ne $archiveId) { throw 'Maestro is already running with a different or unverified source archive. Stop that instance, then restart Maestro with the intended MAESTRO_CONTEXT_ARCHIVE_DIR.' }
    if ($runtime -and $health.launch_id -cne $runtime.launch_id) { throw 'The recorded Maestro address belongs to another launch. Stop the original instance before retrying.' }
    if ($runtime -and $portSpecified -and $Port -ne 0 -and $Port -ne $runtime.port) {
        throw "This workspace is already running at $previewUrl. Stop it before selecting another port."
    }
    $env:MAESTRO_PORT = [string]([uri]$previewUrl).Port
    Write-Output "Maestro is already running at $previewUrl"
    if (-not $NoBrowser) { Start-Process $previewUrl }
    return
}
$previewUrl = "http://127.0.0.1:$Port"

$pidPath = Join-Path $dataDirectory 'server.pid'

Push-Location $repoDirectory
try {
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is required.' }
    }
    $env:MAESTRO_DATA_DIR = $dataDirectory
    if (Test-Path -LiteralPath $pidPath) { & (Join-Path $repoDirectory 'scripts/stop.ps1') }
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
    $env:MAESTRO_PORT = [string]$Port
    $serverProcess = $null
    try {
        $env:MAESTRO_LAUNCH_ID = [guid]::NewGuid().ToString('N')
        $addressPath = Join-Path $dataDirectory "server-address-$($env:MAESTRO_LAUNCH_ID).json"
        $serverProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-m', 'backend.native_server', '--port', [string]$Port) -WorkingDirectory $repoDirectory -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $dataDirectory 'server.stdout.log') -RedirectStandardError (Join-Path $dataDirectory 'server.stderr.log')
        $ready = $false
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            if ($serverProcess.HasExited) { throw "Maestro could not start. Check $dataDirectory\server.stderr.log" }
            try {
                if (-not (Test-Path -LiteralPath $addressPath) -or (Get-Item -LiteralPath $addressPath).Length -gt 4096) {
                    throw 'The launched server has not reported its address.'
                }
                $address = Get-Content -Raw -LiteralPath $addressPath | ConvertFrom-Json
                if ($address.launch_id -cne $env:MAESTRO_LAUNCH_ID -or $address.port -isnot [int] -or
                    $address.port -lt 1 -or $address.port -gt 65535 -or ($Port -ne 0 -and $address.port -ne $Port)) {
                    throw 'The launched server address is not valid for this launch.'
                }
                $previewUrl = "http://127.0.0.1:$($address.port)"
                $health = Invoke-RestMethod -Uri "$previewUrl/health" -TimeoutSec 1
                if ($health.application -eq 'maestro' -and $health.mode -in @('local', 'preview') -and $health.launch_id -eq $env:MAESTRO_LAUNCH_ID -and $health.workspace_id -eq $workspaceId -and $health.archive_id -eq $archiveId -and -not $serverProcess.HasExited) { $ready = $true; break }
            } catch { }
            Start-Sleep -Milliseconds 200
        }
        if (-not $ready) { throw "Maestro did not become ready. Check $dataDirectory\server.stderr.log" }
        New-Item -ItemType File -Path $pidPath -Value $serverProcess.Id | Out-Null
        $env:MAESTRO_PORT = [string]([uri]$previewUrl).Port
        @{ port = [int]$env:MAESTRO_PORT; process_id = $serverProcess.Id; launch_id = $env:MAESTRO_LAUNCH_ID;
           workspace_id = $workspaceId; archive_id = $archiveId } | ConvertTo-Json -Compress |
            Set-Content -LiteralPath $runtimePath -Encoding UTF8
    } catch {
        $startupError = $_
        if ($serverProcess) {
            # The Windows venv launcher owns a child Python process; stop its tree.
            try {
                if (-not $serverProcess.HasExited) {
                    taskkill.exe /PID $serverProcess.Id /T /F 2>$null | Out-Null
                }
            } catch { }
            if (-not $serverProcess.HasExited) { Write-Warning "Could not stop the launched Maestro process tree (PID $($serverProcess.Id))." }
            try {
                $pidExists = Test-Path -LiteralPath $pidPath
                $ownsPidFile = $pidExists -and ([string](Get-Content -Raw -LiteralPath $pidPath)).Trim() -eq [string]$serverProcess.Id
                if ($serverProcess.HasExited -and $ownsPidFile) {
                    Remove-Item -LiteralPath $pidPath
                } elseif (-not $serverProcess.HasExited -and -not $pidExists) {
                    New-Item -ItemType File -Path $pidPath -Value $serverProcess.Id | Out-Null
                }
            } catch { Write-Warning "Could not update the launched Maestro process record (PID $($serverProcess.Id))." }
        }
        throw $startupError
    } finally {
        if ($addressPath -and (Test-Path -LiteralPath $addressPath)) { Remove-Item -LiteralPath $addressPath }
    }
    Write-Output "Maestro is running at $previewUrl"
    Write-Output 'Choose Ollama or an API provider and a model in Settings to use chat. Tasks are managed manually.'
    if (-not $NoBrowser) { Start-Process $previewUrl }
} finally { Pop-Location }
