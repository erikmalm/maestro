param(
    [Parameter(Mandatory)][ValidateSet('build', 'start', 'stop', 'status')][string]$Action,
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9_.-]*$')][string]$Name = 'maestro',
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9_.-]*$')][string]$Volume,
    [string]$Image = 'localhost/maestro:dev',
    [ValidateRange(1024, 65535)][int]$Port = 8765,
    [switch]$Tunnel,
    [ValidateRange(1024, 65535)][int]$OllamaTunnelPort = 11435,
    [ValidateRange(0.1, 32)][double]$Cpus = 2,
    [ValidatePattern('^[1-9][0-9]*[mg]$')][string]$Memory = '1g',
    [string]$OllamaUrl = 'http://host.containers.internal:11434',
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9_.-]*$')][string]$ProviderSecret,
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9_.-]*$')][string]$SearchSecret,
    [string]$ApiKeyUrl = 'https://api.openai.com/v1'
)

$ErrorActionPreference = 'Stop'
$repoDirectory = Split-Path -Parent $PSScriptRoot
$volumeName = if ($Volume) { $Volume } else { "$Name-data" }
$ownerLabel = 'io.maestro.managed'
$owner = 'container.ps1'
$url = "http://127.0.0.1:$Port"
function Invoke-Podman {
    $result = & podman @args
    if ($LASTEXITCODE -ne 0) { throw "Podman failed: $($args[0])" }
    return $result
}
function Get-OwnedContainer {
    & podman container exists $Name
    if ($LASTEXITCODE -eq 1) { return $null }
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect Podman containers.' }
    $container = @(Invoke-Podman container inspect $Name | ConvertFrom-Json)[0]
    if ($container.Config.Labels.$ownerLabel -ne $owner -or -not ($container.Mounts | Where-Object { $_.Type -eq 'volume' -and $_.Name -eq $volumeName -and $_.Destination -eq '/data' })) {
        throw "Container $Name is not owned by this launcher with volume $volumeName."
    }
    return $container
}
function Wait-Ready($container) {
    $launchId = ($container.Config.Env | Where-Object { $_.StartsWith('MAESTRO_LAUNCH_ID=') }) -replace '^MAESTRO_LAUNCH_ID=', ''
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri "$url/health" -TimeoutSec 1
            if ($launchId -and $health.application -eq 'maestro' -and $health.launch_id -eq $launchId) { return }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    throw "Container $Name did not become ready. Inspect it with podman logs $Name."
}
function Manage-Tunnel([bool]$StartTunnel) {
    $directory = Join-Path $env:LOCALAPPDATA 'Maestro/container-tunnels'
    $recordPath = Join-Path $directory "$Name.json"
    if (Test-Path -LiteralPath $recordPath) {
        $record = Get-Content -Raw -LiteralPath $recordPath | ConvertFrom-Json
        if ($record.Owner -ne $owner -or $record.Name -ne $Name) { throw 'The SSH tunnel record is not owned by this launcher.' }
        $process = Get-Process -Id $record.ProcessId -ErrorAction SilentlyContinue
        if ($process) {
            $null = $process.Handle  # Keep this process handle open across verification and stop.
            $details = Get-CimInstance Win32_Process -Filter "ProcessId = $($record.ProcessId)"
            if ($process.StartTime.ToUniversalTime().Ticks -ne $record.StartTicks -or $details.ExecutablePath -ne $record.Executable -or $details.CommandLine -ne $record.CommandLine) {
                throw 'The recorded SSH process no longer matches. It was left untouched; inspect the private tunnel record.'
            }
            if ($StartTunnel) {
                if ($record.ConfigId -ne $configId) { throw 'The SSH tunnel uses another configuration. Stop it before starting again.' }
                return
            }
            $process.Kill()
            if (-not $process.WaitForExit(5000)) { throw 'The owned SSH tunnel did not stop.' }
        }
        Remove-Item -LiteralPath $recordPath
    }
    if (-not $StartTunnel) { return }
    $machine = @(Invoke-Podman machine inspect | ConvertFrom-Json)[0]
    if ($machine.State -ne 'running' -or $machine.SSHConfig.RemoteUsername -notmatch '^[a-zA-Z0-9_.-]+$') { throw 'Start the default Podman machine before using -Tunnel.' }
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    $sshPath = (Get-Command ssh.exe -ErrorAction Stop).Source
    $knownHosts = Join-Path $directory 'known_hosts'
    $logPath = Join-Path $directory "$Name.stderr.log"
    $sshOptions = @('-N', '-T', '-n', '-F', 'none', '-i', ('"' + $machine.SSHConfig.IdentityPath + '"'), '-p', $machine.SSHConfig.Port, '-E', ('"' + $logPath + '"'),
        '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'ExitOnForwardFailure=yes', '-o', 'ConnectTimeout=5',
        '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3', '-o', 'StrictHostKeyChecking=accept-new',
        '-o', ('UserKnownHostsFile="' + $knownHosts + '"'), '-L', "127.0.0.1:${Port}:127.0.0.1:$Port",
        '-R', "127.0.0.1:${OllamaTunnelPort}:127.0.0.1:11434", "$($machine.SSHConfig.RemoteUsername)@127.0.0.1")
    $process = Start-Process -FilePath $sshPath -ArgumentList $sshOptions -WindowStyle Hidden -PassThru
    Start-Sleep -Milliseconds 500
    if ($process.HasExited) { throw "SSH forwarding failed. Check $logPath." }
    try {
        $details = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.Id)"
        if (-not $details.CommandLine -or $details.ExecutablePath -ne $sshPath) { throw 'Could not verify the launched SSH process.' }
        $record = @{ Owner = $owner; Name = $Name; ProcessId = $process.Id; StartTicks = $process.StartTime.ToUniversalTime().Ticks; Executable = $details.ExecutablePath; CommandLine = $details.CommandLine; ConfigId = $configId }
        New-Item -ItemType File -Path $recordPath -Value ($record | ConvertTo-Json -Compress) | Out-Null
    } catch {
        if (-not $process.HasExited) { $process.Kill() }
        throw
    }
}

