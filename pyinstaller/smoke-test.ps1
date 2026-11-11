<#
.SYNOPSIS
    Smoke test for the frozen agentos-backend.exe (stdlib-only Python worker).

.DESCRIPTION
    Spawns the exe with a fresh --data-dir and the mock engine, waits for the
    "ready" line (cold start), sends `health`, checks agentos.db was created,
    then asks the worker to shut down and verifies it exits cleanly.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File pyinstaller\smoke-test.ps1
#>
param(
    [int]$TimeoutSec = 60,
    [string]$ExePath = (Join-Path (Split-Path -Parent $PSScriptRoot) "backend\dist\agentos-backend.exe")
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $ExePath)) { throw "exe not found: $ExePath (build it first)" }

$tmp = Join-Path $env:TEMP ("agentos-smoke-" + (Get-Random))
New-Item -ItemType Directory -Path $tmp | Out-Null
Write-Host "Exe      : $ExePath"
Write-Host "Data dir : $tmp"

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $ExePath
$psi.Arguments = "--data-dir `"$tmp`" --runtime-mode mock"
$psi.RedirectStandardInput = $true
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$proc = [System.Diagnostics.Process]::Start($psi)
try {
    $readyTask = $proc.StandardOutput.ReadLineAsync()
    if (-not $readyTask.Wait($TimeoutSec * 1000)) { throw "no ready line within $TimeoutSec s" }
    $cold = [math]::Round($sw.Elapsed.TotalSeconds, 2)
    $ready = $readyTask.Result | ConvertFrom-Json
    if ($ready.type -ne "ready") { throw "unexpected first line: $($readyTask.Result)" }
    Write-Host "Ready in $cold s  (protocol $($ready.protocol), $($ready.methods) methods)"

    $proc.StandardInput.WriteLine('{"id":1,"method":"health"}')
    $line = $proc.StandardOutput.ReadLine()
    Write-Host "Health   : $line"
    if (($line | ConvertFrom-Json).result.status -ne "ok") { throw "health failed" }

    if (-not (Test-Path (Join-Path $tmp "agentos.db"))) { throw "agentos.db was not created" }
    $proc.StandardInput.WriteLine('{"id":2,"method":"shutdown"}')
    $null = $proc.StandardOutput.ReadLine()
    if (-not $proc.WaitForExit(10000)) { throw "worker did not exit after shutdown" }
    Write-Host "Exit code: $($proc.ExitCode)"
    if ($proc.ExitCode -ne 0) { throw "non-zero exit" }
    Write-Host "SMOKE TEST PASSED"
} finally {
    if (-not $proc.HasExited) { $proc.Kill() }
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
