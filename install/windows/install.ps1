<#
.SYNOPSIS
  Install x17tune as a Windows scheduled task (runs elevated at logon).

.DESCRIPTION
  - Creates a venv in C:\ProgramData\x17tune\venv and installs this repo into it.
  - Registers the task "x17tune". By default it runs in DRY-RUN mode (logs what it
    would do, changes nothing). Re-run with -Apply once the dry-run log looks right.
  - Logs: C:\ProgramData\x17tune\x17tune.log (rotating). Add -Telemetry for a CSV.

  Prerequisites (see docs/05-autotune.md):
    * Python 3.11+ from python.org (tick "Add to PATH")
    * LibreHardwareMonitor running at startup (CPU temp + package power)
    * Optional: MSI Afterburner with undervolt curves saved in profile slots 1-3

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install\windows\install.ps1
  powershell -ExecutionPolicy Bypass -File install\windows\install.ps1 -Apply
#>
param(
  [switch]$Apply,
  [switch]$Telemetry,
  [double]$Ambient = 0
)
$ErrorActionPreference = 'Stop'

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  throw "Run this from an elevated (Administrator) PowerShell."
}

$repo  = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$state = Join-Path $env:ProgramData 'x17tune'
$venv  = Join-Path $state 'venv'
New-Item -ItemType Directory -Force -Path $state | Out-Null

$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { throw "python not found in PATH - install Python 3.11+ from python.org" }
& $py.Source -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) { throw "Python 3.11+ required" }

if (-not (Test-Path $venv)) { & $py.Source -m venv $venv }
$vpy  = Join-Path $venv 'Scripts\python.exe'
$vpyw = Join-Path $venv 'Scripts\pythonw.exe'
& $vpy -m pip install --upgrade pip | Out-Null
& $vpy -m pip install "$repo[windows]"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host "`nCapability probe (read-only):" -ForegroundColor Cyan
& $vpy -m x17tune probe

$taskArgs = @('-m', 'x17tune', 'run', '--logfile', (Join-Path $state 'x17tune.log'))
if ($Apply)     { $taskArgs += '--apply' }
if ($Telemetry) { $taskArgs += @('--log', (Join-Path $state 'telemetry.csv')) }
if ($Ambient -gt 0) { $taskArgs += @('--ambient', "$Ambient") }

$action    = New-ScheduledTaskAction -Execute $vpyw -Argument ($taskArgs -join ' ')
$trigger   = New-ScheduledTaskTrigger -AtLogOn
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
               -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$taskPrincipal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -RunLevel Highest -LogonType Interactive

Register-ScheduledTask -TaskName 'x17tune' -Action $action -Trigger $trigger -Settings $settings `
  -Principal $taskPrincipal -Force | Out-Null
Start-ScheduledTask -TaskName 'x17tune'

$mode = if ($Apply) { 'APPLY (tuning for real)' } else { 'DRY-RUN (nothing is changed)' }
Write-Host "`nx17tune installed and started in $mode mode." -ForegroundColor Green
Write-Host "Log: $state\x17tune.log"
Write-Host "Stop + restore stock: install\windows\uninstall.ps1  (or: $vpy -m x17tune restore)"
