# Register GRAVITY-Host-Bridge as a Task Scheduler job with native restart-on-failure.
# ASCII-only literals for Windows PowerShell 5.1 encoding safety.
param(
    [string]$TaskName = "GRAVITY-Host-Bridge",
    [int]$DelaySeconds = 20,
    [int]$Port = 9879
)

$ErrorActionPreference = "Stop"

$local = [Environment]::GetFolderPath('LocalApplicationData')
if (-not $local) { $local = (Join-Path $HOME "AppData\Local") }
$gravity = Join-Path $local "GRAVITY"
if (-not (Test-Path $gravity)) {
    New-Item -ItemType Directory -Path $gravity -Force | Out-Null
}

$marker = Join-Path $gravity "project-root.txt"
if (-not (Test-Path $marker)) {
    throw "marker not found: $marker (run install-autostart first)"
}

$fso = New-Object -ComObject Scripting.FileSystemObject
$reader = $fso.OpenTextFile($marker, 1, $false, -1)
$root = $reader.ReadAll().Trim()
$reader.Close()

$keepScript = Join-Path $root "scripts\Keep-Host-Bridge.ps1"
if (-not (Test-Path $keepScript)) {
    throw "missing keeper script: $keepScript"
}

# ASCII launcher under LocalAppData avoids Chinese path issues in schtasks.
$launcher = Join-Path $gravity "Keep-Host-Bridge.ps1"
$launcherBody = @"
`$ErrorActionPreference = 'Continue'
`$marker = Join-Path `$env:LOCALAPPDATA 'GRAVITY\project-root.txt'
if (-not (Test-Path `$marker)) { exit 1 }
`$fso = New-Object -ComObject Scripting.FileSystemObject
`$reader = `$fso.OpenTextFile(`$marker, 1, `$false, -1)
`$root = `$reader.ReadAll().Trim()
`$reader.Close()
`$keep = Join-Path `$root 'scripts\Keep-Host-Bridge.ps1'
if (-not (Test-Path `$keep)) { exit 1 }
& `$keep -Port $Port
"@
Set-Content -Path $launcher -Value $launcherBody -Encoding ASCII

$user = $env:USERNAME
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`"" `
    -WorkingDirectory $gravity

$triggerLogon = New-ScheduledTaskTrigger -AtLogOn -User $user
$triggerLogon.Delay = "PT${DelaySeconds}S"

# Periodic safety net: if keeper died without scheduler noticing, start again.
$triggerRepeat = New-ScheduledTaskTrigger `
    -Once `
    -At ((Get-Date).AddMinutes(2)) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 9999)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1)

$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest

try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger @($triggerLogon, $triggerRepeat) `
        -Settings $settings `
        -Principal $principal `
        -Description "GRAVITY Host Bridge keeper (auto-restart + 5min safety net)" `
        -Force | Out-Null
} catch {
    # Highest may require elevation; fall back to Limited.
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger @($triggerLogon, $triggerRepeat) `
        -Settings $settings `
        -Principal $principal `
        -Description "GRAVITY Host Bridge keeper (auto-restart + 5min safety net)" `
        -Force | Out-Null
}

Write-Host "[OK] $TaskName registered (logon+${DelaySeconds}s, restart forever, every 5min safety)"
