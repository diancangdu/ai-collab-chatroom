param(
    [ValidateSet("start","stop","status")]
    [string]$Action = "status",
    [string]$Project = "",
    [int]$IdleMinutes = 60,
    [switch]$NoAutoRelease
)
$ErrorActionPreference = "Continue"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
$Chat = Join-Path $Root "chatroom"
$DataDir = Join-Path $Chat "data"
$DispatcherDir = Join-Path $DataDir "dispatcher"
New-Item -ItemType Directory -Force -Path $DispatcherDir | Out-Null

# 项目根目录 config.json 可覆盖以下默认值；没有该文件也能直接跑
$Config = @{}
$ConfigPath = Join-Path $Root "config.json"
if (Test-Path $ConfigPath) {
    try { $Config = Get-Content $ConfigPath -Raw | ConvertFrom-Json } catch { $Config = @{} }
}

$HostAddr = if ($Config.host) { [string]$Config.host } else { "127.0.0.1" }
$Port = if ($Config.port) { [int]$Config.port } else { 8787 }
$Py = if ($Config.python) { [string]$Config.python } else { "python" }
$ZCodeExe = if ($Config.zcode_app) { [string]$Config.zcode_app } else { "" }
$OpenCodeExe = if ($Config.opencode_app) { [string]$Config.opencode_app } else { "" }

if ([string]::IsNullOrWhiteSpace($Project)) {
    $CwdLeaf = Split-Path (Get-Location) -Leaf
    if ([string]::IsNullOrWhiteSpace($CwdLeaf)) { $CwdLeaf = "main" }
    $Project = $CwdLeaf
}
$Project = (($Project.Trim().ToLowerInvariant()) -replace '[^a-z0-9\u4e00-\u9fff_-]+', '-').Trim('-')
if ([string]::IsNullOrWhiteSpace($Project)) { $Project = "main" }

$ChatServerPy = Join-Path $Chat "chatroom.py"
$MonitorPy = Join-Path $Chat "monitor.py"
$WakeRelayPy = Join-Path $Chat "wake_relay.py"
$OpenCodeLoopPy = Join-Path $Chat "opencode_loop.py"
$WatchdogPy = Join-Path $Chat "watchdog.py"
$DispatchIdlePy = Join-Path $Chat "dispatch_idle.py"
$ApiUrl = "http://{0}:{1}/api/send?project={2}" -f $HostAddr, $Port, [uri]::EscapeDataString($Project)
$StateFile = Join-Path $DispatcherDir ("dispatch_state." + $Project + ".json")

function Get-ProcCommandLine {
    param([int]$ProcessId)
    try {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
        return $p.CommandLine
    } catch {
        return ""
    }
}

function Find-RunningProc {
    param([string]$Needle)
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape($Needle) }
}

function Find-ProjectProc {
    param([string]$Script, [string]$ProjectName)
    $needle = [regex]::Escape($Script)
    $needle += ".+--project\s+" + [regex]::Escape($ProjectName)
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match $needle }
}

function Send-Chat {
    param([string]$Text)
    try {
        $payload = @{ name = "Codex"; text = $Text } | ConvertTo-Json -Compress
        $bytes = [Text.Encoding]::UTF8.GetBytes($payload)
        Invoke-WebRequest -Uri $ApiUrl -Method Post -ContentType "application/json; charset=utf-8" -Body $bytes -UseBasicParsing -TimeoutSec 5 | Out-Null
    } catch {
        Write-Host ("Chat notify failed: " + $_.Exception.Message)
    }
}

function Ensure-ChatServer {
    $hits = Find-RunningProc $ChatServerPy
    if ($hits) {
        return @{ name = "chatroom"; script = $ChatServerPy; pid = [int]$hits[0].ProcessId; source = "existing" }
    }
    $proc = Start-Process -FilePath $Py -ArgumentList @($ChatServerPy, "server", "--host", "$HostAddr", "--port", "$Port") -WorkingDirectory $Chat -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 2
    return @{ name = "chatroom"; script = $ChatServerPy; pid = $proc.Id; source = "new" }
}

