[CmdletBinding()]
param([switch]$SkipAntigravity, [switch]$SkipCodex)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Activate-Clipping.ps1')
if ($SkipAntigravity -and $SkipCodex) { return }
$target = if ($SkipCodex) { 'Antigravity' } elseif ($SkipAntigravity) { 'Codex' } else { 'Both' }
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $python (Join-Path $PSScriptRoot 'tools/install_plugin.py') $target
if ($LASTEXITCODE -ne 0) { throw "Plugin synchronization failed." }
