# ASCII-path launcher for scheduled task (avoids Chinese path encoding issues in schtasks)
$ErrorActionPreference = "Stop"
$LocalAppData = [Environment]::GetFolderPath('LocalApplicationData')
if (-not $LocalAppData) {
    $LocalAppData = (Join-Path $HOME "AppData\Local")
}
$MarkerFile = Join-Path $LocalAppData "GRAVITY\project-root.txt"
if (-not (Test-Path $MarkerFile)) {
    throw "Missing $MarkerFile — run: python manage.py install-autostart"
}
$ProjectRoot = (Get-Content -Path $MarkerFile -Raw -Encoding UTF8).Trim()
$StartScript = Join-Path $ProjectRoot "scripts\Start-GRAVITY-All.ps1"
if (-not (Test-Path $StartScript)) {
    throw "Start script not found: $StartScript"
}
& powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File $StartScript
