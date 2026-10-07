<#
.SYNOPSIS
  Stop x17tune, restore stock behavior and remove the scheduled task.
  Add -Purge to also delete C:\ProgramData\x17tune (venv, logs, heatsink baseline).
#>
param([switch]$Purge)
$ErrorActionPreference = 'Continue'
$state = Join-Path $env:ProgramData 'x17tune'
$vpy = Join-Path $state 'venv\Scripts\python.exe'

Stop-ScheduledTask -TaskName 'x17tune' -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName 'x17tune' -Confirm:$false -ErrorAction SilentlyContinue
if (Test-Path $vpy) { & $vpy -m x17tune restore }
if ($Purge) { Remove-Item -Recurse -Force $state }
Write-Host "x17tune removed; AWCC is back on Balanced, GPU clocks unlocked, power plan restored."
