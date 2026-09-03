param(
    [string]$RemoteRoot = '/sdd_data/pepagent/ampgent/md/pool-a-full-md-v1/results',
    [string]$LocalRoot = 'reports/pool_a_md_50ns_expansion_20260903/compact-evidence',
    [string]$Receipt = 'reports/pool_a_md_50ns_expansion_20260903/synth-successor-11/compact-sync.json',
    [string]$SynthTarget = 'synth@127.0.0.1',
    [int]$SynthPort = 32224
)

$ErrorActionPreference = 'Stop'
if (-not $RemoteRoot.StartsWith('/sdd_data/pepagent/')) {
    throw 'RemoteRoot must remain under /sdd_data/pepagent/'
}
$toolRoot = Join-Path $env:LOCALAPPDATA 'Programs\remote-gpu'
$credential = Join-Path $toolRoot 'credentials\synth-target.dpapi'
$secure = (Get-Content -LiteralPath $credential -Raw).Trim() | ConvertTo-SecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $env:REMOTE_GPU_TARGET_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(
        $pointer
    )
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
}
$env:REMOTE_GPU_TARGET_MATCH = $SynthTarget
$env:SSH_ASKPASS = Join-Path $toolRoot 'ssh-askpass.cmd'
$env:SSH_ASKPASS_REQUIRE = 'force'
$env:DISPLAY = 'remote-gpu'
try {
    $env:PYTHONPATH = '.'
    & uv run python analysis/sync_pool_a_md_compact_evidence.py `
        --ssh-target $SynthTarget `
        --ssh-port $SynthPort `
        --remote-root $RemoteRoot `
        --local-root $LocalRoot `
        --receipt $Receipt `
        --batch-mode no
    if ($LASTEXITCODE -ne 0) {
        throw 'synth compact MD evidence sync failed'
    }
} finally {
    Remove-Item Env:REMOTE_GPU_TARGET_PASSWORD, Env:REMOTE_GPU_TARGET_MATCH, `
        Env:SSH_ASKPASS, Env:SSH_ASKPASS_REQUIRE, Env:DISPLAY, Env:PYTHONPATH `
        -ErrorAction SilentlyContinue
}
