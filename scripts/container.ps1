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
    [string]$ContextArchivePath,
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
    if ($container.Config.Labels.$ownerLabel -cne $owner -or -not ($container.Mounts | Where-Object { $_.Type -eq 'volume' -and $_.Name -ceq $volumeName -and $_.Destination -eq '/data' })) {
        throw "Container $Name is not owned by this launcher with volume $volumeName."
    }
    return $container
}
function Get-SelectedLocalMachine {
    $machine = @(Invoke-Podman machine inspect | ConvertFrom-Json)[0]
    if ($machine.State -ne 'running' -or $machine.SSHConfig.RemoteUsername -notmatch '^[a-zA-Z0-9_.-]+$') { throw 'Start the default Podman machine before using -Tunnel or -ContextArchivePath.' }
    $connectionUrl = $env:CONTAINER_HOST
    if ($env:CONTAINER_CONNECTION -or -not $connectionUrl) {
        $connections = Invoke-Podman system connection list --format json | ConvertFrom-Json
        $connection = @($connections | Where-Object { if ($env:CONTAINER_CONNECTION) { $_.Name -ceq $env:CONTAINER_CONNECTION } else { $_.Default } })[0]
        $connectionUrl = $connection.URI
    }
    try { $endpoint = [uri]$connectionUrl } catch { throw 'Could not read the active Podman connection.' }
    if ($endpoint.Scheme -ne 'ssh' -or $endpoint.Host -notin @('127.0.0.1', 'localhost', '[::1]') -or $endpoint.Port -ne $machine.SSHConfig.Port) {
        throw 'Tunnel and archive mounting require the active Podman connection to use the default machine.'
    }
    return $machine
}
function Get-ArchiveMountSource($directory, $machine) {
    if ($env:OS -ne 'Windows_NT' -or (Split-Path -Leaf $machine.ConfigDir.Path) -ne 'wsl') {
        throw '-ContextArchivePath currently requires a local Windows WSL Podman machine.'
    }
    try { $item = Get-Item -LiteralPath $directory -Force } catch { throw 'Choose an existing directory for -ContextArchivePath; the launcher does not create it.' }
    if ($item.PSProvider.Name -ne 'FileSystem' -or -not $item.PSIsContainer) { throw 'Choose an existing filesystem directory for -ContextArchivePath.' }
    $absolute = $item.FullName.TrimEnd('\')
    if ($absolute -notmatch '^[a-zA-Z]:\\' -or $absolute.Substring(2) -match '[:\r\n]') { throw 'Choose a local Windows drive directory for -ContextArchivePath; UNC and device paths are unsupported.' }
    $repoAbsolute = [IO.Path]::GetFullPath($repoDirectory).TrimEnd('\')
    if ($absolute -eq $repoAbsolute -or $absolute.StartsWith($repoAbsolute + '\', [StringComparison]::OrdinalIgnoreCase) -or
            $repoAbsolute.StartsWith($absolute + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Choose -ContextArchivePath outside the Git checkout.'
    }
    for ($ancestor = $item; $ancestor; $ancestor = $ancestor.Parent) {
        if ($ancestor.LinkType -in @('SymbolicLink', 'Junction')) { throw 'Choose -ContextArchivePath without symbolic links or junctions.' }
    }
    $mapped = '/mnt/' + $absolute.Substring(0, 1).ToLowerInvariant() + '/' + $absolute.Substring(3).Replace('\', '/')
    # Podman executes SSH commands in a shell: quote the path as one literal operand.
    $quoted = "'" + $mapped.Replace("'", "'\''") + "'"
    try {
        $null = Invoke-Podman machine ssh $machine.Name "test -d $quoted"
        $canonical = @(Invoke-Podman machine ssh $machine.Name "readlink -e -- $quoted")
    } catch { throw 'The selected archive directory is unavailable in the default WSL Podman machine.' }
    if ($canonical.Count -ne 1 -or $canonical[0] -cne $mapped) { throw 'The archive directory resolves differently inside the Podman machine; choose a direct Windows directory.' }
    return $mapped
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
        if ($record.Owner -cne $owner -or $record.Name -cne $Name) { throw 'The SSH tunnel record is not owned by this launcher.' }
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
if ($Action -eq 'start' -and ($Tunnel -or $ContextArchivePath)) {
    $machine = Get-SelectedLocalMachine
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
if ($ContextArchivePath) {
    $archiveSource = Get-ArchiveMountSource $ContextArchivePath $machine
    $runOptions += @('--volume', "${archiveSource}:/archive:rw,noexec,nosuid,nodev", '--env', 'MAESTRO_CONTEXT_ARCHIVE_DIR=/archive')
}
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
    if ($volumeInfo.Labels.$ownerLabel -cne $owner) { throw "Volume $volumeName is not owned by this launcher." }
    Invoke-Podman run --detach --name $Name --pull never --label "$ownerLabel=$owner" --label "io.maestro.config=$configId" --env "MAESTRO_LAUNCH_ID=$([guid]::NewGuid().ToString('N'))" @runOptions $Image @command | Out-Null
    $container = Get-OwnedContainer
}
if ($Tunnel) { Manage-Tunnel $true }
Wait-Ready $container
Write-Output "Maestro is ready at $url; workspace volume: $volumeName."
