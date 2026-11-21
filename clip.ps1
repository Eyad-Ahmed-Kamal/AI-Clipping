param([Parameter(ValueFromRemainingArguments=$true)][string[]]$ClipArguments)
. (Join-Path $PSScriptRoot 'Activate-Clipping.ps1')
& (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'clipping.py') @ClipArguments
exit $LASTEXITCODE
