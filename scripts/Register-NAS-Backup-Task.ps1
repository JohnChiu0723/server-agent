<#
.SYNOPSIS
    註冊 Windows 工作排程：每 2 天將整個專案增量備份到 NAS。

.DESCRIPTION
    沿用 GRAVITY 慣例，把 ASCII 路徑啟動器複製到 %LOCALAPPDATA%\ServerStewardNasBackup，
    並用 marker 檔記住中文專案路徑，避免排程器中文路徑亂碼。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\Register-NAS-Backup-Task.ps1
    powershell -ExecutionPolicy Bypass -File scripts\Register-NAS-Backup-Task.ps1 -At 03:30 -DaysInterval 2
#>
param(
    [string]$TaskName     = 'ServerSteward-NAS-Backup',
    [string]$At           = '04:00',
    [int]   $DaysInterval = 2,
    [int]   $KeepVersions = 30
)

$ErrorActionPreference = 'Stop'
$ProjectRoot   = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$BackupScript  = Join-Path $PSScriptRoot 'Backup-Project-To-NAS.ps1'
$User          = $env:USERNAME

if (-not (Test-Path $BackupScript)) { throw "找不到備份腳本：$BackupScript" }

# --- ASCII 工作目錄 + 專案根目錄 marker（避免排程器中文路徑亂碼）---
$LocalAppData = [Environment]::GetFolderPath('LocalApplicationData')
$WorkDir      = Join-Path $LocalAppData 'ServerStewardNasBackup'
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
Set-Content -Path (Join-Path $WorkDir 'project-root.txt') -Value $ProjectRoot -Encoding UTF8 -NoNewline

# --- 產生 ASCII 路徑啟動器：執行時才讀取中文路徑並呼叫真正的備份腳本 ---
$Launcher = Join-Path $WorkDir 'Run-NAS-Backup.ps1'
$launcherBody = @"
`$root = (Get-Content -Path '$(Join-Path $WorkDir 'project-root.txt')' -Raw -Encoding UTF8).Trim()
`$script = Join-Path `$root 'scripts\Backup-Project-To-NAS.ps1'
if (Test-Path `$script) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File `$script -KeepVersions $KeepVersions
} else {
    Write-Error "找不到備份腳本：`$script"
    exit 9
}
"@
Set-Content -Path $Launcher -Value $launcherBody -Encoding UTF8

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Launcher`"" `
    -WorkingDirectory $WorkDir

$trigger = New-ScheduledTaskTrigger -Daily -DaysInterval $DaysInterval -At $At

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 12)

$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "每 $DaysInterval 天將伺服器管家專案增量備份到 NAS（WebDAV / rclone），保留最新 $KeepVersions 份時間戳快照。" `
    -Force | Out-Null

Write-Host ''
Write-Host "[OK] 已註冊排程任務：$TaskName"
Write-Host "     觸發：每 $DaysInterval 天 $At"
Write-Host "     啟動器：$Launcher"
Write-Host "     專案：$ProjectRoot"
Write-Host "     保留份數：$KeepVersions"
Write-Host ''
Write-Host "立即手動測試： powershell -ExecutionPolicy Bypass -File `"$BackupScript`""
Write-Host "馬上跑這個排程： Start-ScheduledTask -TaskName $TaskName"
Write-Host "日誌位置： $(Join-Path $env:LOCALAPPDATA 'ServerStewardNasBackup\logs')"
Write-Host ''