function Start-OwnedProc {
    param([string]$Name, [string]$Script, [string[]]$ExtraArgs)
    $existing = Find-ProjectProc $Script $Project
    if ($existing) {
        return @{ name = $Name; script = $Script; pid = [int]$existing[0].ProcessId; source = "existing" }
    }
    $procArgs = @($Script) + $ExtraArgs
    $proc = Start-Process -FilePath $Py -ArgumentList $procArgs -WorkingDirectory $Chat -WindowStyle Hidden -PassThru
    return @{ name = $Name; script = $Script; pid = $proc.Id; source = "new" }
}

function Stop-LegacyWatchers {
    param([string]$ProjectName)
    $legacy = @($OpenCodeLoopPy, $WatchdogPy, $DispatchIdlePy, (Join-Path $Chat "opencodewatch.py"))
    foreach ($script in $legacy) {
        $hits = Find-ProjectProc $script $ProjectName
        foreach ($p in $hits) {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Host ("Stopped legacy watcher " + (Split-Path $script -Leaf) + " PID " + $p.ProcessId)
        }
    }
}

function Save-State {
    param($Obj)
    $Obj | ConvertTo-Json -Depth 6 | Set-Content -Path $StateFile -Encoding UTF8
}

function Get-State {
    if (Test-Path $StateFile) {
        try { return Get-Content $StateFile -Raw | ConvertFrom-Json } catch { return $null }
    }
    return $null
}

