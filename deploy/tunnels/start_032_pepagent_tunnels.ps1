[CmdletBinding()]
param(
    [int]$ConnectTimeout = 20,
    [int]$RetryDelaySeconds = 10
)

$ErrorActionPreference = 'Stop'
$toolRoot = Join-Path $env:LOCALAPPDATA 'Programs\remote-gpu'
$jumpCredential = Join-Path $toolRoot 'credentials\eh050-jump.dpapi'
$targetCredential = Join-Path $toolRoot 'credentials\luanhaoyang-target.dpapi'
$askPass = Join-Path $toolRoot 'ssh-askpass.cmd'

function Convert-DpapiFileToPlainText {
    param([string]$Path)
    $secure = (Get-Content -LiteralPath $Path -Raw).Trim() | ConvertTo-SecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

try {
    $env:REMOTE_GPU_JUMP_PASSWORD = Convert-DpapiFileToPlainText $jumpCredential
    $env:REMOTE_GPU_TARGET_PASSWORD = Convert-DpapiFileToPlainText $targetCredential
    $env:REMOTE_GPU_JUMP_MATCH = 'eh050@58.34.98.79'
    $env:REMOTE_GPU_TARGET_MATCH = 'luanhaoyang@192.168.99.32'
    $env:SSH_ASKPASS = $askPass
    $env:SSH_ASKPASS_REQUIRE = 'force'
    $env:DISPLAY = 'remote-gpu'
    $arguments = @(
        '-N', '-T',
        '-o', 'BatchMode=no',
        '-o', "ConnectTimeout=$ConnectTimeout",
        '-o', 'StrictHostKeyChecking=accept-new',
        '-o', 'ExitOnForwardFailure=yes',
        '-o', 'ServerAliveInterval=15',
        '-o', 'ServerAliveCountMax=2',
        '-o', 'TCPKeepAlive=yes',
        '-J', 'eh050@58.34.98.79:49200',
        '-R', '127.0.0.1:17233:127.0.0.1:7233',
        '-R', '127.0.0.1:19000:127.0.0.1:9000',
        '-R', '127.0.0.1:55432:127.0.0.1:55432',
        'luanhaoyang@192.168.99.32'
    )
    while ($true) {
        & ssh @arguments
        $exitCode = $LASTEXITCODE
        Write-Warning ".32 AMPgent service tunnel exited with code $exitCode; reconnecting"
        Start-Sleep -Seconds $RetryDelaySeconds
    }
} finally {
    foreach ($name in @(
        'REMOTE_GPU_JUMP_PASSWORD', 'REMOTE_GPU_TARGET_PASSWORD',
        'REMOTE_GPU_JUMP_MATCH', 'REMOTE_GPU_TARGET_MATCH',
        'SSH_ASKPASS', 'SSH_ASKPASS_REQUIRE', 'DISPLAY'
    )) {
        Remove-Item "Env:$name" -ErrorAction SilentlyContinue
    }
}
