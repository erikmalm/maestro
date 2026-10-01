"""Exercise Windows launch cleanup with synthetic processes and temporary PID files."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


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
# Run the production launch/readiness/catch block; setup, network and processes are mocked.
$startup = $ast.Find({ param($node)
    $node -is [System.Management.Automation.Language.TryStatementAst] -and
    $node.CatchClauses.Count -eq 1 -and $node.Body.Extent.Text.Contains('$serverProcess = Start-Process')
}, $true)
if (-not $startup) { throw 'Launch cleanup block was not found.' }
$launch = [scriptblock]::Create($startup.Extent.Text)
function Start-Process { return $fakeProcess }
function Start-Sleep { }
function Invoke-RestMethod {
    $script:polls++
    if ($mode -eq 'health-exit') { $fakeProcess.HasExited = $true }
    if ($mode -in @('success', 'health-exit')) { return @{ application = 'maestro'; mode = 'local' } }
    throw 'Synthetic health timeout'
}
function taskkill.exe {
    if (($args -join ' ') -ne '/PID 43210 /T /F') { throw 'Cleanup targeted an unexpected process.' }
    if ($mode -like 'cleanup-*') { $global:LASTEXITCODE = $(if ($mode -eq 'cleanup-live') { 0 } else { 1 }); return }
    $script:stopped = $true
    $fakeProcess.HasExited = $true
    if ($mode -eq 'stop-changed-pid') { Set-Content -LiteralPath $pidPath -Value 99999 }
    $global:LASTEXITCODE = 0
}
function Write-Warning { $script:cleanupWarnings++ }
foreach ($mode in @('exit', 'health-exit', 'timeout', 'cleanup-failure', 'cleanup-untracked', 'cleanup-changed-pid', 'cleanup-live', 'changed-pid', 'success')) {
    $fakeProcess = [pscustomobject]@{ Id = 43210; HasExited = ($mode -eq 'exit') }
    $stopped = $false; $polls = 0; $failure = $null; $cleanupWarnings = 0
    Remove-Item -LiteralPath $pidPath -ErrorAction SilentlyContinue
    if ($mode -notin @('success', 'cleanup-untracked', 'cleanup-live')) { Set-Content -LiteralPath $pidPath -Value $(if ($mode -in @('changed-pid', 'cleanup-changed-pid')) { 99999 } else { 43210 }) }
    try { . $launch } catch { $failure = $_.Exception.Message }
    if ($mode -eq 'success') {
        if ($failure -or $stopped -or $fakeProcess.HasExited -or (Get-Content -LiteralPath $pidPath) -ne '43210') { throw 'Success did not preserve its running server and PID.' }
    } else {
        $expected = if ($mode -in @('exit', 'health-exit')) { "Maestro could not start. Check $dataDirectory\server.stderr.log" } else { "Maestro did not become ready. Check $dataDirectory\server.stderr.log" }
        if ($failure -ne $expected) { throw "Original startup failure was lost: $failure" }
        if ($mode -in @('timeout', 'changed-pid') -and (-not $stopped -or $polls -ne 30)) { throw 'Timeout did not stop the launched process tree after its bounded polls.' }
        if ($mode -like 'cleanup-*') {
            $tracked = if ($mode -eq 'cleanup-changed-pid') { '99999' } else { '43210' }
            if ($fakeProcess.HasExited -or $cleanupWarnings -ne 1 -or (Get-Content -LiteralPath $pidPath) -ne $tracked) { throw 'Surviving launch lost its own record or overwrote another PID.' }
        } elseif ($mode -eq 'changed-pid') {
            if ((Get-Content -LiteralPath $pidPath) -ne '99999') { throw 'Cleanup removed another launch PID.' }
        } elseif (Test-Path -LiteralPath $pidPath) { throw 'Failed launch left its PID file.' }
    }
}
# Normal stop must also include the verified venv child process tree.
function Get-CimInstance { if (-not $fakeProcess.HasExited) { return [pscustomobject]@{ ExecutablePath = $pythonPath; CommandLine = 'uvicorn backend.app:app' } } }
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
                env={**os.environ, "MAESTRO_DATA_DIR": directory},
                capture_output=True, text=True, timeout=20,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Startup cleanup scenarios passed.", result.stdout)


if __name__ == "__main__":
    unittest.main()
