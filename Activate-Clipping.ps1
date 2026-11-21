$clipRoot = $PSScriptRoot
$env:CLIPPING_ROOT = $clipRoot
$env:UV_CACHE_DIR = Join-Path $clipRoot 'cache\uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $clipRoot 'tools\python'
$env:HF_HOME = Join-Path $clipRoot 'cache\huggingface'
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'
$env:HF_HUB_DISABLE_XET = '1'
$env:XDG_CACHE_HOME = Join-Path $clipRoot 'cache'
$env:TEMP = Join-Path $clipRoot 'temp'
$env:TMP = $env:TEMP
$env:PYTHONUTF8 = '1'
$env:FFMPEG_ENCODER = 'auto'
$clipNvidiaRoot = Join-Path $clipRoot '.venv\Lib\site-packages\nvidia'
if (Test-Path -LiteralPath $clipNvidiaRoot) {
    $clipCudaBins = Get-ChildItem -LiteralPath $clipNvidiaRoot -Directory | ForEach-Object {
        $clipCudaBin = Join-Path $_.FullName 'bin'
        if (Test-Path -LiteralPath $clipCudaBin) { $clipCudaBin }
    }
    if ($clipCudaBins) { $env:Path = ($clipCudaBins -join ';') + ';' + $env:Path }
}
$env:Path = (Join-Path $clipRoot '.venv\Scripts') + ';' + $env:Path
