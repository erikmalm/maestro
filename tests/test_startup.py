"""Exercise Windows launch cleanup with synthetic processes and temporary PID files."""

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


LAUNCHER_DEPENDENCY_HARNESS = r"""
$ErrorActionPreference = 'Stop'
$repoDirectory = (Get-Location).Path
$source = Get-Content -Raw -LiteralPath (Join-Path $repoDirectory 'scripts/start.ps1')
# Exercise the complete production control flow; substitute only interpreter and
# checkout discovery so dependency installation and process creation stay mocked.
$source = $source.Replace('$repoDirectory = Split-Path -Parent $PSScriptRoot', '$repoDirectory = $env:MAESTRO_TEST_REPO')
$source = $source.Replace('$pythonPath = Join-Path $repoDirectory ''.venv\Scripts\python.exe''', '$pythonPath = ''Invoke-LauncherPython''')
if (-not $source.Contains('$pythonPath = ''Invoke-LauncherPython''') -or
    -not $source.Contains('$repoDirectory = $env:MAESTRO_TEST_REPO')) { throw 'Dependency substitution did not match the launcher.' }
$launcher = [scriptblock]::Create($source)
function Invoke-LauncherPython {
    if ($args[0] -eq '-c') { & $env:MAESTRO_TEST_PYTHON @args; return }
    if (($args -join ' ') -notlike '-m pip install *') { throw 'Unexpected Python dependency command.' }
    $script:installs++
    $global:LASTEXITCODE = 0
}
function Test-Path {
    param([string]$LiteralPath)
    if ($LiteralPath -eq 'Invoke-LauncherPython' -or $LiteralPath.EndsWith('node_modules')) { return $true }
    Microsoft.PowerShell.Management\Test-Path -LiteralPath $LiteralPath
}
function npm { $script:builds++; $global:LASTEXITCODE = 0 }
function Start-Process {
    param($FilePath, $ArgumentList, $WindowStyle)
    if ($WindowStyle -ne 'Hidden') { throw 'Background launcher was not hidden.' }
    if (($ArgumentList[0..2] -join ' ') -ne '-m backend.native_server --port') { throw 'Native launcher command was not used.' }
    $script:launches++
    $script:launched = $true
    $script:requestedPort = [int]$ArgumentList[3]
    $script:actualPort = if ($requestedPort -eq 0) { 32456 } else { $requestedPort }
    $script:fakeProcess = [pscustomobject]@{ Id = 43210; HasExited = $false }
    $addressPath = Join-Path $env:MAESTRO_DATA_DIR "server-address-$($env:MAESTRO_LAUNCH_ID).json"
    $address = @{ launch_id = $env:MAESTRO_LAUNCH_ID; port = $actualPort }
    if ($scenario -eq 'wrong-nonce') { $address.launch_id = 'another-launch' }
    if ($scenario -eq 'wrong-port') { $address.port = $actualPort + 1 }
    if ($scenario -eq 'string-port') { $address.port = [string]$actualPort }
    if ($scenario -ne 'missing-address') {
        $body = if ($scenario -eq 'malformed-address') { '{' } elseif ($scenario -eq 'oversized-address') { ' ' * 4097 } else { $address | ConvertTo-Json -Compress }
        Set-Content -LiteralPath $addressPath -Value $body
    }
    return $fakeProcess
}
function Invoke-RestMethod {
    param($Uri, $TimeoutSec)
    $script:polls++
    if ($scenario -eq 'stale-record-foreign-health' -and -not $launched -and ([uri]$Uri).Port -eq 32455) {
        $script:foreignPolls++
        return @{ application = 'another-application' }
    }
    if (-not $launched -and -not $reuse) { throw 'No existing server.' }
    if (([uri]$Uri).Port -ne $actualPort) { throw 'No server at this port.' }
    if ($launched) { $script:readinessPolls++ }
    $answer = @{ application = 'maestro'; mode = 'local'; launch_id = $(if ($launched) { $env:MAESTRO_LAUNCH_ID } else { 'a' * 32 }); workspace_id = $expectedWorkspace; archive_id = $expectedArchive }
    if ($scenario -eq 'wrong-health-workspace' -or $scenario -eq 'reuse-workspace') { $answer.workspace_id = 'another-workspace' }
    if ($scenario -eq 'reuse-archive') { $answer.archive_id = 'another-archive' }
    if ($scenario -eq 'reuse-nonce') { $answer.launch_id = 'b' * 32 }
    return $answer
}
function Start-Sleep { $script:sleeps++ }
function taskkill.exe { $global:stops++; $fakeProcess.HasExited = $true; $global:LASTEXITCODE = 0 }
function Get-CimInstance {
    if (-not $fakeProcess.HasExited) {
        [pscustomobject]@{ ExecutablePath = 'Invoke-LauncherPython'; CommandLine = 'python -m backend.native_server --port 32456' }
    }
}
function Reset-Scenario($label) {
    $script:scenario = $label
    $env:MAESTRO_DATA_DIR = Join-Path $env:MAESTRO_TEST_ROOT $label
    $env:MAESTRO_CONTEXT_ARCHIVE_DIR = ''
    $env:MAESTRO_PORT = ''
    $script:expectedWorkspace = & $env:MAESTRO_TEST_PYTHON -c 'import hashlib, os, sys; from pathlib import Path; print(hashlib.sha256(os.path.normcase(str(Path(sys.argv[1]).resolve())).encode()).hexdigest())' $env:MAESTRO_DATA_DIR
    $script:expectedArchive = $env:MAESTRO_EXPECTED_ARCHIVE_ID
    $script:installs = 0; $script:builds = 0; $script:launches = 0; $script:stops = 0
    $script:polls = 0; $script:sleeps = 0; $script:readinessPolls = 0; $script:foreignPolls = 0
    $script:launched = $false; $script:reuse = $false; $script:actualPort = 32456
    $script:fakeProcess = [pscustomobject]@{ Id = 43210; HasExited = $false }
}
"""


