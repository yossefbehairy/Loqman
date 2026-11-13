# Mirrors AgentOS documentation into the Obsidian vault (one-way: repo -> vault).
# Hand-written notes (00 Home, Architecture, Backend Map) are never overwritten.
param([string]$Vault = "D:\ObsidianVault")
$repo = Split-Path -Parent $PSScriptRoot
$dest = Join-Path $Vault "AgentOS"
$dirs = @("Docs","Docs\Assets","Root")
foreach ($d in $dirs) { New-Item -ItemType Directory -Force (Join-Path $dest $d) | Out-Null }

# docs/*.md -> Docs, images/html -> Docs/Assets
Get-ChildItem "$repo\docs" -File | ForEach-Object {
  if ($_.Extension -eq ".md") { Copy-Item $_.FullName (Join-Path $dest "Docs\$($_.Name)") -Force }
  else { Copy-Item $_.FullName (Join-Path $dest "Docs\Assets\$($_.Name)") -Force }
}
# root markdown (README, SECURITY, ...)
Get-ChildItem "$repo\*.md" -File | ForEach-Object { Copy-Item $_.FullName (Join-Path $dest "Root\$($_.Name)") -Force }
# frontend README
if (Test-Path "$repo\frontend\README.md") { Copy-Item "$repo\frontend\README.md" (Join-Path $dest "Root\Frontend README.md") -Force }
# original plan documents
Get-ChildItem $repo -File | Where-Object { $_.Extension -in ".pdf",".docx" } |
  ForEach-Object { Copy-Item $_.FullName (Join-Path $dest "Docs\Assets\$($_.Name)") -Force }
Write-Host "Synced to $dest"
