"""Exercise container ownership and lifecycle without touching a Podman service."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == "nt", "PowerShell container launcher")
class ContainerLauncherTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory(prefix="maestro-container-test-"))

    def test_lifecycle_preserves_unrelated_containers_and_private_volumes(self):
        archive = Path(self.directory) / "archive's public context"
        alternate = Path(self.directory) / "another archive"
        archive.mkdir()
        alternate.mkdir()
        source_file = Path(self.directory) / "not-a-directory.txt"
        source_file.write_text("Synthetic path-validation fixture.", encoding="utf-8")
        harness = r"""
$ErrorActionPreference = 'Stop'
$launcher = Join-Path (Get-Location) 'scripts/container.ps1'
$global:container = $null
$global:volumeExists = $false
$global:volumeOwned = $true
$global:imageId = 'built-image'
$global:operations = @()
$global:healthMode = 'ready'
$global:connectionUrl = 'ssh://root@127.0.0.1:65152/run/podman/podman.sock'
$global:machineType = 'wsl'
$archiveDirectory = $env:MAESTRO_TEST_ARCHIVE_DIR
$archiveAbsolute = (Get-Item -LiteralPath $archiveDirectory).FullName
$global:archiveMount = '/mnt/' + $archiveAbsolute.Substring(0, 1).ToLowerInvariant() + '/' + $archiveAbsolute.Substring(3).Replace('\', '/')
$global:archiveCanonical = $archiveMount
$global:archiveQuoted = "'" + $archiveMount.Replace("'", "'\''") + "'"
function podman {
    $global:LASTEXITCODE = 0
    $global:operations += ,@($args)
    switch (($args[0..1] -join ' ')) {
        'container exists' { if (-not $global:container) { $global:LASTEXITCODE = 1 }; return }
        'container inspect' { return ($global:container | ConvertTo-Json -Depth 8) }
        'image inspect' { return (@{ Id = $global:imageId } | ConvertTo-Json) }
        'machine inspect' { return (@{ Name = 'synthetic-machine'; ConfigDir = @{ Path = "C:\synthetic\$($global:machineType)" }; State = 'running'; SSHConfig = @{ Port = 65152; RemoteUsername = 'user' } } | ConvertTo-Json) }
        'machine ssh' {
            if ($args[2] -cne 'synthetic-machine') { throw 'Archive check used another machine.' }
            if ($args[3].StartsWith('test -d ')) { return }
            if ($args[3].StartsWith('readlink -e -- ')) { return $global:archiveCanonical }
            throw 'Unexpected archive machine check.'
        }
        'system connection' { return (@(@{ Name = 'default'; Default = $true; URI = $global:connectionUrl }, @{ Name = 'named'; Default = $false; URI = 'ssh://user@127.0.0.1:65153/run/podman/podman.sock' }) | ConvertTo-Json) }
        'volume exists' { if (-not $global:volumeExists) { $global:LASTEXITCODE = 1 }; return }
        'volume create' { $global:volumeExists = $true; return 'maestro-test-data' }
        'volume inspect' { return (@{ Labels = @{ 'io.maestro.managed' = $(if ($global:volumeOwned) { 'container.ps1' } else { 'other' }) } } | ConvertTo-Json) }
    }
    switch ($args[0]) {
        'run' {
            $envs = @(); $labels = @{}
            for ($index = 1; $index -lt $args.Count - 1; $index++) {
                if ($args[$index] -eq '--env') { $envs += $args[++$index] }
                elseif ($args[$index] -eq '--label') { $key, $value = $args[++$index] -split '=', 2; $labels[$key] = $value }
            }
            $global:container = @{ Id = 'owned-id'; Image = $global:imageId; Config = @{ Labels = $labels; Env = $envs }; Mounts = @(@{ Type = 'volume'; Name = 'maestro-test-data'; Destination = '/data' }); State = @{ Running = $true; Status = 'running' } }
            return 'owned-id'
        }
        'start' { $global:container.State.Running = $true; return 'owned-id' }
        'stop' { $global:container.State.Running = $false; return 'owned-id' }
        default { throw "Unexpected Podman call: $args" }
    }
}
function Invoke-RestMethod {
    return @{ application = 'maestro'; launch_id = $(if ($global:healthMode -eq 'ready') { ($global:container.Config.Env | Where-Object { $_.StartsWith('MAESTRO_LAUNCH_ID=') }) -replace '^MAESTRO_LAUNCH_ID=', '' } else { 'another-container' }) }
}
function Start-Sleep { }
function Assert-Blocked($action, $expected, $extra = @{}) {
    $global:operations = @(); $failure = $null
    try { & $launcher -Action $action -Name maestro-test -ProviderSecret provider-test -SearchSecret search-test @extra | Out-Null } catch { $failure = $_.Exception.Message }
    if ($failure -notlike "*$expected*") { throw "Expected blocked $action ($expected), got $failure" }
    if ($global:operations | Where-Object { $_[0] -in @('run', 'start', 'stop', 'rm') -or ($_[0..1] -join ' ') -eq 'volume create' }) { throw 'Blocked operation changed Podman state.' }
}
& $launcher -Action start -Name maestro-test -ProviderSecret provider-test -SearchSecret search-test | Out-Null
$run = $global:operations | Where-Object { $_[0] -eq 'run' }
$required = @('127.0.0.1:8765:8765', 'maestro-test-data:/data:U', '--read-only', '--cap-drop', 'all', '--memory-swap', '1g', '--pids-limit', '64', 'max-size=10mb', '--http-proxy=false', 'provider-test,target=maestro-provider-key,uid=1000,gid=1000,mode=0400', 'search-test,target=maestro-search-key,uid=1000,gid=1000,mode=0400')
if (-not $global:volumeExists -or ($required | Where-Object { $_ -notin $run })) { throw 'Private volume, runtime constraints or secret mounts were missing.' }
if ($global:container.Config.Env -contains 'MAESTRO_CONTEXT_ARCHIVE_DIR=/archive' -or ($run | Where-Object { [string]$_ -like '*:/archive:*' })) { throw 'Archive mount was enabled without an explicit path.' }
$global:operations = @()
& $launcher -Action start -Name maestro-test -ProviderSecret provider-test -SearchSecret search-test | Out-Null
if ($global:operations | Where-Object { $_[0] -in @('run', 'start') }) { throw 'Matching running container was replaced or restarted.' }
& $launcher -Action stop -Name maestro-test | Out-Null
if ($global:container.State.Running) { throw 'Owned container did not stop.' }
& $launcher -Action start -Name maestro-test -ProviderSecret provider-test -SearchSecret search-test | Out-Null
if (-not $global:container.State.Running -or ($global:operations | Where-Object { $_[0] -eq 'run' })) { throw 'Restart did not reuse the existing container and volume.' }
Assert-Blocked start 'another image or configuration' @{ Port = 8766 }
Assert-Blocked start 'not owned' @{ Volume = 'Maestro-Test-data' }
Assert-Blocked start 'another image or configuration' @{ Tunnel = $true }
foreach ($connectionUrl in @('ssh://user@127.0.0.1:65153/run/podman/podman.sock', 'ssh://user@other.invalid:65152/run/podman/podman.sock', 'tcp://127.0.0.1:65152')) {
    $global:connectionUrl = $connectionUrl
    Assert-Blocked start 'active Podman connection' @{ Tunnel = $true }
}
$env:CONTAINER_HOST = 'ssh://root@127.0.0.1:65152/run/podman/podman.sock'
Assert-Blocked start 'another image or configuration' @{ Tunnel = $true }
$env:CONTAINER_CONNECTION = 'named'
Assert-Blocked start 'active Podman connection' @{ Tunnel = $true }
$env:CONTAINER_HOST = ''; $env:CONTAINER_CONNECTION = ''
$global:container.Config.Labels.'io.maestro.managed' = 'Container.ps1'
Assert-Blocked stop 'not owned'
$global:container.Config.Labels.'io.maestro.managed' = 'container.ps1'
$global:imageId = 'updated-image'
Assert-Blocked start 'another image or configuration'
$global:imageId = 'built-image'
$global:healthMode = 'unrelated'
Assert-Blocked start 'did not become ready'
$global:healthMode = 'ready'
$global:connectionUrl = 'ssh://root@127.0.0.1:65152/run/podman/podman.sock'
Assert-Blocked start 'another image or configuration' @{ ContextArchivePath = $archiveDirectory }
Assert-Blocked start 'existing directory' @{ ContextArchivePath = (Join-Path $archiveDirectory 'does-not-exist') }
Assert-Blocked start 'filesystem directory' @{ ContextArchivePath = $env:MAESTRO_TEST_ARCHIVE_FILE }
foreach ($overlap in @((Get-Location).Path, (Join-Path (Get-Location) 'frontend'), (Split-Path -Parent (Get-Location).Path))) {
    Assert-Blocked start 'outside the Git checkout' @{ ContextArchivePath = $overlap }
}
$junction = Join-Path $env:LOCALAPPDATA 'archive-junction'
New-Item -ItemType Junction -Path $junction -Target (Get-Location).Path | Out-Null
try { Assert-Blocked start 'junction' @{ ContextArchivePath = (Join-Path $junction 'frontend') } }
finally { [IO.Directory]::Delete($junction) }
$global:machineType = 'hyperv'
Assert-Blocked start 'Windows WSL' @{ ContextArchivePath = $archiveDirectory }
$global:machineType = 'wsl'
$global:connectionUrl = 'ssh://user@other.invalid:65152/run/podman/podman.sock'
Assert-Blocked start 'active Podman connection' @{ ContextArchivePath = $archiveDirectory }
$global:connectionUrl = 'ssh://root@127.0.0.1:65152/run/podman/podman.sock'
$global:archiveCanonical = '/different/resolved/directory'
Assert-Blocked start 'resolves differently' @{ ContextArchivePath = $archiveDirectory }
$global:archiveCanonical = $archiveMount
$global:container = $null; $global:operations = @()
& $launcher -Action start -Name maestro-test -ProviderSecret provider-test -SearchSecret search-test -ContextArchivePath $archiveDirectory | Out-Null
$run = $global:operations | Where-Object { $_[0] -eq 'run' }
if ($run -notcontains "${archiveMount}:/archive:rw,noexec,nosuid,nodev" -or $global:container.Config.Env -notcontains 'MAESTRO_CONTEXT_ARCHIVE_DIR=/archive') { throw 'Explicit archive mount or environment was missing.' }
$checks = @($global:operations | Where-Object { ($_[0..1] -join ' ') -eq 'machine ssh' })
if ($checks.Count -ne 2 -or $checks[0][3] -cne "test -d $archiveQuoted" -or $checks[1][3] -cne "readlink -e -- $archiveQuoted") { throw 'Archive checks did not quote the selected directory literally.' }
$archiveArgument = @($run | Where-Object { [string]$_ -like '*:/archive:*' })[0]
if ((($archiveArgument -split ':/archive:', 2)[1] -split ',') | Where-Object { $_ -cin @('U', 'z', 'Z') }) { throw 'Archive mount changes ownership or labels.' }
$global:operations = @()
& $launcher -Action start -Name maestro-test -ProviderSecret provider-test -SearchSecret search-test -ContextArchivePath $archiveDirectory | Out-Null
if ($global:operations | Where-Object { $_[0] -in @('run', 'start') }) { throw 'Matching archive container was replaced or restarted.' }
& $launcher -Action stop -Name maestro-test | Out-Null
& $launcher -Action start -Name maestro-test -ProviderSecret provider-test -SearchSecret search-test -ContextArchivePath $archiveDirectory | Out-Null
if (-not $global:container.State.Running -or ($global:operations | Where-Object { $_[0] -eq 'run' })) { throw 'Archive restart replaced its private volume.' }
Assert-Blocked start 'another image or configuration'
$alternateAbsolute = (Get-Item -LiteralPath $env:MAESTRO_TEST_OTHER_ARCHIVE_DIR).FullName
$global:archiveCanonical = '/mnt/' + $alternateAbsolute.Substring(0, 1).ToLowerInvariant() + '/' + $alternateAbsolute.Substring(3).Replace('\', '/')
Assert-Blocked start 'another image or configuration' @{ ContextArchivePath = $env:MAESTRO_TEST_OTHER_ARCHIVE_DIR }
if (Test-Path -LiteralPath (Join-Path $archiveDirectory 'does-not-exist')) { throw 'Archive validation created a missing directory.' }
$global:container.Config.Labels.'io.maestro.managed' = 'other'
foreach ($action in @('start', 'stop', 'status')) { Assert-Blocked $action 'not owned' }
$global:container = $null; $global:volumeOwned = $false
Assert-Blocked start 'not owned'
Write-Output 'Container lifecycle scenarios passed.'
"""
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", harness],
            cwd=Path(__file__).resolve().parent.parent,
            env={**os.environ, "LOCALAPPDATA": self.directory, "CONTAINER_HOST": "", "CONTAINER_CONNECTION": "",
                 "MAESTRO_TEST_ARCHIVE_DIR": str(archive), "MAESTRO_TEST_OTHER_ARCHIVE_DIR": str(alternate),
                 "MAESTRO_TEST_ARCHIVE_FILE": str(source_file)},
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Container lifecycle scenarios passed.", result.stdout)

    def test_tunnel_reuse_and_stop_verify_process_identity(self):
        harness = r"""
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path (Get-Location) 'scripts/container.ps1'), [ref]$tokens, [ref]$errors)
if ($errors) { throw 'Container launcher did not parse.' }
$function = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Manage-Tunnel' }, $true)
. ([scriptblock]::Create($function.Extent.Text))
$owner = 'container.ps1'; $Name = 'maestro-test'; $configId = 'current-config'
$directory = Join-Path $env:LOCALAPPDATA 'Maestro/container-tunnels'
New-Item -ItemType Directory -Force -Path $directory | Out-Null
$recordPath = Join-Path $directory "$Name.json"
$stamp = [datetime]::UtcNow
$global:killed = $false
$global:fakeProcess = [pscustomobject]@{ StartTime = $stamp }
$fakeProcess | Add-Member ScriptMethod Kill { $global:killed = $true }
$fakeProcess | Add-Member ScriptMethod WaitForExit { return $true }
$global:details = @{ ExecutablePath = 'C:\Windows\System32\OpenSSH\ssh.exe'; CommandLine = 'owned SSH command' }
function Get-Process { return $global:fakeProcess }
function Get-CimInstance { return $global:details }
function Start-Process { throw 'Reuse must never start an SSH process.' }
$record = @{ Owner = $owner; Name = $Name; ProcessId = 43210; StartTicks = $stamp.ToUniversalTime().Ticks; Executable = $details.ExecutablePath; CommandLine = $details.CommandLine; ConfigId = $configId }
foreach ($mode in @('reuse', 'configuration', 'pid-reuse', 'executable', 'command', 'foreign', 'case', 'owner-case', 'stop', 'stale')) {
    $global:killed = $false; $failure = $null
    $record.Owner = $owner; $record.Name = $Name; $record.ConfigId = $configId; $record.StartTicks = $stamp.ToUniversalTime().Ticks; $record.Executable = $details.ExecutablePath; $record.CommandLine = $details.CommandLine
    if ($mode -eq 'configuration') { $record.ConfigId = 'other-config' }
    if ($mode -eq 'pid-reuse') { $record.StartTicks++ }
    if ($mode -eq 'executable') { $record.Executable = 'another-process.exe' }
    if ($mode -eq 'command') { $record.CommandLine = 'another SSH command' }
    if ($mode -eq 'foreign') { $record.Owner = 'other-launcher' }
    if ($mode -eq 'case') { $record.Name = 'Maestro-Test' }
    if ($mode -eq 'owner-case') { $record.Owner = 'Container.ps1' }
    Set-Content -LiteralPath $recordPath -Value ($record | ConvertTo-Json -Compress)
    if ($mode -eq 'stale') { $global:fakeProcess = $null }
    try { Manage-Tunnel ($mode -in @('reuse', 'configuration')) } catch { $failure = $_.Exception.Message }
    if ($mode -eq 'reuse') {
        if ($failure -or $killed -or -not (Test-Path -LiteralPath $recordPath)) { throw 'Owned SSH reuse changed its process or record.' }
    } elseif ($mode -in @('stop', 'stale')) {
        if ($failure -or (Test-Path -LiteralPath $recordPath) -or ($killed -ne ($mode -eq 'stop'))) { throw 'Owned/stale tunnel cleanup failed.' }
    } elseif (-not $failure -or $killed -or -not (Test-Path -LiteralPath $recordPath)) { throw 'Unverified tunnel identity was modified.' }
}
Write-Output 'Tunnel ownership scenarios passed.'
"""
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", harness],
            cwd=Path(__file__).resolve().parent.parent,
            env={**os.environ, "LOCALAPPDATA": self.directory},
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Tunnel ownership scenarios passed.", result.stdout)


if __name__ == "__main__":
    unittest.main()
