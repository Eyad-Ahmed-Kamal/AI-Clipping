[CmdletBinding()]
param(
    [switch]$Update,
    [switch]$Gpu,
    [ValidateSet('Antigravity','Codex','Both','None')]
    [string]$Target
)
$ErrorActionPreference = 'Stop'
function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed (exit $LASTEXITCODE)." }
}
Push-Location -LiteralPath $PSScriptRoot
try {
    . .\Activate-Clipping.ps1
    foreach ($tool in 'git','uv','ffmpeg','ffprobe','curl.exe') {
        if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
            throw "Missing $tool. Install the prerequisites listed in README.md, reopen PowerShell, and retry."
        }
    }
    foreach ($dir in 'temp','jobs','cache','vendor','models') {
        [IO.Directory]::CreateDirectory((Join-Path $PSScriptRoot $dir)) | Out-Null
    }
    $setupLock = [IO.File]::Open((Join-Path $PSScriptRoot 'cache/setup.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    $python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        Invoke-Checked uv @('venv','--python','3.12','--managed-python','.venv')
    }
    Invoke-Checked $python @('-c','import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) and sys.maxsize > 2**32 else "Expected 64-bit Python 3.12 in .venv")')
    $statePath = Join-Path $PSScriptRoot 'cache\setup-state.json'
    $state = if (Test-Path -LiteralPath $statePath) { Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json }
    if (-not $Target) { $Target = if ($state.target) { $state.target } else { 'Antigravity' } }
    $useGpu = $Gpu.IsPresent -or [bool]$state.gpu
    $install = @('pip','install','--python',$python,'-r','requirements-lock.txt')
    if ($useGpu) { $install += @('-r','requirements-gpu.txt') }
    Invoke-Checked uv $install
    Invoke-Checked $python @('tools/install_assets.py')
    Invoke-Checked $python @('clipping.py','doctor')
    if ($Target -ne 'None') {
        Invoke-Checked $python @('tools/install_plugin.py',$Target)
    }
    $stateJson = @{target=$Target; gpu=$useGpu} | ConvertTo-Json
    [IO.File]::WriteAllText($statePath, $stateJson)
    if ($Update) { Write-Host "Updated the workbench from the current checkout ($Target)." }
    else { Write-Host "Workbench ready ($Target)." }
} finally {
    if ($setupLock) { $setupLock.Dispose() }
    Pop-Location
}
