[CmdletBinding()]
param(
    [int]$LocalPort = 32224,
    [int]$ConnectTimeout = 20,
    [int]$RetryDelaySeconds = 10
)

$ErrorActionPreference = 'Stop'
$toolRoot = Join-Path $env:LOCALAPPDATA 'Programs\remote-gpu'
$jumpCredential = Join-Path $toolRoot 'credentials\eh002-jump.dpapi'
$targetCredential = Join-Path $toolRoot 'credentials\synth-target.dpapi'
$askPass = Join-Path $toolRoot 'ssh-askpass.cmd'

function Convert-DpapiFileToPlainText {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { throw "Missing local credential: $Path" }
    $secure = (Get-Content -LiteralPath $Path -Raw).Trim() | ConvertTo-SecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

try {
    $env:REMOTE_GPU_JUMP_PASSWORD = Convert-DpapiFileToPlainText $jumpCredential
    $env:REMOTE_GPU_TARGET_PASSWORD = Convert-DpapiFileToPlainText $targetCredential
    $env:REMOTE_GPU_JUMP_MATCH = 'eh002@58.34.98.79'
    $env:REMOTE_GPU_TARGET_MATCH = 'synth@192.168.99.2'
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
        '-J', 'eh002@58.34.98.79:49200',
        '-L', "127.0.0.1:${LocalPort}:127.0.0.1:22",
        'synth@192.168.99.2'
    )
    while ($true) {
        & ssh @arguments
        $exitCode = $LASTEXITCODE
        Write-Warning "AMPgent synth probe tunnel exited with code $exitCode; reconnecting in $RetryDelaySeconds seconds"
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
