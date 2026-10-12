# 背景啟動 MCDR（無視窗），供開機自啟使用；手動管理請用各目錄的 Start-*-Server.bat
param(
    [Parameter(Mandatory = $true)]
    [string]$WorkingDir,
    [string]$Python = $(if ($env:GRAVITY_PYTHON) { $env:GRAVITY_PYTHON } else { (Get-Command python.exe -ErrorAction SilentlyContinue).Source }),
    [uint64]$Affinity = 0,
    [switch]$RestartLoop
)

$ErrorActionPreference = "Continue"
$LogDir = Join-Path (Split-Path $WorkingDir -Parent) "agent_memory"
if (-not (Test-Path $LogDir)) { $LogDir = $WorkingDir }
$Name = Split-Path $WorkingDir -Leaf
$LogFile = Join-Path $LogDir "mcdr-$Name.log"

function Write-McdrLog([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

do {
    Write-McdrLog "starting mcdreforged in $WorkingDir"
    $p = Start-Process -FilePath $Python `
        -ArgumentList "-m", "mcdreforged", "start" `
        -WorkingDirectory $WorkingDir `
        -PassThru `
        -WindowStyle Hidden
    if ($Affinity -gt 0) {
        try {
            $p.ProcessorAffinity = $Affinity
            $p.PriorityClass = [System.Diagnostics.ProcessPriorityClass]::High
        } catch {
            Write-McdrLog "affinity/priority skipped: $_"
        }
    }
    $p.WaitForExit()
    $code = $p.ExitCode
    Write-McdrLog "mcdreforged exited code=$code"
    if (-not $RestartLoop) { break }
    Start-Sleep -Seconds 5
} while ($true)
