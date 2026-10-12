# 註冊 Windows 工作排程：登入後自動啟動 GRAVITY Agent 基建 + 三個 MC 伺服器
# 建議以系統管理員執行一次：powershell -ExecutionPolicy Bypass -File scripts\Install-GRAVITY-Autostart.ps1

param(
    [string]$TaskName = "GRAVITY-AutoStart",
    [int]$DelaySeconds = 90
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$StartScript = Join-Path $PSScriptRoot "Start-GRAVITY-All.ps1"
$LauncherScript = Join-Path $PSScriptRoot "Start-GRAVITY-Launcher.ps1"
$User = $env:USERNAME

if (-not (Test-Path $StartScript)) {
    throw "找不到啟動腳本: $StartScript"
}
if (-not (Test-Path $LauncherScript)) {
    throw "找不到啟動器: $LauncherScript"
}

# ASCII 路徑啟動器 + 專案根目錄標記（避免 schtasks 中文路徑亂碼）
$LocalAppData = [Environment]::GetFolderPath('LocalApplicationData')
if (-not $LocalAppData) {
    $LocalAppData = (Join-Path $HOME "AppData\Local")
}
$GravityDir = Join-Path $LocalAppData "GRAVITY"
if (-not (Test-Path $GravityDir)) {
    New-Item -ItemType Directory -Path $GravityDir -Force | Out-Null
}
Set-Content -Path (Join-Path $GravityDir "project-root.txt") -Value $ProjectRoot -Encoding UTF8 -NoNewline
Copy-Item -Path $LauncherScript -Destination (Join-Path $GravityDir "Start-GRAVITY-Launcher.ps1") -Force

$TaskLauncher = Join-Path $GravityDir "Start-GRAVITY-Launcher.ps1"

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$TaskLauncher`"" `
    -WorkingDirectory $GravityDir

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
$trigger.Delay = "PT${DelaySeconds}S"

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "GRAVITY autostart: Dify, agent_proxy, Host Bridge, 3 MC servers" `
    -Force | Out-Null

# Host Bridge 提早 30 秒啟動（與總任務互補）
$hostBridgeScript = Join-Path $ProjectRoot "docker\host-bridge\host_bridge.ps1"
if ($ProjectRoot -and (Test-Path $hostBridgeScript)) {
    $hbMarker = Join-Path $GravityDir "host-bridge-root.txt"
    Set-Content -Path $hbMarker -Value $hostBridgeScript -Encoding UTF8 -NoNewline
    $hbLauncher = @"
`$script = (Get-Content -Path '$hbMarker' -Raw -Encoding UTF8).Trim()
if (Test-Path `$script) { & powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `$script }
"@
    $hbLauncherPath = Join-Path $GravityDir "Start-Host-Bridge.ps1"
    Set-Content -Path $hbLauncherPath -Value $hbLauncher -Encoding UTF8
    $hbAction = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$hbLauncherPath`"" `
        -WorkingDirectory $GravityDir
    $hbTrigger = New-ScheduledTaskTrigger -AtLogOn -User $User
    $hbTrigger.Delay = "PT30S"
    Register-ScheduledTask `
        -TaskName "GRAVITY-Host-Bridge" `
        -Action $hbAction `
        -Trigger $hbTrigger `
        -Settings $settings `
        -Principal $principal `
        -Description "GRAVITY Host Bridge early start" `
        -Force | Out-Null
}

Write-Host ""
Write-Host "[OK] Registered scheduled task: $TaskName"
Write-Host "     Trigger: $User logon + ${DelaySeconds}s delay"
Write-Host "     Launcher: $TaskLauncher"
Write-Host "     Project:  $ProjectRoot"
Write-Host ""
Write-Host "Manual test: powershell -ExecutionPolicy Bypass -File `"$TaskLauncher`""
Write-Host "Log file: $(Join-Path $ProjectRoot 'agent_memory\boot.log')"
Write-Host ""
Write-Host "Also enable: Docker Desktop -> Settings -> Start Docker Desktop when you sign in"
Write-Host ""
