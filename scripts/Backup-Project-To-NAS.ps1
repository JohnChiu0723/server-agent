<#
.SYNOPSIS
    將整個「伺服器管家」專案增量備份到 NAS（WebDAV，透過 rclone）。

.DESCRIPTION
    - 每次執行建立一份以時間戳命名的獨立快照資料夾：server-steward_yyyy-MM-dd_HHmmss
    - 使用 --copy-dest 指向上一份快照：未變動的檔案由 NAS 端伺服器複製，不重新上傳。
    - 依 nas-backup.filter.txt 排除大型/可重生/已是備份的資料。
    - 只保留最新 N 份快照（預設 30），較舊者自動刪除。
    - 走 Cloudflare 的 WebDAV 單檔上限約 100MB，故以 --max-size 95M 作為保險（正常備份集內無此類大檔）。
    - 日誌寫到 %LOCALAPPDATA%\ServerStewardNasBackup\logs（不會被備份污染）。

.NOTES
    憑證存於 rclone 設定檔（%APPDATA%\rclone\rclone.conf），密碼以 rclone obscure 儲存。
#>
[CmdletBinding()]
param(
    [string]$Remote      = 'nas',
    [string]$RemoteBase  = 'home/server-steward-backup',
    [int]   $KeepVersions = 30,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

# --- 路徑準備 ---
$ScriptDir   = $PSScriptRoot
$ProjectRoot = Split-Path $ScriptDir -Parent
$FilterFile  = Join-Path $ScriptDir 'nas-backup.filter.txt'

$LogRoot = Join-Path $env:LOCALAPPDATA 'ServerStewardNasBackup\logs'
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
$Stamp   = Get-Date -Format 'yyyy-MM-dd_HHmmss'
$LogFile = Join-Path $LogRoot "backup_$Stamp.log"
$LockFile = Join-Path (Split-Path $LogRoot -Parent) 'backup.lock'

function Write-Log {
    param([string]$Msg, [string]$Level = 'INFO')
    $line = "{0} [{1}] {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Msg
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

# --- 前置檢查 ---
if (-not (Get-Command rclone -ErrorAction SilentlyContinue)) {
    Write-Log 'rclone 未安裝或不在 PATH 中。' 'ERROR'; exit 10
}
if (-not (Test-Path $FilterFile)) {
    Write-Log "找不到過濾檔：$FilterFile" 'ERROR'; exit 11
}

# --- 併發鎖：避免上一輪還沒跑完就重複啟動 ---
if (Test-Path $LockFile) {
    $age = (Get-Date) - (Get-Item $LockFile).LastWriteTime
    if ($age.TotalHours -lt 24) {
        Write-Log "偵測到執行中鎖檔（$([int]$age.TotalMinutes) 分鐘前），本次略過。" 'WARN'; exit 0
    }
    Write-Log '鎖檔已超過 24 小時，視為殘留，強制接手。' 'WARN'
}
Set-Content -Path $LockFile -Value $PID -Encoding ASCII

$Dst = "$($Remote):$RemoteBase"
$SnapName = "server-steward_$Stamp"
$SnapPath = "$Dst/$SnapName"

try {
    Write-Log "=============== 備份開始 ==============="
    Write-Log "來源：$ProjectRoot"
    Write-Log "目的：$SnapPath"

    # --- 找出上一份快照，供 --copy-dest 使用（伺服器端複製未變動檔案）---
    $prev = $null
    try {
        $dirs = & rclone lsf --dirs-only $Dst --timeout 60s 2>$null
        $prev = $dirs |
            Where-Object { $_ -match '^server-steward_' } |
            ForEach-Object { $_.TrimEnd('/') } |
            Sort-Object -Descending |
            Select-Object -First 1
    } catch { Write-Log "列出既有快照失敗（可能是首次備份）：$($_.Exception.Message)" 'WARN' }

    $rcloneArgs = @(
        'copy', $ProjectRoot, $SnapPath,
        '--filter-from', $FilterFile,
        '--skip-links',
        '--max-size', '95M',
        # 走 HTTP/1.1：Cloudflare 通道會發 HTTP/2 GOAWAY 導致上傳無法重試，停用 h2 可根治
        '--disable-http2',
        # 降低並發，減少 Cloudflare 高並發限流（<html> 錯誤）
        '--transfers', '4',
        '--checkers', '8',
        '--timeout', '120s',
        '--contimeout', '30s',
        '--retries', '5',
        '--retries-sleep', '15s',
        '--low-level-retries', '20',
        '--log-file', $LogFile,
        '--log-level', 'INFO',
        '--stats', '30s',
        '--stats-one-line'
    )
    if ($prev) {
        Write-Log "以上一份快照做增量：$prev"
        $rcloneArgs += @('--copy-dest', "$Dst/$prev")
    } else {
        Write-Log '找不到舊快照，本次為完整上傳。'
    }
    if ($DryRun) { $rcloneArgs += '--dry-run'; Write-Log '（DryRun 模式，不會真的上傳）' 'WARN' }

    Write-Log "執行： rclone $($rcloneArgs -join ' ')"
    & rclone @rcloneArgs
    $code = $LASTEXITCODE

    # 統計本次 rclone 的檔案層級錯誤（執行中系統偶有被鎖定/寫入中的檔案，屬可容忍的暫時性錯誤）
    $errLines = @()
    try { $errLines = @(Select-String -Path $LogFile -Pattern 'ERROR : ' -SimpleMatch | ForEach-Object { $_.Line }) } catch {}
    $errCount = $errLines.Count
    $ErrorTolerance = 30

    if ($code -eq 0) {
        Write-Log "備份完成（無錯誤）：$SnapName"
    }
    elseif ($errCount -le $ErrorTolerance) {
        Write-Log "備份完成，但有 $errCount 個檔案被略過（多為執行中被鎖定/寫入中的檔案）；結束碼=$code，視為成功。" 'WARN'
        $errLines | Select-Object -First $ErrorTolerance | ForEach-Object { Write-Log "  略過：$_" 'WARN' }
    }
    else {
        Write-Log "rclone 備份失敗（檔案錯誤 $errCount 筆，結束碼=$code）。不執行舊快照清理。" 'ERROR'
        exit $code
    }

    if ($DryRun) { Write-Log 'DryRun 結束。'; exit 0 }

    # --- 寫入一個人類可讀的說明檔到快照內 ---
    $infoTmp = Join-Path $env:TEMP "ssbackup_info_$Stamp.txt"
    @(
        "伺服器管家 專案備份"
        "快照時間 : $Stamp"
        "來源      : $ProjectRoot"
        "備份主機  : $env:COMPUTERNAME"
        "rclone    : $((rclone version)[0])"
        "基準快照  : $(if($prev){$prev}else{'（首次完整備份）'})"
    ) | Set-Content -Path $infoTmp -Encoding UTF8
    & rclone copyto $infoTmp "$SnapPath/_BACKUP_INFO.txt" --timeout 30s 2>$null
    Remove-Item $infoTmp -ErrorAction SilentlyContinue

    # --- 清理：只保留最新 KeepVersions 份 ---
    $all = & rclone lsf --dirs-only $Dst --timeout 60s 2>$null |
        Where-Object { $_ -match '^server-steward_' } |
        ForEach-Object { $_.TrimEnd('/') } |
        Sort-Object -Descending
    Write-Log "目前共有 $($all.Count) 份快照，保留上限 $KeepVersions。"
    if ($all.Count -gt $KeepVersions) {
        $toDelete = $all | Select-Object -Skip $KeepVersions
        foreach ($d in $toDelete) {
            Write-Log "刪除舊快照：$d"
            & rclone purge "$Dst/$d" --timeout 120s 2>&1 | Out-Null
        }
    }

    Write-Log "=============== 備份結束（成功）==============="
    exit 0
}
catch {
    Write-Log "未預期錯誤：$($_.Exception.Message)" 'ERROR'
    exit 1
}
finally {
    Remove-Item $LockFile -ErrorAction SilentlyContinue
}
