# GRAVITY boot autostart: Agent stack + 3 Minecraft servers
param(
    [int]$DockerWaitSeconds = 240,
    [int]$HostBridgePort = 9879
)

$ErrorActionPreference = "Continue"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$LogDir = Join-Path $ProjectRoot "agent_memory"
$LogFile = Join-Path $LogDir "boot.log"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

function Write-BootLog {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Test-HttpOk {
    param([string]$Url, [int]$TimeoutSec = 5)
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
        return $r.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Test-HostBridge {
    return (Test-HttpOk -Url "http://127.0.0.1:$HostBridgePort/health")
}

function Start-HiddenProcess {
    param(
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$WorkingDirectory = $null
    )
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    $escaped = $ArgumentList | ForEach-Object {
        if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
    }
    $psi.Arguments = $escaped -join ' '
    if ($WorkingDirectory) { $psi.WorkingDirectory = $WorkingDirectory }
    $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    $psi.CreateNoWindow = $true
    $psi.UseShellExecute = $false
    [System.Diagnostics.Process]::Start($psi) | Out-Null
}

function Start-HostBridge {
    if (Test-HostBridge) {
        Write-BootLog "Host Bridge already running on port $HostBridgePort"
        return $true
    }
    $keep = Join-Path $ProjectRoot "scripts\Keep-Host-Bridge.ps1"
    $script = Join-Path $ProjectRoot "docker\host-bridge\host_bridge.ps1"
    if (Test-Path $keep) {
        Start-HiddenProcess -FilePath "powershell.exe" `
            -ArgumentList @(
                "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-WindowStyle", "Hidden", "-File", $keep, "-Port", "$HostBridgePort"
            )
    } elseif (Test-Path $script) {
        Start-HiddenProcess -FilePath "powershell.exe" `
            -ArgumentList @(
                "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-WindowStyle", "Hidden", "-File", $script
            )
    } else {
        Write-BootLog "ERROR missing Host Bridge scripts"
        return $false
    }
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 2
        if (Test-HostBridge) {
            Write-BootLog "Host Bridge started (keeper)"
            return $true
        }
    }
    Write-BootLog "ERROR Host Bridge health check failed"
    return $false
}

function Wait-DockerReady {
    param([int]$MaxSeconds)
    $deadline = (Get-Date).AddSeconds($MaxSeconds)
    while ((Get-Date) -lt $deadline) {
        docker info 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            return $true
        }
        Start-Sleep -Seconds 5
    }
    return $false
}

function Start-DockerStack {
    if (-not (Wait-DockerReady -MaxSeconds $DockerWaitSeconds)) {
        Write-BootLog "ERROR Docker not ready - enable Docker Desktop at sign-in"
        return $false
    }
    Write-BootLog "Docker ready, running docker compose up -d"
    Push-Location $ProjectRoot
    try {
        & docker compose up -d 2>&1 | ForEach-Object { Write-BootLog $_ }
        if ($LASTEXITCODE -ne 0) {
            Write-BootLog "ERROR docker compose exit code: $LASTEXITCODE"
            return $false
        }
        Write-BootLog "docker compose up -d OK (Dify + agent_proxy)"
        # 確保 ssrf_proxy 正常（常見原因：SSRF_PROXY_ALLOW_PRIVATE_IPS=true）
        $ssrf = docker compose ps ssrf_proxy --format "{{.Status}}" 2>$null
        if ($ssrf -match "Restarting") {
            Write-BootLog "WARN ssrf_proxy restarting, attempting recreate"
            & docker compose up -d --force-recreate ssrf_proxy 2>&1 | ForEach-Object { Write-BootLog $_ }
            Start-Sleep -Seconds 8
        }
        return $true
    } finally {
        Pop-Location
    }
}

function Start-GameServer {
    param(
        [string]$Name,
        [string]$RelativeDir,
        [uint64]$Affinity = 0,
        [switch]$RestartLoop
    )
    $dir = Join-Path $ProjectRoot $RelativeDir
    $hiddenScript = Join-Path $ProjectRoot "scripts\Start-MCDR-Hidden.ps1"
    if (-not (Test-Path $hiddenScript)) {
        Write-BootLog "ERROR missing hidden MCDR script: $hiddenScript"
        return $false
    }
    $running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*mcdreforged*" -and $_.CommandLine -like "*$RelativeDir*" }
    if ($running) {
        Write-BootLog "$Name already running PID $($running.ProcessId)"
        return $true
    }
    $psArgs = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
        "-File", $hiddenScript,
        "-WorkingDir", $dir
    )
    if ($Affinity -gt 0) {
        $psArgs += "-Affinity", "$Affinity"
    }
    if ($RestartLoop) {
        $psArgs += "-RestartLoop"
    }
    Start-HiddenProcess -FilePath "powershell.exe" -ArgumentList $psArgs -WorkingDirectory $ProjectRoot
    Write-BootLog "$Name launch triggered (hidden background)"
    return $true
}

function Write-HealthSummary {
    $checks = @(
        @{ Name = "Host Bridge :$HostBridgePort"; Url = "http://127.0.0.1:$HostBridgePort/health" },
        @{ Name = "agent_proxy :8788"; Url = "http://127.0.0.1:8788/health" },
        @{ Name = "Dify :8080"; Url = "http://127.0.0.1:8080" }
    )
    $allOk = $true
    foreach ($c in $checks) {
        $ok = Test-HttpOk -Url $c.Url -TimeoutSec 10
        $status = if ($ok) { "OK" } else { "FAIL" }
        Write-BootLog "HEALTH $($c.Name) = $status"
        if (-not $ok) { $allOk = $false }
    }
    $ssrfStatus = docker compose -f (Join-Path $ProjectRoot "docker-compose.yml") ps ssrf_proxy --format "{{.Status}}" 2>$null
    if ($ssrfStatus) {
        $ssrfOk = $ssrfStatus -notmatch "Restarting|Exited"
        Write-BootLog "HEALTH ssrf_proxy = $(if ($ssrfOk) { 'OK' } else { 'FAIL' }) ($ssrfStatus)"
        if (-not $ssrfOk) { $allOk = $false }
    }
    if ($allOk) {
        Write-BootLog "HEALTH SUMMARY: ALL PASS"
    } else {
        Write-BootLog "HEALTH SUMMARY: SOME FAILED — check docker compose ps"
    }
    return $allOk
}

Write-BootLog "========== GRAVITY boot start =========="
Write-BootLog "Project: $ProjectRoot"

# 防止排程重入（同時只跑一份）
$bootLock = Join-Path $LogDir "boot.lock"
if (Test-Path $bootLock) {
    $age = (Get-Date) - (Get-Item $bootLock).LastWriteTime
    if ($age.TotalMinutes -lt 10) {
        Write-BootLog "SKIP another boot run in progress (lock age $([int]$age.TotalSeconds)s)"
        return
    }
}
Set-Content -Path $bootLock -Value (Get-Date -Format "o") -Encoding UTF8

try {
    # 只負責基建：Host Bridge + Docker（Dify/agent_proxy）。
    # 三個 MC 伺服器改由獨立工作排程任務啟動（GRAVITY-MC-*），
    # 用排程器原生「失敗自動重啟」取代 PowerShell 迴圈，避免多餘保姆程序。
    Start-HostBridge | Out-Null
    Start-DockerStack | Out-Null

    Start-Sleep -Seconds 5
    Write-HealthSummary | Out-Null
} finally {
    Remove-Item -Path $bootLock -Force -ErrorAction SilentlyContinue
}

Write-BootLog "========== GRAVITY boot end =========="
