# GRAVITY Host Bridge keeper — never exits; restarts listener if health fails.
# ASCII-only literals for Windows PowerShell 5.1 encoding safety.
param(
    [int]$Port = 9879,
    [int]$CheckIntervalSec = 30,
    [int]$StartWaitSec = 20
)

$ErrorActionPreference = "Continue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$BridgeScript = Join-Path $ProjectRoot "docker\host-bridge\host_bridge.ps1"
$LogDir = Join-Path $ProjectRoot "agent_memory"
$LogFile = Join-Path $LogDir "host_bridge.log"
$MutexName = "Global\GRAVITY-Host-Bridge-Keeper"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

function Write-KeepLog {
    param([string]$Message)
    $line = "[{0}] KEEP {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    try {
        Add-Content -Path $LogFile -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
    } catch {}
}

function Test-BridgeHealth {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health" -UseBasicParsing -TimeoutSec 3
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Get-BridgeProcesses {
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and (
                $_.CommandLine -like "*host_bridge.ps1*" -or
                $_.CommandLine -like "*host-bridge\host_bridge.ps1*"
            ) -and
            $_.CommandLine -notlike "*Keep-Host-Bridge.ps1*"
        }
}

function Stop-StaleBridge {
    $procs = @(Get-BridgeProcesses)
    foreach ($p in $procs) {
        Write-KeepLog "kill stale bridge pid=$($p.ProcessId)"
        try {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        } catch {}
    }
    if ($procs.Count -gt 0) {
        Start-Sleep -Seconds 2
    }
}

function Start-BridgeProcess {
    if (-not (Test-Path $BridgeScript)) {
        Write-KeepLog "ERROR missing $BridgeScript"
        return $false
    }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "powershell.exe"
    $psi.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$BridgeScript`" -Port $Port"
    $psi.WorkingDirectory = $ProjectRoot
    $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    $psi.CreateNoWindow = $true
    $psi.UseShellExecute = $false
    [void][System.Diagnostics.Process]::Start($psi)

    $deadline = (Get-Date).AddSeconds($StartWaitSec)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        if (Test-BridgeHealth) {
            Write-KeepLog "bridge healthy after start"
            return $true
        }
    }
    Write-KeepLog "ERROR bridge failed health after start"
    return $false
}

# Only one keeper
$mutex = $null
try {
    $mutex = New-Object System.Threading.Mutex($false, $MutexName)
    if (-not $mutex.WaitOne(0)) {
        Write-KeepLog "EXIT another keeper already running"
        exit 0
    }
} catch {
    Write-KeepLog "WARN keeper mutex failed: $($_.Exception.Message)"
}

Write-KeepLog "========== keeper start pid=$PID port=$Port =========="

try {
    while ($true) {
        if (Test-BridgeHealth) {
            Start-Sleep -Seconds $CheckIntervalSec
            continue
        }

        Write-KeepLog "health FAIL - restarting bridge"
        Stop-StaleBridge
        [void](Start-BridgeProcess)
        Start-Sleep -Seconds $CheckIntervalSec
    }
} finally {
    if ($mutex) {
        try { $mutex.ReleaseMutex() } catch {}
        try { $mutex.Dispose() } catch {}
    }
    Write-KeepLog "========== keeper exit pid=$PID =========="
}