if ($Action -eq 'build') {
    Invoke-Podman build --format docker --http-proxy=false --file (Join-Path $repoDirectory 'Containerfile') --tag $Image $repoDirectory
    return
}
$container = Get-OwnedContainer
if ($Action -eq 'status') {
    if ($container) { Write-Output "$Name is $($container.State.Status); health: $($container.State.Health.Status)." } else { Write-Output "$Name is not created." }
    return
}
if ($Action -eq 'stop') {
    if ($container) { Invoke-Podman stop $container.Id }
    Manage-Tunnel $false
    return
}

$networkOptions = @('--publish', "127.0.0.1:${Port}:8765")
$command = @()
if ($Tunnel) {
    if ($Port -eq $OllamaTunnelPort) { throw 'Choose different Maestro and Ollama tunnel ports.' }
    $OllamaUrl = "http://127.0.0.1:$OllamaTunnelPort"
    $networkOptions = @('--network', 'host', '--env', "MAESTRO_LISTEN_PORT=$Port")
    $command = @('python', '-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', "$Port", '--workers', '1', '--no-access-log')
}
$runOptions = $networkOptions + @('--volume', "${volumeName}:/data:U",
    '--cpus', $Cpus.ToString([Globalization.CultureInfo]::InvariantCulture), '--memory', $Memory, '--memory-swap', $Memory,
    '--pids-limit', '64', '--read-only', '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m,mode=1777', '--cap-drop', 'all', '--security-opt', 'no-new-privileges', '--umask', '0077',
    '--log-driver', 'k8s-file', '--log-opt', 'max-size=10mb', '--restart', 'unless-stopped', '--http-proxy=false',
    '--env', "MAESTRO_PORT=$Port", '--env', "MAESTRO_OLLAMA_URL=$OllamaUrl")
if ($ProviderSecret) {
    $runOptions += @('--secret', "$ProviderSecret,target=maestro-provider-key,uid=1000,gid=1000,mode=0400",
        '--env', 'MAESTRO_API_KEY_FILE=/run/secrets/maestro-provider-key', '--env', "MAESTRO_API_KEY_URL=$ApiKeyUrl")
}
if ($SearchSecret) {
    $runOptions += @('--secret', "$SearchSecret,target=maestro-search-key,uid=1000,gid=1000,mode=0400",
        '--env', 'MAESTRO_SEARCH_KEY_FILE=/run/secrets/maestro-search-key')
}
$imageId = @(Invoke-Podman image inspect $Image | ConvertFrom-Json)[0].Id
$sha256 = [Security.Cryptography.SHA256]::Create()
try { $configId = -join ($sha256.ComputeHash([Text.Encoding]::UTF8.GetBytes((($runOptions + $command) | ConvertTo-Json -Compress))) | ForEach-Object { $_.ToString('x2') }) } finally { $sha256.Dispose() }
if ($container) {
    if ($container.Image -ne $imageId -or $container.Config.Labels.'io.maestro.config' -ne $configId) {
        throw "Container $Name uses another image or configuration. Stop it and remove that container explicitly before starting again; keep $volumeName."
    }
    if (-not $container.State.Running) { Invoke-Podman start $container.Id | Out-Null }
} else {
    & podman volume exists $volumeName
    if ($LASTEXITCODE -eq 1) {
        Invoke-Podman volume create --label "$ownerLabel=$owner" --uid 1000 --gid 1000 $volumeName | Out-Null
    } elseif ($LASTEXITCODE -ne 0) { throw 'Could not inspect Podman volumes.' }
    $volumeInfo = @(Invoke-Podman volume inspect $volumeName | ConvertFrom-Json)[0]
    if ($volumeInfo.Labels.$ownerLabel -ne $owner) { throw "Volume $volumeName is not owned by this launcher." }
    Invoke-Podman run --detach --name $Name --pull never --label "$ownerLabel=$owner" --label "io.maestro.config=$configId" --env "MAESTRO_LAUNCH_ID=$([guid]::NewGuid().ToString('N'))" @runOptions $Image @command | Out-Null
    $container = Get-OwnedContainer
}
if ($Tunnel) { Manage-Tunnel $true }
Wait-Ready $container
Write-Output "Maestro is ready at $url; workspace volume: $volumeName."
