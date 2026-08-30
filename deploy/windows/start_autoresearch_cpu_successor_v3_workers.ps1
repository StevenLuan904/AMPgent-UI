[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Archive,
    [Parameter(Mandatory = $true)][string]$ArchiveSha256,
    [Parameter(Mandatory = $true)][string]$SourceRevision,
    [Parameter(Mandatory = $true)][string]$Python,
    [ValidateSet(3, 4)][int]$QueueGeneration = 3,
    [ValidateSet('all', 'control', 'persistence', 'metrics')][string]$RoleFilter = 'all',
    [string]$ReleaseRoot = 'var/platform/releases-v39-quality',
    [string]$StateRoot = ''
)

$ErrorActionPreference = 'Stop'
$expected40 = '^[0-9a-f]{40}$'
$expected64 = '^[0-9a-f]{64}$'
if ($SourceRevision -notmatch $expected40 -or $ArchiveSha256 -notmatch $expected64) {
    throw 'CPU successor worker source or archive identity is invalid'
}
if (-not $StateRoot) {
    $StateRoot = "var/run/autoresearch-cpu-successor-v$QueueGeneration"
}

$workspace = (Resolve-Path -LiteralPath '.').Path
$archivePath = (Resolve-Path -LiteralPath $Archive).Path
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
$pythonEnvironment = Split-Path (Split-Path $pythonPath -Parent) -Parent
$sitePackages = Join-Path $pythonEnvironment 'Lib\site-packages'
$releaseBase = [IO.Path]::GetFullPath((Join-Path $workspace $ReleaseRoot))
$stateBase = [IO.Path]::GetFullPath((Join-Path $workspace $StateRoot))
$workRoot = [IO.Path]::GetFullPath((Join-Path $workspace 'var/work-v38'))
foreach ($path in @($releaseBase, $stateBase, $workRoot)) {
    if (-not $path.StartsWith($workspace, [StringComparison]::OrdinalIgnoreCase)) {
        throw "v3 worker path escapes workspace: $path"
    }
    $null = New-Item -ItemType Directory -Path $path -Force
}
if (-not (Test-Path -LiteralPath $sitePackages -PathType Container)) {
    throw 'v3 worker Python site-packages is missing'
}
if ((Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ArchiveSha256) {
    throw 'v3 worker archive SHA-256 drifted'
}

$releasePath = Join-Path $releaseBase $ArchiveSha256
if (-not (Test-Path -LiteralPath $releasePath -PathType Container)) {
    $null = New-Item -ItemType Directory -Path $releasePath
    & tar -xf $archivePath -C $releasePath
    if ($LASTEXITCODE -ne 0) { throw 'v3 worker release extraction failed' }
}
$marker = [IO.File]::ReadAllText(
    (Join-Path $releasePath '.pepagent-source-revision'),
    [Text.Encoding]::UTF8
).Trim()
if ($marker -ne $SourceRevision) { throw 'v3 worker release marker drifted' }

$roles = if ($QueueGeneration -eq 3) {
    @(
        @{
            Name = 'autoresearch-cpu-successor-v3-control'
            Queue = 'pepagent-autoresearch-cpu-successor-control-v3'
            Maximum = '16'
        },
        @{
            Name = 'autoresearch-cpu-successor-v3-persistence'
            Queue = 'pepagent-autoresearch-cpu-successor-persistence-v3'
            Maximum = '5'
        },
        @{
            Name = 'autoresearch-cpu-successor-v3-metrics'
            Queue = 'pepagent-autoresearch-cpu-successor-metrics-v3'
            Maximum = '5'
        }
    )
} else {
    @(
        @{
            Name = 'autoresearch-cpu-successor-v4-control'
            Queue = 'pepagent-autoresearch-cpu-successor-control-v4'
            Maximum = '16'
        },
        @{
            Name = 'autoresearch-cpu-successor-v4-persistence'
            Queue = 'pepagent-autoresearch-cpu-successor-persistence-v4'
            Maximum = '5'
        },
        @{
            Name = 'autoresearch-cpu-successor-v4-metrics'
            Queue = 'pepagent-autoresearch-cpu-successor-metrics-v4'
            Maximum = '5'
        }
    )
}
if ($RoleFilter -ne 'all') {
    $roles = @($roles | Where-Object { $_.Name.EndsWith("-$RoleFilter") })
    if ($roles.Count -ne 1) {
        throw "CPU successor role filter did not resolve exactly one role: $RoleFilter"
    }
}
$pythonSha256 = (Get-FileHash -LiteralPath $pythonPath -Algorithm SHA256).Hash.ToLowerInvariant()
$receipts = @()
foreach ($role in $roles) {
    $receiptPath = Join-Path $stateBase "$($role.Name).json"
    if (Test-Path -LiteralPath $receiptPath -PathType Leaf) {
        $previous = Get-Content -LiteralPath $receiptPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $live = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$previous.pid)" -ErrorAction SilentlyContinue
        if ($null -ne $live) {
            if ($previous.ampgent_owned -ne $true -or $previous.foreign -eq $true -or
                $previous.role -ne $role.Name -or $previous.task_queue -ne $role.Queue -or
                $previous.source_revision -ne $SourceRevision -or
                $previous.release_sha256 -ne $ArchiveSha256) {
                throw "live v3 worker identity differs for $($role.Name); replacement is forbidden"
            }
            $receipts += $previous
            continue
        }
    }

    $stdout = Join-Path $stateBase "$($role.Name).stdout.log"
    $stderr = Join-Path $stateBase "$($role.Name).stderr.log"
    $saved = @{
        Role = $env:PEPAGENT_WORKER_ROLE
        Source = $env:PEPAGENT_WORKER_SOURCE_REVISION
        Maximum = $env:PEPAGENT_WORKER_MAX_CONCURRENT_ACTIVITIES
        PythonPath = $env:PYTHONPATH
        WorkRoot = $env:PEPAGENT_WORK_ROOT
        Release = $env:PEPAGENT_PLATFORM_RELEASE_SHA256
        Environment = $env:PEPAGENT_WORKER_ENVIRONMENT_SHA256
    }
    try {
        $env:PEPAGENT_WORKER_ROLE = $role.Name
        $env:PEPAGENT_WORKER_SOURCE_REVISION = $SourceRevision
        $env:PEPAGENT_WORKER_MAX_CONCURRENT_ACTIVITIES = $role.Maximum
        $env:PYTHONPATH = "$sitePackages;$(Join-Path $releasePath 'src')"
        $env:PEPAGENT_WORK_ROOT = $workRoot
        $env:PEPAGENT_PLATFORM_RELEASE_SHA256 = $ArchiveSha256
        $releaseQueue = (& $pythonPath -S -c (
            'import os; from pepagent.workers.v38_temporal_worker import ' +
            'V38_ROLE_CONFIG; print(V38_ROLE_CONFIG[os.environ[' +
            '"PEPAGENT_WORKER_ROLE"]][0])'
        )).Trim()
        if ($LASTEXITCODE -ne 0 -or $releaseQueue -ne $role.Queue) {
            throw "v3 release queue differs for $($role.Name)"
        }
        $environmentSha256 = (& $pythonPath -S -c (
            'from pepagent.provenance.environment import fingerprint_runtime; ' +
            'print(fingerprint_runtime()[0])'
        )).Trim()
        if ($LASTEXITCODE -ne 0 -or $environmentSha256 -notmatch $expected64) {
            throw 'v3 worker environment fingerprint is invalid'
        }
        $env:PEPAGENT_WORKER_ENVIRONMENT_SHA256 = $environmentSha256
        $process = Start-Process -FilePath $pythonPath -ArgumentList @(
            '-S', '-m', 'pepagent.workers.v38_temporal_worker'
        ) -WorkingDirectory $releasePath -WindowStyle Hidden -PassThru `
          -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    }
    finally {
        $env:PEPAGENT_WORKER_ROLE = $saved.Role
        $env:PEPAGENT_WORKER_SOURCE_REVISION = $saved.Source
        $env:PEPAGENT_WORKER_MAX_CONCURRENT_ACTIVITIES = $saved.Maximum
        $env:PYTHONPATH = $saved.PythonPath
        $env:PEPAGENT_WORK_ROOT = $saved.WorkRoot
        $env:PEPAGENT_PLATFORM_RELEASE_SHA256 = $saved.Release
        $env:PEPAGENT_WORKER_ENVIRONMENT_SHA256 = $saved.Environment
    }
    Start-Sleep -Seconds 2
    if ($process.HasExited) {
        $detail = if (Test-Path -LiteralPath $stderr) {
            Get-Content -LiteralPath $stderr -Raw -Encoding UTF8
        } else { '' }
        throw "v3 worker exited during launch: $detail"
    }
    $children = @(Get-CimInstance Win32_Process | Where-Object {
        $_.ParentProcessId -eq $process.Id -and
        $_.Name -eq 'python.exe' -and
        $_.CommandLine -like '*pepagent.workers.v38_temporal_worker*'
    })
    if ($children.Count -ne 1) {
        throw "v3 launcher did not produce exactly one poller for $($role.Name)"
    }
    $receipt = [ordered]@{
        schema_version = "ampgent.autoresearch-cpu-successor-v$QueueGeneration-worker.1"
        role = $role.Name
        task_queue = $role.Queue
        pid = [int]$children[0].ProcessId
        supervisor_pid = [int]$process.Id
        host = [Environment]::MachineName
        source_revision = $SourceRevision
        release_sha256 = $ArchiveSha256
        archive_path = $archivePath
        release_path = $releasePath
        work_root = $workRoot
        python_path = $pythonPath
        python_sha256 = $pythonSha256
        environment_sha256 = $environmentSha256
        task_queue_verified_from_release = $true
        ampgent_owned = $true
        foreign = $false
        started_at = [DateTimeOffset]::UtcNow.ToString('o')
        existing_worker_restarted = $false
        gpu_task_started = $false
    }
    $receipt | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $receiptPath -Encoding UTF8
    $receipts += [pscustomobject]$receipt
}
$receipts | ConvertTo-Json -Depth 5
