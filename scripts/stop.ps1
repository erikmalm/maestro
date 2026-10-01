$ErrorActionPreference = 'Stop'
$dataDirectory = if ($env:MAESTRO_DATA_DIR) { [IO.Path]::GetFullPath($env:MAESTRO_DATA_DIR) } else { Join-Path $env:LOCALAPPDATA 'Maestro\preview' }
$pidPath = Join-Path $dataDirectory 'server.pid'
if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Output 'No Maestro preview process is recorded.'
    return
}
$serverProcessId = [int](Get-Content -LiteralPath $pidPath)
$serverProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $serverProcessId"
$repoDirectory = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$expectedPython = Join-Path $repoDirectory '.venv\Scripts\python.exe'
if ($serverProcess -and $serverProcess.ExecutablePath -eq $expectedPython -and $serverProcess.CommandLine -match 'uvicorn backend\.app:app') {
    Stop-Process -Id $serverProcessId
    Remove-Item -LiteralPath $pidPath
    Write-Output 'Maestro preview stopped. Your local workspace is preserved.'
} elseif (-not $serverProcess) {
    Remove-Item -LiteralPath $pidPath
    Write-Output 'Maestro preview was already stopped.'
} else {
    throw 'The recorded process no longer belongs to Maestro; it was not stopped.'
}
