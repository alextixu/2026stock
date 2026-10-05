$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'set_trading_env.ps1')) {
    . (Join-Path $PSScriptRoot 'set_trading_env.ps1')
}
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'set_model_env.ps1')) {
    . (Join-Path $PSScriptRoot 'set_model_env.ps1')
}
python -B -X utf8 (Join-Path $PSScriptRoot 'nightly_execute.py')
exit $LASTEXITCODE
