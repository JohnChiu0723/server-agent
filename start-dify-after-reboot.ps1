$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$logDir = Join-Path $projectRoot "agent_memory"
$logPath = Join-Path $logDir "start-dify-after-reboot.log"
$taskName = "Start Dify Selfhost After Reboot"
$startupWrapper = Join-Path ([Environment]::GetFolderPath('Startup')) "start-dify-after-reboot.cmd"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-Log {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $logPath -Value "[$timestamp] $Message"
}

Write-Log "Startup script begins."

try {
    $dockerDesktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (-not (Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue)) {
        Write-Log "Docker Desktop is not running. Starting it."
        Start-Process -FilePath $dockerDesktop
        Start-Sleep -Seconds 10
    }

    $ready = $false
    for ($i = 0; $i -lt 40; $i++) {
        docker version | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $ready = $true
            break
        }
        Start-Sleep -Seconds 5
    }

    if (-not $ready) {
        throw "Docker engine did not become ready within timeout."
    }

    Write-Log "Docker engine is ready. Starting Dify."
    Push-Location $projectRoot
    try {
        docker compose --env-file .env.dify -f docker-compose.dify.yml -p dify-selfhost up -d 2>&1 |
            Tee-Object -FilePath $logPath -Append
        if ($LASTEXITCODE -ne 0) {
            throw "docker compose up failed with exit code $LASTEXITCODE."
        }
    } finally {
        Pop-Location
    }

    Write-Log "Dify startup command finished."
} catch {
    Write-Log "Startup failed: $($_.Exception.Message)"
    throw
} finally {
    if (Test-Path $startupWrapper) {
        Remove-Item -Path $startupWrapper -Force -ErrorAction SilentlyContinue
        Write-Log "Startup wrapper removed."
    }
    schtasks /delete /tn $taskName /f | Out-Null
    Write-Log "Cleanup finished."
}
