"""Exercise Windows launch cleanup with synthetic processes and temporary PID files."""

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class HealthTests(unittest.TestCase):
    def test_health_identifies_the_launched_server(self):
        with tempfile.TemporaryDirectory(prefix="maestro-health-test-") as directory:
            workspace_id = hashlib.sha256(os.path.normcase(str(Path(directory).resolve())).encode()).hexdigest()
            result = subprocess.run(
                [sys.executable, "-c", "from fastapi.testclient import TestClient; from backend.app import app; "
                 "response = TestClient(app).get('/health'); assert response.status_code == 200; "
                 f"assert response.json() == {{'application': 'maestro', 'mode': 'local', 'launch_id': 'test-launch', 'workspace_id': '{workspace_id}'}}"],
                cwd=Path(__file__).resolve().parent.parent,
                env={**os.environ, "MAESTRO_DATA_DIR": directory, "MAESTRO_LAUNCH_ID": "test-launch"},
                capture_output=True, text=True, timeout=20,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipUnless(os.name == "nt", "Windows PowerShell launcher")
class StartupTests(unittest.TestCase):
    def test_failed_launch_cleanup_and_successful_owned_process_tree(self):
        harness = r"""
$ErrorActionPreference = 'Stop'
$repoDirectory = (Get-Location).Path
$dataDirectory = $env:MAESTRO_DATA_DIR
$pythonPath = Join-Path $repoDirectory '.venv\Scripts\python.exe'
$previewUrl = 'http://127.0.0.1:8765'
$pidPath = Join-Path $dataDirectory 'server.pid'
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $repoDirectory 'scripts/start.ps1'), [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'Launcher did not parse.' }
$identityStatements = $ast.EndBlock.Statements | Where-Object {
    $_.Extent.StartOffset -ge ($ast.EndBlock.Statements | Where-Object { $_.Extent.Text.StartsWith('$identityPython =') }).Extent.StartOffset -and
    $_.Extent.StartOffset -lt ($ast.EndBlock.Statements | Where-Object { $_.Extent.Text.StartsWith('$previewUrl =') }).Extent.StartOffset
}
$identify = [scriptblock]::Create(($identityStatements.Extent.Text -join "`n"))
. $identify
if ($workspaceId -ne $env:MAESTRO_EXPECTED_WORKSPACE_ID) { throw 'Launcher and backend workspace identities differ.' }
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
    if ($mode -like 'retry-*') { $fakeProcess.Id = 54321; $fakeProcess.HasExited = $false }
    if ($mode -eq 'success-changed-pid') { Set-Content -LiteralPath $pidPath -Value 99999 }
    return $fakeProcess
}
function Start-Sleep { $script:sleeps++ }
function Invoke-RestMethod {
    $script:polls++
    if ($mode -eq 'health-exit' -or ($mode -eq 'other-health-exit' -and $polls -eq 2)) { $fakeProcess.HasExited = $true }
    if ($mode -in @('success', 'success-changed-pid', 'health-exit', 'retry-success', 'retry-stale', 'browser-failure', 'other-health', 'missing-health', 'other-health-exit', 'eventual-health')) {
        $response = @{ application = 'maestro'; mode = 'local'; launch_id = $launchedNonce; workspace_id = $workspaceId }
        if ($mode -like 'other-health*' -or ($mode -eq 'eventual-health' -and $polls -eq 1)) { $response.launch_id = 'another-launch' }
        if ($mode -eq 'missing-health') { $response.Remove('launch_id') }
        return $response
    }
    if ($mode -like 'reuse-*') {
        $response = @{ application = 'maestro'; mode = 'local'; workspace_id = $workspaceId }
        if ($mode -eq 'reuse-different') { $response.workspace_id = 'another-workspace' }
        if ($mode -eq 'reuse-missing') { $response.Remove('workspace_id') }
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
foreach ($mode in @('reuse-same', 'reuse-different', 'reuse-missing')) {
    $failure = $null; $launches = 0; $polls = 0; $output = $null
    try { $output = & $healthProbe } catch { $failure = $_.Exception.Message }
    if ($launches -or $polls -ne 1) { throw 'Workspace reuse started a process or retried a healthy server.' }
    if ($mode -eq 'reuse-same') {
        if ($failure -or $output -ne "Maestro is already running at $previewUrl") { throw 'Matching workspace did not reuse its healthy server.' }
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
foreach ($mode in @('exit', 'health-exit', 'timeout', 'cleanup-failure', 'cleanup-untracked', 'cleanup-changed-pid', 'cleanup-live', 'changed-pid', 'success-changed-pid', 'success', 'other-health', 'missing-health', 'other-health-exit', 'eventual-health')) {
    $fakeProcess = [pscustomobject]@{ Id = 43210; HasExited = ($mode -eq 'exit') }
    $serverProcess = $null
    $stopped = $false; $polls = 0; $sleeps = 0; $failure = $null; $cleanupWarnings = 0
    Remove-Item -LiteralPath $pidPath -ErrorAction SilentlyContinue
    if ($mode -notin @('success', 'success-changed-pid', 'cleanup-untracked', 'cleanup-live', 'eventual-health')) { Set-Content -LiteralPath $pidPath -Value $(if ($mode -in @('changed-pid', 'cleanup-changed-pid', 'other-health')) { 99999 } else { 43210 }) }
    try { . $launch } catch { $failure = $_.Exception.Message }
    if ($mode -in @('success', 'eventual-health')) {
        if ($failure -or $stopped -or $fakeProcess.HasExited -or (Get-Content -LiteralPath $pidPath) -ne '43210') { throw 'Success did not preserve its running server and PID.' }
        if ($mode -eq 'eventual-health' -and ($polls -ne 2 -or $sleeps -ne 1)) { throw 'Readiness accepted another launch or failed to wait for its own server.' }
    } elseif ($mode -eq 'success-changed-pid') {
        if (-not $failure -or -not $stopped -or -not $fakeProcess.HasExited -or (Get-Content -LiteralPath $pidPath) -ne '99999') { throw 'Success replaced a concurrent launch PID.' }
    } else {
        $expected = if ($mode -in @('exit', 'health-exit', 'other-health-exit')) { "Maestro could not start. Check $dataDirectory\server.stderr.log" } else { "Maestro did not become ready. Check $dataDirectory\server.stderr.log" }
        if ($failure -ne $expected) { throw "Original startup failure was lost: $failure" }
        if ($mode -in @('timeout', 'changed-pid', 'other-health', 'missing-health') -and (-not $stopped -or $polls -ne 30 -or $sleeps -ne 30)) { throw 'Unready launch did not stop its process tree after its bounded polls and waits.' }
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
                env={**os.environ, "MAESTRO_DATA_DIR": directory,
                     "MAESTRO_EXPECTED_WORKSPACE_ID": hashlib.sha256(os.path.normcase(str(Path(directory).resolve())).encode()).hexdigest()},
                capture_output=True, text=True, timeout=20,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Startup cleanup scenarios passed.", result.stdout)


if __name__ == "__main__":
    unittest.main()
