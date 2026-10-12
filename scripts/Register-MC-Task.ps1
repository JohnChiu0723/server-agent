# Register one Minecraft server as a Task Scheduler task with native restart-on-failure
# (replaces the PowerShell restart loop). Project root is read from the UTF-16 marker,
# so the Chinese project path stays correct; SubDir is passed as an ASCII argument.
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads BOM-less .ps1 as Big5,
# which corrupts non-ASCII string literals and breaks parsing.
param(
    [Parameter(Mandatory = $true)][string]$TaskName,
    [Parameter(Mandatory = $true)][string]$SubDir,
    [string]$Title = "GRAVITY MC",
    [int]$DelaySeconds = 120,
    [string]$Affinity = "ffff",
    [switch]$HighPriority,
    [string]$Python = $(if ($env:GRAVITY_PYTHON) { $env:GRAVITY_PYTHON } else { (Get-Command python.exe -ErrorAction SilentlyContinue).Source })
)

$ErrorActionPreference = "Stop"

$local = [Environment]::GetFolderPath('LocalApplicationData')
if (-not $local) { $local = (Join-Path $HOME "AppData\Local") }
$marker = Join-Path $local "GRAVITY\project-root.txt"
if (-not (Test-Path $marker)) { throw "marker not found: $marker (run install-autostart first)" }

$fso = New-Object -ComObject Scripting.FileSystemObject
$reader = $fso.OpenTextFile($marker, 1, $false, -1)
$root = $reader.ReadAll().Trim()
$reader.Close()

$WorkingDir = Join-Path $root $SubDir
if (-not (Test-Path $WorkingDir)) { throw "server dir not found: $WorkingDir" }

$user = $env:USERNAME

# cmd /c start: opens a normal console window, pins CPU affinity, /wait so the
# scheduler can detect a crash (non-zero exit) and restart the task.
$prio = ""
if ($HighPriority) { $prio = "/high " }
$argLine = '/c start "' + $Title + '" /D "' + $WorkingDir + '" /affinity ' + $Affinity + ' ' + $prio + '/wait "' + $Python + '" -m mcdreforged start'

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $argLine
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$trigger.Delay = "PT${DelaySeconds}S"
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "GRAVITY MC ($SubDir) - native scheduler restart, normal window" `
    -Force | Out-Null

Write-Host "[OK] $TaskName -> $WorkingDir (delay ${DelaySeconds}s, affinity $Affinity)"
