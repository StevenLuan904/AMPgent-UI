[CmdletBinding()]
param(
    [string]$SourceRevision = 'HEAD',
    [string]$ReleaseRoot = 'var/platform/releases-v39-quality',
    [string]$ArchiveRoot = 'var/platform/release-archives-v39-quality'
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$resolvedRevision = (& git -C $repoRoot rev-parse "$SourceRevision^{commit}").Trim()
if ($LASTEXITCODE -ne 0 -or $resolvedRevision -notmatch '^[0-9a-f]{40}$') {
    throw "Cannot resolve source revision: $SourceRevision"
}

$releaseRootPath = [IO.Path]::GetFullPath((Join-Path $repoRoot $ReleaseRoot))
$archiveRootPath = [IO.Path]::GetFullPath((Join-Path $repoRoot $ArchiveRoot))
$tempRootPath = [IO.Path]::GetFullPath((Join-Path $repoRoot 'var\tmp\v39-quality-release'))
foreach ($path in @($releaseRootPath, $archiveRootPath, $tempRootPath)) {
    if (-not $path.StartsWith($repoRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Release path escapes repository: $path"
    }
    $null = New-Item -ItemType Directory -Path $path -Force
}

$stagePath = Join-Path $tempRootPath ([guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $stagePath

try {
    $candidateArchive = Join-Path $stagePath 'platform-release.tar'
    $markerArgument = "--add-virtual-file=.pepagent-source-revision:$resolvedRevision"
    & git -C $repoRoot archive --format=tar --output=$candidateArchive $markerArgument $resolvedRevision
    if ($LASTEXITCODE -ne 0) { throw 'git release archive failed' }
    $releaseSha = (Get-FileHash -LiteralPath $candidateArchive -Algorithm SHA256).Hash.ToLowerInvariant()
    $archivePath = Join-Path $archiveRootPath "platform-$releaseSha.tar"
    $releasePath = Join-Path $releaseRootPath $releaseSha

    if (-not (Test-Path -LiteralPath $archivePath)) {
        Copy-Item -LiteralPath $candidateArchive -Destination $archivePath
    }
    if (-not (Test-Path -LiteralPath $releasePath)) {
        $null = New-Item -ItemType Directory -Path $releasePath
        & tar -xf $archivePath -C $releasePath
        if ($LASTEXITCODE -ne 0) { throw 'release extraction failed' }
    }

    $marker = [IO.File]::ReadAllText((Join-Path $releasePath '.pepagent-source-revision'), [Text.Encoding]::UTF8)
    if ($marker -ne $resolvedRevision) { throw 'release source marker mismatch' }
    if ((Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $releaseSha) {
        throw 'persisted release archive digest mismatch'
    }

    [pscustomobject]@{
        schema_version = 'ampgent.v39-quality-release.1'
        source_revision = $resolvedRevision
        release_sha256 = $releaseSha
        archive_path = $archivePath
        archive_size_bytes = (Get-Item -LiteralPath $archivePath).Length
        release_path = $releasePath
        source_marker_verified = $true
    } | ConvertTo-Json -Depth 4
}
finally {
    $resolvedStage = [IO.Path]::GetFullPath($stagePath)
    if (-not $resolvedStage.StartsWith($tempRootPath, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove unexpected stage path: $resolvedStage"
    }
    if (Test-Path -LiteralPath $resolvedStage) {
        Remove-Item -LiteralPath $resolvedStage -Recurse -Force
    }
}