class HealthTests(unittest.TestCase):
    def test_health_identifies_the_launched_server(self):
        with tempfile.TemporaryDirectory(prefix="maestro-health-test-") as directory:
            workspace_id = hashlib.sha256(os.path.normcase(str(Path(directory).resolve())).encode()).hexdigest()
            result = subprocess.run(
                [sys.executable, "-c", "from fastapi.testclient import TestClient; from backend.app import app; "
                 "response = TestClient(app).get('/health'); assert response.status_code == 200; "
                 f"assert response.json() == {{'application': 'maestro', 'mode': 'local', 'launch_id': 'test-launch', 'workspace_id': '{workspace_id}', 'archive_id': '{hashlib.sha256(b'none').hexdigest()}'}}"],
                cwd=Path(__file__).resolve().parent.parent,
                env={**os.environ, "MAESTRO_DATA_DIR": directory, "MAESTRO_LAUNCH_ID": "test-launch", "MAESTRO_CONTEXT_ARCHIVE_DIR": ""},
                capture_output=True, text=True, timeout=20,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipUnless(os.name == "nt", "Windows PowerShell launcher")
class StartupTests(unittest.TestCase):
    def run_launcher_harness(self, scenarios):
        with tempfile.TemporaryDirectory(prefix="maestro-port-test-") as directory:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                 LAUNCHER_DEPENDENCY_HARNESS + scenarios],
                cwd=Path(__file__).resolve().parent.parent,
                env={**os.environ, "MAESTRO_TEST_ROOT": directory,
                     "MAESTRO_TEST_REPO": str(Path(__file__).resolve().parent.parent),
                     "MAESTRO_TEST_PYTHON": sys.executable,
                     "MAESTRO_EXPECTED_ARCHIVE_ID": hashlib.sha256(b"none").hexdigest()},
                capture_output=True, text=True, timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_complete_launcher_port_precedence_and_invalid_inputs(self):
        self.run_launcher_harness(r"""
$cases = @(
    @{ label = 'default'; expected = 8765 },
    @{ label = 'environment'; environment = '8769'; expected = 8769 },
    @{ label = 'parameter'; environment = '8769'; parameter = 8770; expected = 8770 },
    @{ label = 'parameter-over-invalid-env'; environment = 'invalid'; parameter = 8771; expected = 8771 },
    @{ label = 'auto-parameter'; environment = '8769'; parameter = 0; expected = 32456 },
    @{ label = 'auto-environment'; environment = '0'; expected = 32456 }
)
foreach ($case in $cases) {
    Reset-Scenario $case.label
    $env:MAESTRO_PORT = $case.environment
    $parameters = @{ NoBrowser = $true }
    if ($case.ContainsKey('parameter')) { $parameters.Port = $case.parameter }
    $output = & $launcher @parameters
    $record = Get-Content -Raw -LiteralPath (Join-Path $env:MAESTRO_DATA_DIR 'server.json') | ConvertFrom-Json
    if ($launches -ne 1 -or $installs -ne 1 -or $builds -ne 1 -or $stops) { throw "Wrong dependency calls: $($case.label)" }
    if ($record.port -ne $case.expected -or $record.process_id -ne 43210 -or $record.launch_id -cne $env:MAESTRO_LAUNCH_ID -or
        $record.workspace_id -cne $expectedWorkspace -or $record.archive_id -cne $expectedArchive -or
        $env:MAESTRO_PORT -ne [string]$case.expected) { throw "Resolved port/identity was not recorded: $($case.label)" }
    if ($output -notcontains "Maestro is running at http://127.0.0.1:$($case.expected)") { throw 'Printed address was not the bound address.' }
    if (Get-ChildItem -LiteralPath $env:MAESTRO_DATA_DIR -Filter 'server-address-*.json') { throw 'Handshake was not removed.' }
}
$i = 0
foreach ($badPort in @('-1', '65536', 'invalid', '1.5', ' 8765', '012345', '0x2222')) {
    Reset-Scenario "invalid-environment-$i"; $i++
    $env:MAESTRO_PORT = $badPort; $failure = $null
    try { & $launcher -NoBrowser } catch { $failure = $_.Exception.Message }
    if (-not $failure -or $launches -or $installs -or $builds -or $polls -or (Test-Path -LiteralPath $env:MAESTRO_DATA_DIR)) { throw 'Invalid environment port performed work.' }
}
foreach ($badPort in @(-1, 65536, 'invalid')) {
    Reset-Scenario "invalid-parameter-$i"; $i++
    $failure = $null
    try { & $launcher -NoBrowser -Port $badPort } catch { $failure = $_.Exception.Message }
    if (-not $failure -or $launches -or $installs -or $builds -or $polls -or (Test-Path -LiteralPath $env:MAESTRO_DATA_DIR)) { throw 'Invalid parameter port performed work.' }
}
""")

    def test_complete_launcher_requires_owned_handshake_and_workspace(self):
        self.run_launcher_harness(r"""
foreach ($label in @('wrong-nonce', 'wrong-port', 'string-port', 'missing-address', 'malformed-address', 'oversized-address', 'wrong-health-workspace')) {
    Reset-Scenario $label; $failure = $null
    try { & $launcher -NoBrowser -Port 8765 } catch { $failure = $_.Exception.Message }
    if ($failure -notlike 'Maestro did not become ready.*' -or $launches -ne 1 -or $stops -ne 1 -or $sleeps -ne 30) { throw "Unowned handshake/health was accepted: $label ($failure)" }
    if ($label -ne 'wrong-health-workspace' -and $readinessPolls) { throw 'Invalid handshake was used to poll readiness.' }
    if ((Test-Path -LiteralPath (Join-Path $env:MAESTRO_DATA_DIR 'server.pid')) -or
        (Test-Path -LiteralPath (Join-Path $env:MAESTRO_DATA_DIR 'server.json')) -or
        (Get-ChildItem -LiteralPath $env:MAESTRO_DATA_DIR -Filter 'server-address-*.json')) { throw 'Failed readiness left owned metadata.' }
}
""")

    def test_complete_launcher_reuses_recorded_runtime_without_stopping_conflicts(self):
        self.run_launcher_harness(r"""
foreach ($label in @('reuse-default', 'reuse-auto', 'reuse-same', 'reuse-conflict', 'reuse-archive', 'reuse-workspace', 'reuse-nonce')) {
    Reset-Scenario $label
    $script:reuse = $true
    New-Item -ItemType Directory -Path $env:MAESTRO_DATA_DIR | Out-Null
    $recordPath = Join-Path $env:MAESTRO_DATA_DIR 'server.json'
    $pidPath = Join-Path $env:MAESTRO_DATA_DIR 'server.pid'
    $record = @{ port = 32456; process_id = 43210; launch_id = 'a' * 32; workspace_id = $expectedWorkspace; archive_id = $expectedArchive } | ConvertTo-Json -Compress
    Set-Content -LiteralPath $recordPath -Value $record
    Set-Content -LiteralPath $pidPath -Value 43210
    $parameters = @{ NoBrowser = $true }
    if ($label -eq 'reuse-auto') { $parameters.Port = 0 }
    if ($label -eq 'reuse-same') { $parameters.Port = 32456 }
    if ($label -eq 'reuse-conflict') { $parameters.Port = 8765 }
    $failure = $null; $output = $null
    try { $output = & $launcher @parameters } catch { $failure = $_.Exception.Message }
    if ($launches -or $stops -or $installs -or $builds -or $polls -ne 1 -or
        (Get-Content -Raw -LiteralPath $recordPath).Trim() -cne $record -or
        ([string](Get-Content -Raw -LiteralPath $pidPath)).Trim() -ne '43210') { throw "Live recorded runtime was mutated: $label" }
    if ($label -in @('reuse-default', 'reuse-auto', 'reuse-same')) {
        if ($failure -or $output -ne 'Maestro is already running at http://127.0.0.1:32456' -or $env:MAESTRO_PORT -ne '32456') { throw "Matching runtime not reused: $label ($failure)" }
    } elseif (-not $failure) { throw "Conflicting runtime was reused: $label" }
}
foreach ($label in @('stale-record', 'malformed-record', 'invalid-record-port')) {
    Reset-Scenario $label
    New-Item -ItemType Directory -Path $env:MAESTRO_DATA_DIR | Out-Null
    $recordPath = Join-Path $env:MAESTRO_DATA_DIR 'server.json'
    $record = @{ port = 32456; process_id = 43210; launch_id = 'a' * 32; workspace_id = $expectedWorkspace; archive_id = $expectedArchive }
    if ($label -eq 'invalid-record-port') { $record.port = '32456' }
    $body = if ($label -eq 'malformed-record') { '{' } else { $record | ConvertTo-Json -Compress }
    Set-Content -LiteralPath $recordPath -Value $body
    & $launcher -NoBrowser -Port 0 | Out-Null
    $saved = Get-Content -Raw -LiteralPath $recordPath | ConvertFrom-Json
    if ($launches -ne 1 -or $stops -or $saved.port -ne 32456 -or $saved.launch_id -ceq ('a' * 32)) { throw "Stale/malformed record did not permit a new owned launch: $label" }
}
Reset-Scenario 'stale-record-foreign-health'
New-Item -ItemType Directory -Path $env:MAESTRO_DATA_DIR | Out-Null
$recordPath = Join-Path $env:MAESTRO_DATA_DIR 'server.json'
@{ port = 32455; process_id = 43210; launch_id = 'a' * 32; workspace_id = $expectedWorkspace; archive_id = $expectedArchive } | ConvertTo-Json -Compress | Set-Content -LiteralPath $recordPath
Set-Content -LiteralPath (Join-Path $env:MAESTRO_DATA_DIR 'server.pid') -Value 43210
$fakeProcess.HasExited = $true
& $launcher -NoBrowser -Port 0 | Out-Null
$saved = Get-Content -Raw -LiteralPath $recordPath | ConvertFrom-Json
if ($foreignPolls -or $stops -or $launches -ne 1 -or $saved.port -ne 32456 -or $saved.launch_id -ceq ('a' * 32)) { throw 'A dead runtime record let an unrelated HTTP listener block automatic restart.' }
""")

    def test_stop_handles_native_and_legacy_processes_and_preserves_unowned_metadata(self):
        self.run_launcher_harness(r"""
function Get-CimInstance {
    if (-not $fakeProcess.HasExited) {
        $command = if ($scenario -eq 'legacy-process') { 'python -m uvicorn backend.app:app --port 8765' }
            elseif ($scenario -eq 'unrelated-process') { 'python -m backend.native_server_unrelated --port 32456' }
            else { 'python -m backend.native_server --port 32456' }
        [pscustomobject]@{ ExecutablePath = (Join-Path $env:MAESTRO_TEST_REPO '.venv\Scripts\python.exe'); CommandLine = $command }
    }
}
function taskkill.exe {
    if (($args -join ' ') -ne '/PID 43210 /T /F') { throw 'Stop targeted an unexpected process.' }
    $global:stops++; $fakeProcess.HasExited = $true; $global:LASTEXITCODE = 0
    if ($scenario -eq 'concurrent-process-record') {
        Set-Content -LiteralPath (Join-Path $env:MAESTRO_DATA_DIR 'server.pid') -Value 99999
        @{ process_id = 99999; port = 32457 } | ConvertTo-Json -Compress | Set-Content -LiteralPath (Join-Path $env:MAESTRO_DATA_DIR 'server.json')
    }
}
foreach ($label in @('native-process', 'legacy-process', 'other-runtime-owner', 'malformed-runtime', 'concurrent-process-record', 'unrelated-process')) {
    Reset-Scenario $label
    New-Item -ItemType Directory -Path $env:MAESTRO_DATA_DIR | Out-Null
    $pidPath = Join-Path $env:MAESTRO_DATA_DIR 'server.pid'
    $recordPath = Join-Path $env:MAESTRO_DATA_DIR 'server.json'
    Set-Content -LiteralPath $pidPath -Value 43210
    $body = if ($label -eq 'malformed-runtime') { '{' }
        else { @{ process_id = $(if ($label -eq 'other-runtime-owner') { 99999 } else { 43210 }); port = 32456 } | ConvertTo-Json -Compress }
    Set-Content -LiteralPath $recordPath -Value $body
    $failure = $null
    try { & (Join-Path $env:MAESTRO_TEST_REPO 'scripts/stop.ps1') | Out-Null } catch { $failure = $_.Exception.Message }
    if ($label -eq 'unrelated-process') {
        if (-not $failure -or $stops -or -not (Test-Path -LiteralPath $pidPath) -or -not (Test-Path -LiteralPath $recordPath)) { throw 'Stop touched an unrelated executable command.' }
    } elseif ($label -eq 'concurrent-process-record') {
        if ($failure -or $stops -ne 1 -or (Get-Content -LiteralPath $pidPath) -ne '99999' -or
            (Get-Content -Raw -LiteralPath $recordPath | ConvertFrom-Json).process_id -ne 99999) { throw 'Stop removed concurrent process metadata.' }
    } else {
        if ($failure -or $stops -ne 1 -or (Test-Path -LiteralPath $pidPath)) { throw "Owned process was not stopped: $label ($failure), stops=$stops, pid=$([bool](Test-Path -LiteralPath $pidPath))" }
        $preserve = $label -in @('other-runtime-owner', 'malformed-runtime')
        if ((Test-Path -LiteralPath $recordPath) -ne $preserve) { throw "Stop changed metadata without ownership: $label" }
        if ($preserve -and (Get-Content -Raw -LiteralPath $recordPath).Trim() -cne $body) { throw 'Stop rewrote unowned metadata.' }
    }
}
""")

    def test_failed_launch_cleanup_and_successful_owned_process_tree(self):
        harness = r"""
$ErrorActionPreference = 'Stop'
$repoDirectory = (Get-Location).Path
$dataDirectory = $env:MAESTRO_DATA_DIR
$pythonPath = Join-Path $repoDirectory '.venv\Scripts\python.exe'
$previewUrl = 'http://127.0.0.1:8765'
$Port = 8765
$portSpecified = $false
$runtime = $null
$runtimePath = Join-Path $dataDirectory 'server.json'
$pidPath = Join-Path $dataDirectory 'server.pid'
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $repoDirectory 'scripts/start.ps1'), [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'Launcher did not parse.' }
$identityStatements = $ast.EndBlock.Statements | Where-Object {
    $_.Extent.StartOffset -ge ($ast.EndBlock.Statements | Where-Object { $_.Extent.Text.StartsWith('$identityPython =') }).Extent.StartOffset -and
    $_.Extent.StartOffset -lt ($ast.EndBlock.Statements | Where-Object { $_.Extent.Text.StartsWith('$previewUrl =') } | Select-Object -First 1).Extent.StartOffset
}
$identify = [scriptblock]::Create(($identityStatements.Extent.Text -join "`n"))
. $identify
if ($workspaceId -ne $env:MAESTRO_EXPECTED_WORKSPACE_ID) { throw 'Launcher and backend workspace identities differ.' }
if ($archiveId -ne $env:MAESTRO_EXPECTED_ARCHIVE_ID) { throw 'Launcher and backend blank archive identities differ.' }
$blankArchiveId = $archiveId
$env:MAESTRO_CONTEXT_ARCHIVE_DIR = 'relative/source-archive'
. $identify
$relativeArchiveId = $archiveId
Push-Location $dataDirectory
try { . $identify } finally { Pop-Location }
if ($archiveId -ne $relativeArchiveId -or $archiveId -ne $env:MAESTRO_EXPECTED_RELATIVE_ARCHIVE_ID) { throw 'Invalid relative archive identity depended on the launcher working directory.' }
$env:MAESTRO_CONTEXT_ARCHIVE_DIR = Join-Path (Split-Path -Parent $dataDirectory) 'maestro-archive-identity'
. $identify
$configuredArchiveId = $archiveId
if ($configuredArchiveId -eq $blankArchiveId) { throw 'Configured archive reused the blank identity.' }
$env:MAESTRO_CONTEXT_ARCHIVE_DIR = (Join-Path $env:MAESTRO_CONTEXT_ARCHIVE_DIR '.').ToUpperInvariant()
. $identify
if ($archiveId -ne $configuredArchiveId) { throw 'Equivalent archive paths changed archive identity.' }
$env:MAESTRO_CONTEXT_ARCHIVE_DIR = Join-Path (Split-Path -Parent $dataDirectory) 'maestro-other-archive'
. $identify
if ($archiveId -eq $configuredArchiveId) { throw 'Another archive reused the archive identity.' }
$env:MAESTRO_CONTEXT_ARCHIVE_DIR = ''
. $identify
$originalDirectory = $dataDirectory
$dataDirectory = (Join-Path $dataDirectory '.').ToUpperInvariant()
. $identify
if ($workspaceId -ne $env:MAESTRO_EXPECTED_WORKSPACE_ID) { throw 'Equivalent private directory aliases changed workspace identity.' }
$dataDirectory = Join-Path (Split-Path -Parent $originalDirectory) 'maestro-different-workspace'
. $identify
if ($workspaceId -eq $env:MAESTRO_EXPECTED_WORKSPACE_ID) { throw 'Another private directory reused the workspace identity.' }
$dataDirectory = $originalDirectory
. $identify
# Run the production launch/readiness/catch block; setup, network and processes are mocked.
$startup = $ast.Find({ param($node)
    $node -is [System.Management.Automation.Language.TryStatementAst] -and
    $node.CatchClauses.Count -eq 1 -and $node.Body.Extent.Text.Contains('$serverProcess = Start-Process')
}, $true)
if (-not $startup) { throw 'Launch cleanup block was not found.' }
$launch = [scriptblock]::Create($startup.Extent.Text)
$preflight = $ast.Find({ param($node)
    $node -is [System.Management.Automation.Language.IfStatementAst] -and
    $node.Extent.Text.Contains("'scripts/stop.ps1'")
}, $true)
if (-not $preflight) { throw 'Recorded process check was not found.' }
$preflight = [scriptblock]::Create($preflight.Extent.Text)
$healthStatements = $ast.EndBlock.Statements | Where-Object {
    $_.Extent.StartOffset -ge ($ast.EndBlock.Statements | Where-Object { $_.Extent.Text.Contains('-TimeoutSec 2') }).Extent.StartOffset -and
    $_.Extent.StartOffset -lt ($ast.EndBlock.Statements | Where-Object { $_.Extent.Text.StartsWith('$pidPath =') }).Extent.StartOffset
}
$healthProbe = [scriptblock]::Create(($healthStatements.Extent.Text -join "`n"))
function Start-Process {
    if ($mode -eq 'browser-failure') { throw 'Synthetic browser failure' }
    if ($env:MAESTRO_LAUNCH_ID -notmatch '^[a-f0-9]{32}$' -or $env:MAESTRO_LAUNCH_ID -eq $launchedNonce) { throw 'Launch identity was absent or reused.' }
    $script:launchedNonce = $env:MAESTRO_LAUNCH_ID
    $script:launches++
    @{ launch_id = $env:MAESTRO_LAUNCH_ID; port = $Port } | ConvertTo-Json -Compress | Set-Content -LiteralPath $addressPath
    if ($mode -like 'retry-*') { $fakeProcess.Id = 54321; $fakeProcess.HasExited = $false }
    if ($mode -eq 'success-changed-pid') { Set-Content -LiteralPath $pidPath -Value 99999 }
    return $fakeProcess
}
function Start-Sleep { $script:sleeps++ }
function Invoke-RestMethod {
    $script:polls++
    if ($mode -eq 'health-exit' -or ($mode -eq 'other-health-exit' -and $polls -eq 2)) { $fakeProcess.HasExited = $true }
    if ($mode -in @('success', 'success-changed-pid', 'health-exit', 'retry-success', 'retry-stale', 'browser-failure', 'other-health', 'missing-health', 'other-health-exit', 'eventual-health', 'wrong-archive-health', 'missing-archive-health', 'eventual-archive-health')) {
        $response = @{ application = 'maestro'; mode = 'local'; launch_id = $launchedNonce; workspace_id = $workspaceId; archive_id = $archiveId }
        if ($mode -like 'other-health*' -or ($mode -eq 'eventual-health' -and $polls -eq 1)) { $response.launch_id = 'another-launch' }
        if ($mode -eq 'missing-health') { $response.Remove('launch_id') }
        if ($mode -eq 'wrong-archive-health' -or ($mode -eq 'eventual-archive-health' -and $polls -eq 1)) { $response.archive_id = 'another-archive' }
        if ($mode -eq 'missing-archive-health') { $response.Remove('archive_id') }
        return $response
    }
    if ($mode -like 'reuse-*') {
        $response = @{ application = 'maestro'; mode = 'local'; workspace_id = $workspaceId; archive_id = $archiveId }
        if ($mode -eq 'reuse-different') { $response.workspace_id = 'another-workspace' }
        if ($mode -eq 'reuse-missing') { $response.Remove('workspace_id') }
        if ($mode -eq 'reuse-archive-different') { $response.archive_id = 'another-archive' }
        if ($mode -eq 'reuse-archive-missing') { $response.Remove('archive_id') }
        return $response
    }
    throw 'Synthetic health timeout'
}
function Get-CimInstance {
    if (-not $fakeProcess.HasExited) {
        return [pscustomobject]@{ ExecutablePath = $pythonPath; CommandLine = $(if ($mode -eq 'retry-unrelated') { 'python worker.py' } else { 'uvicorn backend.app:app' }) }
    }
}
function taskkill.exe {
    if (($args -join ' ') -ne '/PID 43210 /T /F') { throw 'Cleanup targeted an unexpected process.' }
    if ($mode -like 'cleanup-*') { $global:LASTEXITCODE = $(if ($mode -eq 'cleanup-live') { 0 } else { 1 }); return }
    $global:stopped = $true
    $fakeProcess.HasExited = $true
    if ($mode -eq 'stop-changed-pid') { Set-Content -LiteralPath $pidPath -Value 99999 }
    $global:LASTEXITCODE = 0
}
function Write-Warning { $script:cleanupWarnings++ }
$mode = 'browser-failure'; $NoBrowser = $false; $continued = $false; $failure = $null
try { . $healthProbe; $continued = $true } catch { $failure = $_.Exception.Message }
if ($continued -or $failure -ne 'Synthetic browser failure') { throw 'Browser failure retried an already-running server.' }
$NoBrowser = $true
foreach ($mode in @('reuse-same', 'reuse-different', 'reuse-missing', 'reuse-archive-different', 'reuse-archive-missing')) {
    $failure = $null; $launches = 0; $polls = 0; $output = $null
    try { $output = & $healthProbe } catch { $failure = $_.Exception.Message }
    if ($launches -or $polls -ne 1) { throw 'Workspace reuse started a process or retried a healthy server.' }
    if ($mode -eq 'reuse-same') {
        if ($failure -or $output -ne "Maestro is already running at $previewUrl") { throw 'Matching workspace did not reuse its healthy server.' }
    } elseif ($mode -like 'reuse-archive-*') {
        if ($failure -notlike '*different or unverified source archive*Stop*restart*MAESTRO_CONTEXT_ARCHIVE_DIR*' -or $stopped) { throw 'Archive mismatch was reused or stopped without an explicit restart.' }
    } elseif ($failure -notlike '*different or unverified private data directory*') { throw 'Workspace reuse accepted a conflicting or absent identity.' }
}
foreach ($mode in @('retry-success', 'retry-stale', 'retry-unrelated', 'cleanup-failure', 'cleanup-live')) {
    $fakeProcess = [pscustomobject]@{ Id = 43210; HasExited = ($mode -eq 'retry-stale') }
    $serverProcess = $null; $launches = 0; $stopped = $false; $failure = $null
    Set-Content -LiteralPath $pidPath -Value 43210
    try { . $preflight; . $launch } catch { $failure = $_.Exception.Message }
    if ($mode -in @('retry-success', 'retry-stale')) {
        if ($failure -or $launches -ne 1 -or $fakeProcess.HasExited -or (Get-Content -LiteralPath $pidPath) -ne '54321') { throw 'Retry did not resolve the recorded process before launching.' }
        if (($mode -eq 'retry-success') -ne $stopped) { throw 'Retry stopped an unexpected process.' }
    } elseif (-not $failure -or $launches -or $stopped -or (Get-Content -LiteralPath $pidPath) -ne '43210') { throw 'Retry replaced an unresolved process record.' }
}
foreach ($mode in @('exit', 'health-exit', 'timeout', 'cleanup-failure', 'cleanup-untracked', 'cleanup-changed-pid', 'cleanup-live', 'changed-pid', 'success-changed-pid', 'success', 'other-health', 'missing-health', 'other-health-exit', 'eventual-health', 'wrong-archive-health', 'missing-archive-health', 'eventual-archive-health')) {
    $fakeProcess = [pscustomobject]@{ Id = 43210; HasExited = ($mode -eq 'exit') }
    $serverProcess = $null
    $stopped = $false; $polls = 0; $sleeps = 0; $failure = $null; $cleanupWarnings = 0
    Remove-Item -LiteralPath $pidPath -ErrorAction SilentlyContinue
    if ($mode -notin @('success', 'success-changed-pid', 'cleanup-untracked', 'cleanup-live', 'eventual-health', 'eventual-archive-health')) { Set-Content -LiteralPath $pidPath -Value $(if ($mode -in @('changed-pid', 'cleanup-changed-pid', 'other-health')) { 99999 } else { 43210 }) }
    try { . $launch } catch { $failure = $_.Exception.Message }
    if ($mode -in @('success', 'eventual-health', 'eventual-archive-health')) {
        if ($failure -or $stopped -or $fakeProcess.HasExited -or (Get-Content -LiteralPath $pidPath) -ne '43210') { throw 'Success did not preserve its running server and PID.' }
        if ($mode -in @('eventual-health', 'eventual-archive-health') -and ($polls -ne 2 -or $sleeps -ne 1)) { throw 'Readiness accepted another launch/archive or failed to wait for its own server.' }
    } elseif ($mode -eq 'success-changed-pid') {
        if (-not $failure -or -not $stopped -or -not $fakeProcess.HasExited -or (Get-Content -LiteralPath $pidPath) -ne '99999') { throw 'Success replaced a concurrent launch PID.' }
    } else {
        $expected = if ($mode -in @('exit', 'health-exit', 'other-health-exit')) { "Maestro could not start. Check $dataDirectory\server.stderr.log" } else { "Maestro did not become ready. Check $dataDirectory\server.stderr.log" }
        if ($failure -ne $expected) { throw "Original startup failure was lost: $failure" }
        if ($mode -in @('timeout', 'changed-pid', 'other-health', 'missing-health', 'wrong-archive-health', 'missing-archive-health') -and (-not $stopped -or $polls -ne 30 -or $sleeps -ne 30)) { throw 'Unready launch did not stop its process tree after its bounded polls and waits.' }
        if ($mode -like 'cleanup-*') {
            $tracked = if ($mode -eq 'cleanup-changed-pid') { '99999' } else { '43210' }
            if ($fakeProcess.HasExited -or $cleanupWarnings -ne 1 -or (Get-Content -LiteralPath $pidPath) -ne $tracked) { throw 'Surviving launch lost its own record or overwrote another PID.' }
        } elseif ($mode -in @('changed-pid', 'other-health')) {
            if ((Get-Content -LiteralPath $pidPath) -ne '99999') { throw 'Cleanup removed another launch PID.' }
        } elseif (Test-Path -LiteralPath $pidPath) { throw 'Failed launch left its PID file.' }
    }
}
# Normal stop must also include the verified venv child process tree.
foreach ($mode in @('cleanup-failure', 'cleanup-live', 'stop-changed-pid', 'success')) {
    $fakeProcess.HasExited = $false; $failure = $null
    Set-Content -LiteralPath $pidPath -Value 43210
    try { & (Join-Path $repoDirectory 'scripts/stop.ps1') } catch { $failure = $_.Exception.Message }
    if ($mode -like 'cleanup-*') {
        if (-not $failure -or $fakeProcess.HasExited -or (Get-Content -LiteralPath $pidPath) -ne '43210') { throw 'Failed stop lost its live process record.' }
    } elseif ($mode -eq 'stop-changed-pid') {
        if ($failure -or (Get-Content -LiteralPath $pidPath) -ne '99999') { throw 'Stop removed another launch PID.' }
    } elseif ($failure -or -not $fakeProcess.HasExited -or (Test-Path -LiteralPath $pidPath)) { throw 'Normal stop did not stop its owned tree and remove its PID.' }
}
Write-Output 'Startup cleanup scenarios passed.'
"""
        with tempfile.TemporaryDirectory(prefix="maestro-startup-test-") as directory:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", harness],
                cwd=Path(__file__).resolve().parent.parent,
                env={**os.environ, "MAESTRO_DATA_DIR": directory, "MAESTRO_CONTEXT_ARCHIVE_DIR": "",
                     "MAESTRO_EXPECTED_WORKSPACE_ID": hashlib.sha256(os.path.normcase(str(Path(directory).resolve())).encode()).hexdigest(),
                     "MAESTRO_EXPECTED_ARCHIVE_ID": hashlib.sha256(b"none").hexdigest(),
                     "MAESTRO_EXPECTED_RELATIVE_ARCHIVE_ID": hashlib.sha256(b"invalid:relative/source-archive").hexdigest()},
                capture_output=True, text=True, timeout=20,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Startup cleanup scenarios passed.", result.stdout)


if __name__ == "__main__":
    unittest.main()
