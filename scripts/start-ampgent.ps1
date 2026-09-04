param(
    [int]$UiPort = 5173,
    [int]$ApiPort = 8081
)

$ErrorActionPreference = 'Stop'
$appRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$appUrl = "http://127.0.0.1:$UiPort/"
$apiUrl = "http://127.0.0.1:$ApiPort/healthz"
$proxiedApiUrl = "http://127.0.0.1:$UiPort/healthz"
$observerProtocolVersion = 'ampgent-observer/v2'
$observerServiceVersion = 'observer-only-cache-v2'
$defaultBackendRoot = Join-Path (Split-Path -Parent $appRoot) 'agent-platform'
$backendRoot = if ($env:AMPGENT_BACKEND_ROOT) { $env:AMPGENT_BACKEND_ROOT } else { $defaultBackendRoot }
$observerSourcePath = Join-Path $appRoot 'scripts\observer_only.py'
$observerRouterPath = Join-Path $backendRoot 'src\pepagent\api\observer.py'

function Get-ObserverSourceFingerprint {
    if (-not (Test-Path -LiteralPath $observerSourcePath) -or -not (Test-Path -LiteralPath $observerRouterPath)) {
        return $null
    }
    $stream = [System.IO.MemoryStream]::new()
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        foreach ($source in @(
            @{ Label = 'observer_only.py'; Path = $observerSourcePath },
            @{ Label = 'pepagent/api/observer.py'; Path = $observerRouterPath }
        ) | Sort-Object Label) {
            $labelBytes = [System.Text.Encoding]::UTF8.GetBytes($source.Label)
            $stream.Write($labelBytes, 0, $labelBytes.Length)
            $stream.Write([byte[]](0), 0, 1)
            $contentBytes = [System.IO.File]::ReadAllBytes($source.Path)
            $stream.Write($contentBytes, 0, $contentBytes.Length)
            $stream.Write([byte[]](0), 0, 1)
        }
        return ([System.BitConverter]::ToString($sha.ComputeHash($stream.ToArray())) -replace '-', '').ToLowerInvariant()
    }
    catch {
        return $null
    }
    finally {
        $sha.Dispose()
        $stream.Dispose()
    }
}

$observerSourceFingerprint = Get-ObserverSourceFingerprint

function Test-AmpgentUi {
    try {
        $response = Invoke-WebRequest -Method Get -Uri $appUrl -TimeoutSec 2 -UseBasicParsing
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

function Test-AmpgentEndpoint([string]$url) {
    try {
        $response = Invoke-RestMethod -Method Get -Uri $url -TimeoutSec 2
        return $response.status -eq 'ok' -and $response.mode -eq 'observer-only' -and $response.protocol_version -eq $observerProtocolVersion -and $response.service_version -eq $observerServiceVersion -and $null -ne $observerSourceFingerprint -and $response.source_fingerprint -eq $observerSourceFingerprint
    }
    catch {
        return $false
    }
}

function Start-HiddenProcess(
    [string]$filePath,
    [string[]]$arguments,
    [string]$stdoutPath,
    [string]$stderrPath
) {
    Start-Process -FilePath $filePath `
        -ArgumentList $arguments `
        -WorkingDirectory $appRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath | Out-Null
}

function Wait-Until([scriptblock]$condition, [int]$attempts, [string]$failureMessage) {
    foreach ($attempt in 1..$attempts) {
        if (& $condition) { return }
        Start-Sleep -Milliseconds 500
    }
    throw $failureMessage
}

function Show-LaunchError([string]$message) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        $message,
        'AMPgent 启动失败',
        [System.Windows.MessageBoxButton]::OK,
        [System.Windows.MessageBoxImage]::Error
    ) | Out-Null
}

try {
    $npmCommand = (Get-Command npm.cmd -ErrorAction Stop).Source
    $powerShellPath = (Get-Command powershell.exe -ErrorAction Stop).Source
    $logDirectory = Join-Path $appRoot 'output\launcher'
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

    # Supervise the UI and observer independently. HTML may still respond after
    # a previous observer process has gone away, so HTML alone is not readiness.
    if (-not (Test-AmpgentEndpoint $apiUrl)) {
        $apiStdoutPath = Join-Path $logDirectory 'data-service.out.log'
        $apiStderrPath = Join-Path $logDirectory 'data-service.err.log'
        $devScript = Join-Path $PSScriptRoot 'dev.ps1'
        Start-HiddenProcess $powerShellPath @(
            '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-File', $devScript, '-ApiPort', "$ApiPort", '-ApiOnly'
        ) $apiStdoutPath $apiStderrPath
        Wait-Until { Test-AmpgentEndpoint $apiUrl } 90 "数据服务未能在 45 秒内就绪。请查看 $apiStderrPath"
    }

    if (-not (Test-AmpgentUi)) {
        $uiStdoutPath = Join-Path $logDirectory 'interface.out.log'
        $uiStderrPath = Join-Path $logDirectory 'interface.err.log'
        $previousApiTarget = $env:AMPGENT_API_TARGET
        $env:AMPGENT_API_TARGET = "http://127.0.0.1:$ApiPort"
        try {
            Start-HiddenProcess $npmCommand @('run', 'dev:ui', '--', '--port', "$UiPort") $uiStdoutPath $uiStderrPath
        }
        finally {
            $env:AMPGENT_API_TARGET = $previousApiTarget
        }
        Wait-Until { Test-AmpgentUi } 90 "界面未能在 45 秒内就绪。请查看 $uiStderrPath"
    }

    Wait-Until { Test-AmpgentEndpoint $proxiedApiUrl } 30 '界面已启动，但数据服务代理尚未就绪。'
    Start-Process $appUrl | Out-Null

    # Opening the interface must not wait for remote PostgreSQL aggregation.
    # The UI can render its last verified snapshot immediately, while this
    # best-effort helper coalesces with the first browser refresh in the API.
    $warmupScript = Join-Path $PSScriptRoot 'warm-observer-cache.ps1'
    $warmupStdoutPath = Join-Path $logDirectory 'cache-warmup.out.log'
    $warmupStderrPath = Join-Path $logDirectory 'cache-warmup.err.log'
    Start-HiddenProcess $powerShellPath @(
        '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', $warmupScript, '-BaseUrl', "http://127.0.0.1:$UiPort"
    ) $warmupStdoutPath $warmupStderrPath
}
catch {
    Show-LaunchError $_.Exception.Message
    exit 1
}
