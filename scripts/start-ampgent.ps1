param(
    [int]$UiPort = 5173
)

$ErrorActionPreference = 'Stop'
$appRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$appUrl = "http://127.0.0.1:$UiPort/"

function Test-AmpgentUi {
    try {
        $response = Invoke-WebRequest -Method Get -Uri $appUrl -TimeoutSec 2 -UseBasicParsing
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
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
    if (-not (Test-AmpgentUi)) {
        $npmCommand = (Get-Command npm.cmd -ErrorAction Stop).Source
        $logDirectory = Join-Path $appRoot 'output\launcher'
        New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
        $stdoutPath = Join-Path $logDirectory 'application.out.log'
        $stderrPath = Join-Path $logDirectory 'application.err.log'

        Start-Process -FilePath $npmCommand `
            -ArgumentList @('run', 'dev') `
            -WorkingDirectory $appRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath | Out-Null

        $ready = $false
        foreach ($attempt in 1..90) {
            if (Test-AmpgentUi) {
                $ready = $true
                break
            }
            Start-Sleep -Milliseconds 500
        }

        if (-not $ready) {
            throw "应用未能在 45 秒内就绪。请查看 $stderrPath"
        }
    }

    Start-Process $appUrl | Out-Null
}
catch {
    Show-LaunchError $_.Exception.Message
    exit 1
}
