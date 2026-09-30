# Registers a per-user Windows task that computes tomorrow's plan every day at 21:00.
# It does not submit orders. Remove with: Unregister-ScheduledTask -TaskName 'XGB38-NightlyPlan'
param([string]$At = '21:00')
$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'run_nightly_plan.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $script + '"') `
    -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $At
# StartWhenAvailable: if the PC was off at 21:00, run once it is back on.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'XGB38-NightlyPlan' -Action $action -Trigger $trigger -Settings $settings `
    -Description 'XGB38: compute next-session BUY/SELL/HOLD plan (no orders are submitted)' -Force | Out-Null
Get-ScheduledTask -TaskName 'XGB38-NightlyPlan' | Get-ScheduledTaskInfo | Select-Object TaskName, NextRunTime