switch ($Action) {
    "start" {
        Write-Host "== Dispatch: START project=$Project =="
        $state = @{ action = "start"; project = $Project; idle_minutes = $IdleMinutes; auto_release = (-not $NoAutoRelease); ts = (Get-Date -Format "yyyy-MM-dd HH:mm:ss"); services = @(); apps = @() }
        $state.services += Ensure-ChatServer
        Stop-LegacyWatchers $Project
        $monitorArgs = @("--project", $Project, "--idle-minutes", "$IdleMinutes")
        if ($NoAutoRelease) { $monitorArgs += "--no-auto-release" }
        $state.services += Start-OwnedProc "monitor" $MonitorPy $monitorArgs
        $state.services += Start-OwnedProc "wake-relay" $WakeRelayPy @("--project", $Project)

        if ($ZCodeExe -and (Test-Path $ZCodeExe)) {
            $zname = [IO.Path]::GetFileNameWithoutExtension($ZCodeExe)
            if (Get-Process -Name $zname -ErrorAction SilentlyContinue) {
                $state.apps += "zcode:already-running"
            } else {
                try { Start-Process -FilePath $ZCodeExe; $state.apps += "zcode:launched" } catch { $state.apps += "zcode:launch-failed" }
            }
        } else {
            $state.apps += "zcode:not-configured"
        }

        if ($OpenCodeExe -and (Test-Path $OpenCodeExe)) {
            $oname = [IO.Path]::GetFileNameWithoutExtension($OpenCodeExe)
            if (Get-Process -Name $oname -ErrorAction SilentlyContinue) {
                $state.apps += "opencode:already-running"
            } else {
                try { Start-Process -FilePath $OpenCodeExe; $state.apps += "opencode:launched" } catch { $state.apps += "opencode:launch-failed" }
            }
        } else {
            $state.apps += "opencode:not-configured"
        }

        Save-State $state
        Send-Chat "@二哥 @三哥 大哥调度：项目 $Project 集合开工，请按 AGENTS.md / docs/COLLABORATION.md 流程报备后领活；任务完成我会发收工令并解除该项目值守；默认静默 $IdleMinutes 分钟自动收工，有任务发言即可重置计时"
        Send-Chat "聊天室 http://$HostAddr`:$Port/?project=$Project"
        $state | Format-List
    }
    "stop" {
        Write-Host "== Dispatch: STOP project=$Project =="
        $state = Get-State
        if ($state -and $state.services) {
            foreach ($svc in $state.services) {
                if ($svc.name -eq "chatroom") {
                    Write-Host ("chatroom shared, kept PID " + $svc.pid)
                    continue
                }
                if ($svc.source -eq "new") {
                    $cl = Get-ProcCommandLine ([int]$svc.pid)
                    $clNorm = if ($cl) { $cl -replace '\\', '/' } else { "" }
                    $pyNorm = [string]$Py -replace '\\', '/'
                    $scriptNorm = [string]$svc.script -replace '\\', '/'
                    if ($clNorm -and $clNorm -match [regex]::Escape($pyNorm) -and $clNorm -match [regex]::Escape($scriptNorm)) {
                        Stop-Process -Id ([int]$svc.pid) -Force -ErrorAction SilentlyContinue
                        Write-Host ("Stopped " + $svc.name + " PID " + $svc.pid)
                    } else {
                        Write-Host ($svc.name + " no longer running, skipped PID " + $svc.pid)
                    }
                } else {
                    Write-Host ($svc.name + " pre-existing, kept PID " + $svc.pid)
                }
            }
        } else {
            Write-Host "No dispatched services to stop"
        }
        $knownPids = @()
        if ($state -and $state.services) { $knownPids = @($state.services | ForEach-Object { [int]$_.pid }) }
        $orphanMon = Find-ProjectProc $MonitorPy $Project | Where-Object { $knownPids -notcontains [int]$_.ProcessId }
        foreach ($p in $orphanMon) {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Host ("Stopped orphan monitor PID " + $p.ProcessId)
        }
        $orphanRelay = Find-ProjectProc $WakeRelayPy $Project | Where-Object { $knownPids -notcontains [int]$_.ProcessId }
        foreach ($p in $orphanRelay) {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Host ("Stopped orphan wake-relay PID " + $p.ProcessId)
        }
        Stop-LegacyWatchers $Project
        Save-State @{ action = "stop"; project = $Project; ts = (Get-Date -Format "yyyy-MM-dd HH:mm:ss"); services = @(); apps = @() }
        Send-Chat "@二哥 @三哥 项目 $Project 本场协同结束，收工解散"
    }
    "status" {
        Write-Host "== Dispatch: STATUS project=$Project =="
        $state = Get-State
        if ($state) { $state | Format-List } else { Write-Host "No dispatch record for project $Project" }
        Write-Host "-- processes --"
        $cs = Find-RunningProc $ChatServerPy
        if ($cs) { Write-Host ("chatroom : running PID " + $cs[0].ProcessId) } else { Write-Host "chatroom : not running" }
        $mon = Find-ProjectProc $MonitorPy $Project
        if ($mon) { Write-Host ("monitor : running PID " + $mon[0].ProcessId) } else { Write-Host "monitor : not running" }
        $relay = Find-ProjectProc $WakeRelayPy $Project
        if ($relay) { Write-Host ("wake-relay : running PID " + $relay[0].ProcessId) } else { Write-Host "wake-relay : not running" }
        $legacy = @($OpenCodeLoopPy, $WatchdogPy, $DispatchIdlePy, (Join-Path $Chat "opencodewatch.py")) |
            ForEach-Object { Find-ProjectProc $_ $Project } | Where-Object { $_ }
        if ($legacy) {
            Write-Host ("legacy watchers still running: " + (($legacy | ForEach-Object { $_.ProcessId }) -join ","))
        } else {
            Write-Host "legacy watchers : none"
        }
        Write-Host ("ZCode app : " + $(if ($ZCodeExe -and (Get-Process -Name ([IO.Path]::GetFileNameWithoutExtension($ZCodeExe)) -ErrorAction SilentlyContinue)) { "running" } else { "not running / not configured" }))
        Write-Host ("OpenCode app : " + $(if ($OpenCodeExe -and (Get-Process -Name ([IO.Path]::GetFileNameWithoutExtension($OpenCodeExe)) -ErrorAction SilentlyContinue)) { "running" } else { "not running / not configured" }))
        Write-Host ("chat URL  : http://{0}:{1}/?project={2}" -f $HostAddr, $Port, $Project)
    }
}
