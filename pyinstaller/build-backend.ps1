<#
.SYNOPSIS
    Builds the AgentOS FastAPI backend into a single-file Windows executable.

.DESCRIPTION
    Idempotent build: clears previous outputs, runs PyInstaller (ONEFILE spec)
    from the repo root with the backend venv interpreter, then copies the exe
    to frontend\build-resources\ (where the Electron packaging step picks it up).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File pyinstaller\build-backend.ps1
#>

$ErrorActionPreference = "Stop"

# --- Paths -------------------------------------------------------------------
$RepoRoot  = Split-Path -Parent $PSScriptRoot            # D:\AgentOS
$VenvPy    = Join-Path $RepoRoot "backend\.venv\Scripts\python.exe"
$SpecFile  = Join-Path $RepoRoot "pyinstaller\agentos-backend.spec"
$DistDir   = Join-Path $RepoRoot "backend\dist"
$BuildDir  = Join-Path $RepoRoot "backend\build"
$ExePath   = Join-Path $DistDir "agentos-backend.exe"
$Resources = Join-Path $RepoRoot "frontend\build-resources"
$DestExe   = Join-Path $Resources "agentos-backend.exe"

if (-not (Test-Path $VenvPy))   { throw "venv python not found: $VenvPy" }
if (-not (Test-Path $SpecFile)) { throw "spec file not found: $SpecFile" }

# --- Clean previous outputs (idempotent rebuilds) ----------------------------
foreach ($dir in @($DistDir, $BuildDir)) {
    if (Test-Path $dir) {
        Write-Host "Cleaning $dir ..."
        Remove-Item -Recurse -Force $dir
    }
}
New-Item -ItemType Directory -Path $Resources -Force | Out-Null

# --- Build (from repo root; absolute --distpath/--workpath) ------------------
Push-Location $RepoRoot
try {
    Write-Host "Building agentos-backend.exe with PyInstaller (ONEFILE)..."
    & $VenvPy -m PyInstaller --noconfirm --clean --distpath $DistDir --workpath $BuildDir $SpecFile
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}

if (-not (Test-Path $ExePath)) { throw "build did not produce $ExePath" }

# --- Publish next to Electron build resources --------------------------------
Copy-Item -Force $ExePath $DestExe

$sizeMb = [math]::Round((Get-Item $ExePath).Length / 1MB, 2)
Write-Host ""
Write-Host ("BUILD OK  : {0} ({1} MB)" -f $ExePath, $sizeMb)
Write-Host ("COPIED TO : {0} ({1} MB)" -f $DestExe, ([math]::Round((Get-Item $DestExe).Length / 1MB, 2)))
