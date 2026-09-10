<#
.SYNOPSIS
    Keep the widget running: start it at logon, and start it again if it stops.

.DESCRIPTION
    Registers a scheduled task with two triggers - one at logon, one repeating
    every few minutes. The repeating trigger is the watchdog, and it needs no
    process check of its own: the task is set to ignore a new instance while
    one is still running, so while the widget is alive the repeat does nothing,
    and the first repeat after it dies starts it again.

    The widget holds a named mutex for its own lifetime, so a second copy
    launched any other way exits quietly rather than stacking a second chip on
    the same pixel.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\autostart.ps1
    powershell -ExecutionPolicy Bypass -File tools\autostart.ps1 -Remove
#>
param(
    [switch]$Remove,
    [int]$EveryMinutes = 5,
    [string]$TaskName = "Smith Agents"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$module = Join-Path $root "smith_agents\__main__.py"

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    if ($TaskName -eq "Smith Agents" -and (Get-ScheduledTask -TaskName "Claude Usage Widget" -ErrorAction SilentlyContinue)) {
        Unregister-ScheduledTask -TaskName "Claude Usage Widget" -Confirm:$false
    }
    "Removed the task. The widget keeps running until you quit it."
    exit 0
}

if (-not (Test-Path $module)) { throw "No widget package beside tools\ (looked in $root)" }

# "pyw" on PATH can resolve to a different interpreter than "py", so ask "py"
# which Python it is and use the pythonw.exe sitting next to it - the same
# pairing run.cmd makes, and the one "py -m pip install" writes into.
$pythonw = & py -c "import os,sys;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"
if (-not (Test-Path $pythonw)) { throw "Could not find pythonw.exe next to the py launcher's interpreter" }

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "-m smith_agents" -WorkingDirectory $root

# At logon, after a short delay - the desktop and the tray are not ready the
# instant the session starts, and a tray icon registered too early is dropped.
$atLogon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$atLogon.Delay = "PT20S"

$watchdog = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes)

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName $TaskName -Action $action `
    -Trigger @($atLogon, $watchdog) -Settings $settings `
    -Description "Starts Smith Agents at logon and restarts it if it stops." `
    -Force | Out-Null

# Retire the old task only after the new registration succeeds.
if ($TaskName -eq "Smith Agents" -and (Get-ScheduledTask -TaskName "Claude Usage Widget" -ErrorAction SilentlyContinue)) {
    Unregister-ScheduledTask -TaskName "Claude Usage Widget" -Confirm:$false
}

"Registered '$TaskName': at logon, then every $EveryMinutes minutes if it is not running."
