param(
    [Parameter(Mandatory = $true)] [string]$SourceCommit,
    [string]$Snapshot = 'reports/pool_a_md_50ns_expansion_20260903/pool_a_combined_486_20260903.json',
    [string]$EvidenceRoot = 'reports/pool_a_md_50ns_expansion_20260903/compact-evidence',
    [string]$OutputDir = 'reports/pool_a_md_50ns_expansion_20260903/live-summary-all-486'
)

$ErrorActionPreference = 'Stop'
if ($SourceCommit -notmatch '^[0-9a-f]{40}$') {
    throw 'SourceCommit must be a full Git SHA-1'
}

$env:PYTHONPATH = '.'
try {
    & uv run python analysis/sync_pool_a_md_compact_evidence.py `
        --ssh-target TargetServerDirect `
        --ssh-port 32222 `
        --remote-root /data1/huangyueshan/pepagent/md/pool-a-full-md-v1/results `
        --local-root $EvidenceRoot `
        --receipt reports/pool_a_md_50ns_expansion_20260903/compact_sync_live.json
    if ($LASTEXITCODE -ne 0) {
        throw '.19 compact MD evidence sync failed'
    }

    & ./deploy/windows/sync_synth_pool_a_md_compact_evidence.ps1 `
        -LocalRoot $EvidenceRoot
    if ($LASTEXITCODE -ne 0) {
        throw 'synth compact MD evidence sync failed'
    }

    & ./deploy/windows/relay_synth_pool_a_md_evidence.ps1 `
        -SourceCommit $SourceCommit
    if ($LASTEXITCODE -ne 0) {
        throw 'synth PostgreSQL evidence relay failed'
    }

    & uv run python analysis/refresh_pool_a_md_reports.py `
        --snapshot $Snapshot `
        --evidence-root $EvidenceRoot `
        --output-dir $OutputDir
    if ($LASTEXITCODE -ne 0) {
        throw 'Pool-A MD report refresh failed'
    }
} finally {
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
}

Get-Content -Raw -LiteralPath (Join-Path $OutputDir 'refresh_receipt.json')
