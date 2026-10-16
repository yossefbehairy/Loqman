<#
.SYNOPSIS
    Smoke test for the frozen agentos-backend.exe (ONEFILE build).

.DESCRIPTION
    Spawns the exe with a fresh --data-dir, polls /api/health until the first
    200 response (measuring cold start), verifies agentos.db was created inside
    the data dir, then kills the process tree and dumps the logs.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File pyinstaller\smoke-test.ps1 -Port 8231
#>
param(
    [int]$Port = 8231,
    [int]$TimeoutSec = 120,
    [string]$ExePath = (Join-Path (Split-Path -Parent $PSScriptRoot) "backend\dist\agentos-backend.exe")
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $ExePath)) { throw "exe not found: $ExePath (build it first)" }

# Fresh temp data dir, like the Electron shell would pass via --data-dir.
$tmp = Join-Path $env:TEMP ("agentos-smoke-" + (Get-Random))
New-Item -ItemType Directory -Path $tmp | Out-Null
$stdout = Join-Path $tmp "stdout.log"
$stderr = Join-Path $tmp "stderr.log"

Write-Host "Exe      : $ExePath"
Write-Host "Port     : $Port"
Write-Host "Data dir : $tmp"

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$proc = Start-Process -FilePath $ExePath `
    -ArgumentList "--port", $Port, "--data-dir", $tmp `
    -PassThru `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr

$health = $null
$coldStartSec = $null
try {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ($true) {
        if ($proc.HasExited) { throw "process exited early with code $($proc.ExitCode) - see $stderr" }
        if ((Get-Date) -gt $deadline) { throw "no /api/health response within $TimeoutSec s" }
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 5
            $coldStartSec = [math]::Round($sw.Elapsed.TotalSeconds, 2)
            break
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
} finally {
    $sw.Stop()
}

Write-Host ""
Write-Host "Health JSON : $($health | ConvertTo-Json -Compress)"
Write-Host "Cold start  : $coldStartSec s (spawn -> first 200)"
$db = Join-Path $tmp "agentos.db"
Write-Host "agentos.db  : $(if (Test-Path $db) { 'PRESENT' } else { 'MISSING' })"

Write-Host "Data dir contents:"
Get-ChildItem $tmp | Select-Object Name, Length | Format-Table -AutoSize | Out-String | Write-Host

# Kill the whole process tree (onefile bootloader + python child).
if (-not $proc.HasExited) {
    & taskkill /PID $proc.Id /T /F 2>&1 | Out-Null
}

Write-Host "--- stdout.log (tail 20) ---"
if (Test-Path $stdout) { Get-Content $stdout -Tail 20 | Write-Host }
Write-Host "--- stderr.log (tail 20) ---"
if (Test-Path $stderr) { Get-Content $stderr -Tail 20 | Write-Host }

if (-not (Test-Path $db)) { exit 1 }
exit 0
