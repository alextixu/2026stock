# Copy to set_model_env.ps1 (git-ignored) and fill in your token locally.
$env:FINMIND_TOKEN = ''
$env:FINMIND_API_URL = 'https://api.finmindtrade.com/api/v4/data'
$env:SHARES_UNIT = 'lot'
Remove-Item Env:XGB38_QUARTER -ErrorAction SilentlyContinue
