param(
    [Parameter(Mandatory = $true)]
    [string]$SynthRoot,
    [Parameter(Mandatory = $true)]
    [string]$SynthExporter,
    [Parameter(Mandatory = $true)]
    [string]$Host19Ingester,
    [Parameter(Mandatory = $true)]
    [string]$Host19State,
    [string]$SynthTarget = 'synth@127.0.0.1',
    [int]$SynthPort = 32224,
    [string]$Host19Target = 'TargetServerDirect',
    [int]$Host19Port = 32222
)

$ErrorActionPreference = 'Stop'
if (-not $SynthRoot.StartsWith('/sdd_data/pepagent/')) {
    throw 'SynthRoot must remain under /sdd_data/pepagent/'
}
if (-not $SynthExporter.StartsWith('/sdd_data/pepagent/')) {
    throw 'SynthExporter must remain under /sdd_data/pepagent/'
}
if (-not $Host19Ingester.StartsWith('/data1/huangyueshan/pepagent/')) {
    throw 'Host19Ingester must remain under the AMPgent /data1 root'
}
if (-not $Host19State.StartsWith('/data1/huangyueshan/pepagent/')) {
    throw 'Host19State must remain under the AMPgent /data1 root'
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
    $bundle = @(
        & ssh -p $SynthPort -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 `
            $SynthTarget "python3 '$SynthExporter' --root '$SynthRoot'"
    )
    if ($LASTEXITCODE -ne 0) {
        throw 'synth receipt export failed'
    }
} finally {
    Remove-Item Env:REMOTE_GPU_TARGET_PASSWORD, Env:REMOTE_GPU_TARGET_MATCH, `
        Env:SSH_ASKPASS, Env:SSH_ASKPASS_REQUIRE, Env:DISPLAY `
        -ErrorAction SilentlyContinue
}

$remote = "cd /data1/huangyueshan/pepagent && " +
    'PEPAGENT_DATABASE_URL=postgresql+asyncpg://pepagent@127.0.0.1:55433/pepagent ' +
    'PYTHONPATH=/data1/huangyueshan/pepagent ' +
    "/data1/huangyueshan/pepagent/envs/gpu-worker-py311-v1/bin/python " +
    "'$Host19Ingester' --bundle-jsonl-stdin --state '$Host19State'"
if ($bundle.Count -eq 0) {
    '' | & ssh -p $Host19Port -o BatchMode=yes -o ConnectTimeout=10 $Host19Target $remote
} else {
    $bundle | & ssh -p $Host19Port -o BatchMode=yes -o ConnectTimeout=10 `
        $Host19Target $remote
}
if ($LASTEXITCODE -ne 0) {
    throw 'receipt relay ingest failed'
}
& ssh -p $Host19Port -o BatchMode=yes -o ConnectTimeout=10 `
    $Host19Target "cat '$Host19State'"
if ($LASTEXITCODE -ne 0) {
    throw 'receipt relay state read failed'
}
