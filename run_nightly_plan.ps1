$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
# Credentials are loaded here only to query holdings; nightly_plan.py never submits orders.
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'set_trading_env.ps1')) {
    . (Join-Path $PSScriptRoot 'set_trading_env.ps1')
}
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'set_model_env.ps1')) {
    . (Join-Path $PSScriptRoot 'set_model_env.ps1')
}
$python = (Get-Command pythonw.exe -ErrorAction Stop).Source
$process = Start-Process -FilePath $python -ArgumentList @('-B', '-X', 'utf8', ('"' + (Join-Path $PSScriptRoot 'nightly_plan.py') + '"')) -WorkingDirectory $PSScriptRoot -Wait -PassThru
exit $process.ExitCode
