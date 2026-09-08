param(
    [int]$ApiPort = 8081,
    [int]$UiPort = 5173
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
$apiProcess = $null
$ownsApiProcess = $false

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

if (-not (Test-AmpgentApi)) {
    $outputDir = Join-Path $uiRoot 'output\dev'
    New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    $stdoutPath = Join-Path $outputDir 'data-service.out.log'
    $stderrPath = Join-Path $outputDir 'data-service.err.log'
    $previousPythonPath = $env:PYTHONPATH
    $env:PYTHONPATH = 'src'
    try {
        $apiProcess = Start-Process -FilePath $pythonPath `
            -ArgumentList @('-m', 'uvicorn', 'pepagent.api.main:app', '--host', '127.0.0.1', '--port', "$ApiPort") `
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
}
