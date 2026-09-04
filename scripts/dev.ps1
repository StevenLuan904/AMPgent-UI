param(
    [int]$ApiPort = 8081,
    [int]$UiPort = 5173,
    [switch]$FullControlPlane
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$uiRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$defaultBackendRoot = Join-Path (Split-Path -Parent $uiRoot) 'agent-platform'
$backendRoot = if ($env:AMPGENT_BACKEND_ROOT) { $env:AMPGENT_BACKEND_ROOT } else { $defaultBackendRoot }
$backendRoot = (Resolve-Path -LiteralPath $backendRoot).Path
$pythonPath = if ($env:AMPGENT_PYTHON) { $env:AMPGENT_PYTHON } else { Join-Path $backendRoot '.venv-local\Scripts\python.exe' }
$healthUrl = "http://127.0.0.1:$ApiPort/healthz"
$apiModule = if ($FullControlPlane) { 'pepagent.api.main:app' } else { 'observer_only:app' }
$databaseHost = '127.0.0.1'
$databasePort = 55432
$tunnelScript = Join-Path $backendRoot 'deploy\tunnels\start_019_pepagent_tunnels.ps1'
$apiProcess = $null
$ownsApiProcess = $false
$tunnelProcess = $null
$ownsTunnelProcess = $false

function Test-TcpPort {
    param(
        [string]$HostName,
        [int]$Port,
        [int]$TimeoutMilliseconds = 500
    )
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $pending = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne($TimeoutMilliseconds)) { return $false }
        $client.EndConnect($pending)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Test-AmpgentApi {
    try {
        $response = Invoke-RestMethod -Method Get -Uri $healthUrl -TimeoutSec 2
        return $response.status -eq 'ok'
    }
    catch {
        return $false
    }
}

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "未找到数据服务运行环境：$pythonPath。可通过 AMPGENT_PYTHON 指定 Python。"
}

if (-not (Test-TcpPort -HostName $databaseHost -Port $databasePort)) {
    if (-not (Test-Path -LiteralPath $tunnelScript)) {
        throw "安全隧道未连接：127.0.0.1:$databasePort；未找到只读隧道脚本，未启动本地 PostgreSQL。"
    }
    $outputDir = Join-Path $uiRoot 'output\dev'
    New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    $tunnelStdoutPath = Join-Path $outputDir 'database-tunnel.out.log'
    $tunnelStderrPath = Join-Path $outputDir 'database-tunnel.err.log'
    $tunnelProcess = Start-Process -FilePath 'powershell.exe' `
        -ArgumentList @('-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $tunnelScript) `
        -WorkingDirectory (Split-Path -Parent $tunnelScript) -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $tunnelStdoutPath -RedirectStandardError $tunnelStderrPath
    $ownsTunnelProcess = $true

    $tunnelReady = $false
    foreach ($attempt in 1..24) {
        if ($tunnelProcess.HasExited) { break }
        if (Test-TcpPort -HostName $databaseHost -Port $databasePort -TimeoutMilliseconds 400) {
            $tunnelReady = $true
            break
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $tunnelReady) {
        $details = if (Test-Path -LiteralPath $tunnelStderrPath) { (Get-Content -LiteralPath $tunnelStderrPath -Tail 8) -join "`n" } else { '隧道尚未监听本地端口。' }
        throw "安全隧道未连接：127.0.0.1:$databasePort；已尝试启动只读隧道但未在 12 秒内就绪。`n$details"
    }
    Write-Host "只读数据库隧道已就绪：127.0.0.1:$databasePort" -ForegroundColor Green
}
else {
    Write-Host "复用已存在的只读数据库隧道：127.0.0.1:$databasePort" -ForegroundColor Green
}

if (-not (Test-AmpgentApi)) {
    $outputDir = Join-Path $uiRoot 'output\dev'
    New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    $stdoutPath = Join-Path $outputDir 'data-service.out.log'
    $stderrPath = Join-Path $outputDir 'data-service.err.log'
    $previousPythonPath = $env:PYTHONPATH
    $env:PYTHONPATH = "$backendRoot\src;$uiRoot\scripts"
    try {
        $apiProcess = Start-Process -FilePath $pythonPath `
            -ArgumentList @('-m', 'uvicorn', $apiModule, '--host', '127.0.0.1', '--port', "$ApiPort") `
            -WorkingDirectory $backendRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
        $ownsApiProcess = $true
    }
    finally {
        $env:PYTHONPATH = $previousPythonPath
    }

    $connected = $false
    foreach ($attempt in 1..40) {
        if ($apiProcess.HasExited) { break }
        if (Test-AmpgentApi) { $connected = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $connected) {
        $details = if (Test-Path -LiteralPath $stderrPath) { (Get-Content -LiteralPath $stderrPath -Tail 12) -join "`n" } else { '无错误日志' }
        throw "数据服务未能启动。`n$details"
    }
    Write-Host "数据服务已连接：http://127.0.0.1:$ApiPort" -ForegroundColor Green
}
else {
    Write-Host "复用已运行的数据服务：http://127.0.0.1:$ApiPort" -ForegroundColor Green
}

$env:AMPGENT_API_TARGET = "http://127.0.0.1:$ApiPort"
$viteEntry = Join-Path $uiRoot 'node_modules\vite\bin\vite.js'
try {
    & node $viteEntry --host 127.0.0.1 --port $UiPort --strictPort
    exit $LASTEXITCODE
}
finally {
    if ($ownsApiProcess -and $apiProcess -and -not $apiProcess.HasExited) {
        Stop-Process -Id $apiProcess.Id -ErrorAction SilentlyContinue
    }
    if ($ownsTunnelProcess -and $tunnelProcess -and -not $tunnelProcess.HasExited) {
        Stop-Process -Id $tunnelProcess.Id -ErrorAction SilentlyContinue
    }
}
