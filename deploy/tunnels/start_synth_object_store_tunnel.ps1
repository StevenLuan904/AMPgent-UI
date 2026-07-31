[CmdletBinding()]
param(
    [int]$LocalPort = 9000,
    [int]$RemotePort = 19000,
    [int]$ConnectTimeout = 15
)

$ErrorActionPreference = 'Stop'
$toolRoot = Join-Path $env:LOCALAPPDATA 'Programs\remote-gpu'
$jumpCredential = Join-Path $toolRoot 'credentials\eh002-jump.dpapi'
$targetCredential = Join-Path $toolRoot 'credentials\synth-target.dpapi'
$askPass = Join-Path $toolRoot 'ssh-askpass.cmd'

function Convert-DpapiFileToPlainText {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { throw "Missing local credential: $Path" }
    try {
        $secure = (Get-Content -LiteralPath $Path -Raw).Trim() | ConvertTo-SecureString
    } catch {
        throw "DPAPI credential cannot be decrypted by the current Windows user: $Path"
    }
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
        '-o', 'ServerAliveInterval=30',
        '-o', 'ServerAliveCountMax=3',
        '-J', 'eh002@58.34.98.79:49200',
        '-R', "127.0.0.1`:$RemotePort`:127.0.0.1`:$LocalPort",
        'synth@192.168.99.2'
    )
    & ssh @arguments
    if ($LASTEXITCODE -ne 0) { throw "Object-store tunnel exited with code $LASTEXITCODE" }
} finally {
    foreach ($name in @(
        'REMOTE_GPU_JUMP_PASSWORD', 'REMOTE_GPU_TARGET_PASSWORD',
        'REMOTE_GPU_JUMP_MATCH', 'REMOTE_GPU_TARGET_MATCH',
        'SSH_ASKPASS', 'SSH_ASKPASS_REQUIRE', 'DISPLAY'
    )) {
        Remove-Item "Env:$name" -ErrorAction SilentlyContinue
    }
}
