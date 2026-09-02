param(
    [string]$SynthRoot = '/sdd_data/pepagent/ampgent/md/pool-a-full-md-v1',
    [string]$Host19Root = '/data1/huangyueshan/pepagent/md/pool-a-full-md-v1',
    [Parameter(Mandatory = $true)] [string]$SourceCommit,
    [string]$SynthTarget = 'synth@127.0.0.1',
    [int]$SynthPort = 32224,
    [string]$Host19Target = 'TargetServerDirect',
    [int]$Host19Port = 32222
)

$ErrorActionPreference = 'Stop'
if (-not $SynthRoot.StartsWith('/sdd_data/pepagent/')) {
    throw 'SynthRoot must remain under /sdd_data/pepagent/'
}
if (-not $Host19Root.StartsWith('/data1/huangyueshan/pepagent/')) {
    throw 'Host19Root must remain under the AMPgent /data1 root'
}
if ($SourceCommit -notmatch '^[0-9a-f]{40}$') {
    throw 'SourceCommit must be a full Git SHA-1'
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
    $exporter = "$SynthRoot/inputs/ingest_pool_a_md_evidence.py"
    $bundle = @(
        & ssh -p $SynthPort -o BatchMode=no -o ConnectTimeout=10 $SynthTarget `
            "$SynthRoot/mmgbsa-env-py311/bin/python '$exporter' --root '$SynthRoot/results' --uri-root '$SynthRoot/results' --source-commit '$SourceCommit' --export-jsonl-stdout"
    )
    if ($LASTEXITCODE -ne 0) {
        throw 'synth MD evidence export failed'
    }
} finally {
    Remove-Item Env:REMOTE_GPU_TARGET_PASSWORD, Env:REMOTE_GPU_TARGET_MATCH, `
        Env:SSH_ASKPASS, Env:SSH_ASKPASS_REQUIRE, Env:DISPLAY -ErrorAction SilentlyContinue
}

$ingester = "$Host19Root/inputs/ingest_pool_a_md_evidence.py"
$state = "$Host19Root/synth-successor-11-postgresql-ingester-state.json"
$remote = "cd /data1/huangyueshan/pepagent && " +
    'PEPAGENT_DATABASE_URL=postgresql+asyncpg://pepagent@127.0.0.1:55433/pepagent ' +
    'PYTHONPATH=/data1/huangyueshan/pepagent ' +
    "/data1/huangyueshan/pepagent/envs/gpu-worker-py311-v1/bin/python '$ingester' " +
    "--source-commit '$SourceCommit' --bundle-jsonl-stdin --state '$state'"
if ($bundle.Count -eq 0) {
    '' | & ssh -p $Host19Port -o BatchMode=yes -o ConnectTimeout=10 $Host19Target $remote
} else {
    $bundle | & ssh -p $Host19Port -o BatchMode=yes -o ConnectTimeout=10 $Host19Target $remote
}
if ($LASTEXITCODE -ne 0) {
    throw 'PostgreSQL MD evidence relay failed'
}
& ssh -p $Host19Port -o BatchMode=yes -o ConnectTimeout=10 $Host19Target "cat '$state'"
if ($LASTEXITCODE -ne 0) {
    throw 'PostgreSQL MD evidence relay state read failed'
}
