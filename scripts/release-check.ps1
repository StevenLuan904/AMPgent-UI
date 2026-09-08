[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$RequireCleanWorktree
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $repoRoot

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory)] [string]$Label,
        [Parameter(Mandatory)] [string]$Executable,
        [Parameter()] [string[]]$Arguments = @()
    )

    Write-Host "`n[$Label]" -ForegroundColor Cyan
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE."
    }
}

foreach ($command in @('node', 'npm', 'git')) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Required command is not available: $command"
    }
}

if ($Install) {
    Invoke-CheckedCommand -Label 'Reproducible dependency install' -Executable 'npm.cmd' -Arguments @('ci')
} elseif (-not (Test-Path -LiteralPath (Join-Path $repoRoot 'node_modules'))) {
    throw 'node_modules is missing. Re-run with -Install.'
}

Invoke-CheckedCommand -Label 'Analysis unit and integration tests' -Executable 'npm.cmd' -Arguments @('run', 'test:analysis')
Invoke-CheckedCommand -Label 'Production build' -Executable 'npm.cmd' -Arguments @('run', 'build')
Invoke-CheckedCommand -Label 'Dependency security audit' -Executable 'npm.cmd' -Arguments @('audit', '--audit-level=high')
Invoke-CheckedCommand -Label 'Whitespace/error diff check' -Executable 'git.exe' -Arguments @('diff', '--check')

$snapshotPath = Join-Path $repoRoot 'public\data\launch-analysis.snapshot.json'
$distIndexPath = Join-Path $repoRoot 'dist\index.html'
if (-not (Test-Path -LiteralPath $snapshotPath)) { throw 'Release snapshot is missing.' }
if (-not (Test-Path -LiteralPath $distIndexPath)) { throw 'Production dist/index.html is missing.' }

$snapshot = Get-Content -LiteralPath $snapshotPath -Raw -Encoding utf8 | ConvertFrom-Json -Depth 100
$expectedRunId = '57afecc7-22e9-4efb-9051-acb11234013d'
if ($snapshot.run.id -ne $expectedRunId) { throw "Unexpected release run: $($snapshot.run.id)" }
if ($snapshot.run.status -ne 'cancelled') { throw 'Release snapshot status changed; re-audit all launch claims.' }
if ($snapshot.occurrences.Count -ne 900) { throw 'Expected exactly 900 proposal occurrences.' }
if ($snapshot.candidates.Count -ne 773) { throw 'Expected exactly 773 unique candidates.' }
$metricCount = @($snapshot.metricMethods.PSObject.Properties).Count
if ($metricCount -ne 11) { throw "Expected exactly 11 metric definitions, found $metricCount." }
if ($snapshot.coverage.observed -ne 8503 -or $snapshot.coverage.expected -ne 8503) {
    throw 'Expected complete 8503/8503 evaluation coverage.'
}

if ($RequireCleanWorktree) {
    $dirty = & git.exe status --porcelain
    if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect worktree status.' }
    if ($dirty) { throw 'Worktree is not clean.' }
}

$commit = (& git.exe rev-parse --short HEAD).Trim()
Write-Host "`nRelease checks passed." -ForegroundColor Green
[pscustomobject]@{
    Commit = $commit
    RunId = $snapshot.run.id
    RunStatus = $snapshot.run.status
    RawOccurrences = $snapshot.occurrences.Count
    UniqueCandidates = $snapshot.candidates.Count
    EvaluationCoverage = "$($snapshot.coverage.observed)/$($snapshot.coverage.expected)"
    Metrics = $metricCount
    SnapshotSha256 = $snapshot.snapshotSha256
} | Format-List
