param([switch]$NoBrowser)
$ErrorActionPreference = "Continue"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
$Chat = Join-Path $Root "chatroom"
$ChatServerPy = Join-Path $Chat "chatroom.py"

$Config = @{}
$ConfigPath = Join-Path $Root "config.json"
if (Test-Path $ConfigPath) {
    try { $Config = Get-Content $ConfigPath -Raw | ConvertFrom-Json } catch { $Config = @{} }
}

$HostAddr = if ($Config.host) { [string]$Config.host } else { "127.0.0.1" }
$Port = if ($Config.port) { [int]$Config.port } else { 8787 }
$Py = if ($Config.python) { [string]$Config.python } else { "python" }

$existing = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape($ChatServerPy) }
if ($existing) {
    Write-Host ("chatroom already running PID " + $existing[0].ProcessId)
} else {
    $proc = Start-Process -FilePath $Py -ArgumentList @($ChatServerPy, "server", "--host", "$HostAddr", "--port", "$Port") -WorkingDirectory $Chat -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 2
    Write-Host ("chatroom started PID " + $proc.Id)
}

if (-not $NoBrowser) {
    Start-Process ("http://{0}:{1}/" -f $HostAddr, $Port)
}
