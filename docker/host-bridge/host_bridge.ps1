# GRAVITY Host Bridge — Windows host PowerShell / HTTP / filesystem for Docker agent_proxy
# Listens on 127.0.0.1 only. Self-healing outer loop; never exits on request errors.
# NOTE: keep string literals ASCII-safe for Windows PowerShell 5.1 encoding.
param(
    [int]$Port = 9879,
    [int]$MaxOutputChars = 160000,
    [int]$DefaultExecTimeoutSec = 300,
    [int]$RestartDelaySec = 3
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
$LogDir = Join-Path $ProjectRoot "agent_memory"
$LogFile = Join-Path $LogDir "host_bridge.log"
$MutexName = "Global\GRAVITY-Host-Bridge-Listener"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

function Write-HbLog {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    try {
        Add-Content -Path $LogFile -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
    } catch {}
}

function Test-HbHealth {
    param([int]$CheckPort = $Port)
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$CheckPort/health" -UseBasicParsing -TimeoutSec 2
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

# Single instance: if another healthy listener owns the port, exit quietly.
$mutex = $null
try {
    $mutex = New-Object System.Threading.Mutex($false, $MutexName)
    if (-not $mutex.WaitOne(0)) {
        if (Test-HbHealth) {
            Write-HbLog "EXIT another healthy Host Bridge already running on $Port"
            exit 0
        }
        Write-HbLog "WARN mutex held but health failed; waiting briefly"
        if (-not $mutex.WaitOne(5000)) {
            Write-HbLog "EXIT could not acquire mutex"
            exit 0
        }
    }
} catch {
    Write-HbLog "WARN mutex setup failed: $($_.Exception.Message)"
}

function Send-Json {
    param($Context, $Obj, [int]$Code = 200)
    $json = $Obj | ConvertTo-Json -Depth 12 -Compress
    $buf = [System.Text.Encoding]::UTF8.GetBytes($json)
    $Context.Response.StatusCode = $Code
    $Context.Response.ContentType = "application/json; charset=utf-8"
    $Context.Response.ContentLength64 = $buf.Length
    $Context.Response.OutputStream.Write($buf, 0, $buf.Length)
    $Context.Response.Close()
}

function Truncate-Text([string]$Text) {
    if ($null -eq $Text) { $Text = "" }
    if ($Text.Length -le $MaxOutputChars) { return @{ text = $Text; truncated = $false } }
    return @{ text = $Text.Substring(0, $MaxOutputChars); truncated = $true }
}

function Read-Body($Request) {
    if (-not $Request.HasEntityBody) { return @{} }
    $reader = New-Object System.IO.StreamReader($Request.InputStream, $Request.ContentEncoding)
    try {
        $raw = $reader.ReadToEnd()
    } finally {
        $reader.Close()
    }
    if (-not $raw) { return @{} }
    return ($raw | ConvertFrom-Json)
}

function Invoke-ExecCommand {
    param(
        [string]$Command,
        [string]$Cwd,
        [int]$TimeoutSec
    )
    $stdout = ""
    $stderr = ""
    $exit = 0
    $timedOut = $false

    $encoded = [Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes($Command))
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "powershell.exe"
    $psi.Arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand $encoded"
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    if ($Cwd -and (Test-Path -LiteralPath $Cwd)) {
        $psi.WorkingDirectory = $Cwd
    }

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    [void]$proc.Start()
    if (-not $proc.WaitForExit($TimeoutSec * 1000)) {
        $timedOut = $true
        try { $proc.Kill() } catch {}
        try { $proc.WaitForExit(5000) } catch {}
        $stderr = "exec timed out after ${TimeoutSec}s"
        $exit = 124
    } else {
        $exit = $proc.ExitCode
    }
    $stdout = $proc.StandardOutput.ReadToEnd()
    $errTail = $proc.StandardError.ReadToEnd()
    if ($errTail) {
        if ($stderr) { $stderr = $stderr + "`n" + $errTail } else { $stderr = $errTail }
    }
    return @{
        stdout    = $stdout
        stderr    = $stderr
        exit_code = $exit
        timed_out = $timedOut
    }
}

function Start-HbListener {
    $listener = New-Object System.Net.HttpListener
    $prefix = "http://127.0.0.1:$Port/"
    $listener.Prefixes.Add($prefix)
    try {
        $listener.Start()
    } catch {
        if (Test-HbHealth) {
            throw "port $Port already healthy elsewhere"
        }
        throw
    }
    Write-HbLog "LISTEN $prefix (pid=$PID)"

    try {
        while ($listener.IsListening) {
            $ctx = $null
            try {
                $ctx = $listener.GetContext()
                $req = $ctx.Request
                $path = $req.Url.AbsolutePath.TrimEnd("/")
                if (-not $path) { $path = "/" }

                if ($path -eq "/health") {
                    Send-Json $ctx @{
                        ok      = $true
                        service = "GRAVITY Host Bridge"
                        port    = $Port
                        os      = "Windows"
                        pid     = $PID
                    }
                    continue
                }

                $payload = Read-Body $req

                if ($path -eq "/exec" -and $req.HttpMethod -eq "POST") {
                    $cmd = [string]$payload.command
                    if (-not $cmd.Trim()) { throw "command is required" }
                    $cwd = [string]$payload.cwd
                    $timeoutSec = $DefaultExecTimeoutSec
                    if ($payload.timeout_sec) {
                        $timeoutSec = [int]$payload.timeout_sec
                        if ($timeoutSec -lt 1) { $timeoutSec = 1 }
                        if ($timeoutSec -gt 600) { $timeoutSec = 600 }
                    }
                    $result = Invoke-ExecCommand -Command $cmd -Cwd $cwd -TimeoutSec $timeoutSec
                    $combined = [string]$result.stdout + [string]$result.stderr
                    $out = Truncate-Text $combined
                    Send-Json $ctx @{
                        ok         = (($result.exit_code -eq 0) -and (-not $result.timed_out))
                        exit_code  = $result.exit_code
                        stdout     = $out.text
                        truncated  = $out.truncated
                        timed_out  = $result.timed_out
                    }
                    continue
                }

                if ($path -eq "/fetch" -and $req.HttpMethod -eq "POST") {
                    $url = [string]$payload.url
                    if (-not $url.Trim()) { throw "url is required" }
                    $method = if ($payload.method) { [string]$payload.method } else { "GET" }
                    $headers = @{}
                    if ($payload.headers) {
                        $payload.headers.PSObject.Properties | ForEach-Object {
                            $headers[$_.Name] = [string]$_.Value
                        }
                    }
                    $params = @{
                        Uri             = $url
                        Method          = $method
                        UseBasicParsing = $true
                        TimeoutSec      = 120
                    }
                    if ($headers.Count -gt 0) { $params.Headers = $headers }
                    if ($payload.body) { $params.Body = [string]$payload.body }
                    $resp = Invoke-WebRequest @params
                    $content = Truncate-Text ([string]$resp.Content)
                    Send-Json $ctx @{
                        ok          = $true
                        status_code = [int]$resp.StatusCode
                        url         = $url
                        content     = $content.text
                        truncated   = $content.truncated
                    }
                    continue
                }

                if ($path -eq "/read_file" -and $req.HttpMethod -eq "POST") {
                    $fp = [string]$payload.path
                    if (-not $fp.Trim()) { throw "path is required" }
                    if (-not (Test-Path -LiteralPath $fp)) { throw "file not found: $fp" }
                    $text = Get-Content -LiteralPath $fp -Raw -Encoding UTF8
                    $content = Truncate-Text $text
                    Send-Json $ctx @{
                        ok        = $true
                        path      = $fp
                        content   = $content.text
                        truncated = $content.truncated
                    }
                    continue
                }

                if ($path -eq "/write_file" -and $req.HttpMethod -eq "POST") {
                    $fp = [string]$payload.path
                    $content = [string]$payload.content
                    if (-not $fp.Trim()) { throw "path is required" }
                    $dir = Split-Path -Parent $fp
                    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
                        New-Item -ItemType Directory -Path $dir -Force | Out-Null
                    }
                    Set-Content -LiteralPath $fp -Value $content -Encoding UTF8
                    Send-Json $ctx @{ ok = $true; path = $fp; bytes = $content.Length }
                    continue
                }

                if ($path -eq "/list_dir" -and $req.HttpMethod -eq "POST") {
                    $dir = if ($payload.path) { [string]$payload.path } else { "C:\\" }
                    if (-not (Test-Path -LiteralPath $dir)) { throw "path not found: $dir" }
                    $items = Get-ChildItem -LiteralPath $dir -Force -ErrorAction SilentlyContinue |
                        Select-Object -First 500 |
                        ForEach-Object {
                            @{
                                name = $_.Name + $(if ($_.PSIsContainer) { "/" } else { "" })
                                path = $_.FullName
                                type = if ($_.PSIsContainer) { "dir" } else { "file" }
                            }
                        }
                    Send-Json $ctx @{ ok = $true; path = $dir; entries = @($items); count = @($items).Count }
                    continue
                }

                Send-Json $ctx @{ ok = $false; error = "not found: $path" } 404
            } catch {
                Write-HbLog "REQ error: $($_.Exception.Message)"
                if ($ctx) {
                    try {
                        Send-Json $ctx @{ ok = $false; error = $_.Exception.Message } 500
                    } catch {
                        Write-HbLog "RESP error: $($_.Exception.Message)"
                    }
                }
            }
        }
    } finally {
        try { $listener.Stop() } catch {}
        try { $listener.Close() } catch {}
        Write-HbLog "LISTENER stopped"
    }
}

Write-HbLog "========== Host Bridge start pid=$PID port=$Port =========="

try {
    while ($true) {
        try {
            Start-HbListener
            Write-HbLog "WARN listener loop ended unexpectedly; restarting in ${RestartDelaySec}s"
        } catch {
            $msg = $_.Exception.Message
            Write-HbLog "FATAL listener: $msg"
            if ($msg -match "healthy elsewhere") {
                Write-HbLog "EXIT yielding to healthy peer"
                break
            }
        }
        Start-Sleep -Seconds $RestartDelaySec
    }
} finally {
    if ($mutex) {
        try { $mutex.ReleaseMutex() } catch {}
        try { $mutex.Dispose() } catch {}
    }
    Write-HbLog "========== Host Bridge exit pid=$PID =========="
}
