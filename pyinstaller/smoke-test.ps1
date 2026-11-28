param(
    [int]$TimeoutSec = 60,
    [string]$ExePath = (Join-Path (Split-Path -Parent $PSScriptRoot) "backend\dist\agentos-backend.exe"),
    [string]$Python = (Join-Path (Split-Path -Parent $PSScriptRoot) "backend\.venv\Scripts\python.exe")
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $ExePath -PathType Leaf)) { throw "Executable not found: $ExePath" }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Python not found: $Python" }
if ($TimeoutSec -le 0) { throw "TimeoutSec must be positive" }
& $Python (Join-Path $PSScriptRoot "smoke_test.py") (Resolve-Path -LiteralPath $ExePath).Path $TimeoutSec
if ($LASTEXITCODE -ne 0) { throw "Frozen worker smoke test failed (exit $LASTEXITCODE)" }
