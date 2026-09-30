$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'set_trading_env.ps1')) {
    . (Join-Path $PSScriptRoot 'set_trading_env.ps1')
}
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'set_model_env.ps1')) {
    . (Join-Path $PSScriptRoot 'set_model_env.ps1')
}
$guiPython = (Get-Command pythonw.exe -ErrorAction Stop).Source
$guiScript = Join-Path $PSScriptRoot 'trading_gui.py'
# The user requested a visible interactive GUI; pythonw creates no console window.
$guiProcess = Start-Process -FilePath $guiPython -ArgumentList @('-B', '-X', 'utf8', ('"' + $guiScript + '"')) -WorkingDirectory $PSScriptRoot -PassThru
Write-Output ('GUI started; process ID: ' + $guiProcess.Id)
