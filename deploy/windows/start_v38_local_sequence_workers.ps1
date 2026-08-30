param(
    [Parameter(Mandatory = $true)][string]$Archive,
    [Parameter(Mandatory = $true)][string]$ArchiveSha256,
    [Parameter(Mandatory = $true)][string]$SourceRevision,
    [Parameter(Mandatory = $true)][string]$Python,
    [string]$ReleaseRoot = "var/platform/releases-v38",
    [string]$StateRoot = "var/run/v38-workers",
    [ValidateSet("v38", "autoresearch-local", "all")]
    [string]$RoleSet = "v38",
    [ValidateSet(
        "v38-control",
        "v38-generator",
        "v38-metrics",
        "autoresearch-control",
        "autoresearch-persistence",
        "autoresearch-metrics"
    )]
    [string]$OnlyRole,
    [switch]$ReplaceOwned
)

$ErrorActionPreference = "Stop"
$expected40 = "^[0-9a-f]{40}$"
$expected64 = "^[0-9a-f]{64}$"
if ($SourceRevision -notmatch $expected40 -or $ArchiveSha256 -notmatch $expected64) {
    throw "v38 worker source or archive identity is invalid"
}
$archivePath = (Resolve-Path -LiteralPath $Archive).Path
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
$pythonEnvironment = Split-Path (Split-Path $pythonPath -Parent) -Parent
$sitePackages = Join-Path $pythonEnvironment "Lib\site-packages"
if (-not (Test-Path -LiteralPath $sitePackages -PathType Container)) {
    throw "v38 worker Python site-packages is missing"
}
$actualArchiveSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $archivePath).Hash.ToLower()
if ($actualArchiveSha -ne $ArchiveSha256) {
    throw "v38 worker archive SHA drifted"
}
$pythonSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $pythonPath).Hash.ToLower()

$workspace = (Resolve-Path -LiteralPath ".").Path
$releaseBase = [IO.Path]::GetFullPath((Join-Path $workspace $ReleaseRoot))
$stateBase = [IO.Path]::GetFullPath((Join-Path $workspace $StateRoot))
$workRoot = [IO.Path]::GetFullPath((Join-Path $workspace "var/work-v38"))
if (-not $releaseBase.StartsWith($workspace, [StringComparison]::OrdinalIgnoreCase) -or
    -not $stateBase.StartsWith($workspace, [StringComparison]::OrdinalIgnoreCase) -or
    -not $workRoot.StartsWith($workspace, [StringComparison]::OrdinalIgnoreCase)) {
    throw "v38 worker paths escaped the workspace"
}
$releasePath = Join-Path $releaseBase $ArchiveSha256
New-Item -ItemType Directory -Force -Path $releaseBase, $stateBase, $workRoot | Out-Null
if (-not (Test-Path -LiteralPath $releasePath -PathType Container)) {
    New-Item -ItemType Directory -Path $releasePath | Out-Null
    tar -xzf $archivePath -C $releasePath
    if ($LASTEXITCODE -ne 0) { throw "v38 worker archive extraction failed" }
}
$markerPath = Join-Path $releasePath ".pepagent-source-revision"
if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) {
    throw "v38 worker release has no source marker"
}
$marker = (Get-Content -Raw -LiteralPath $markerPath).Trim()
if ($marker -ne $SourceRevision) {
    throw "v38 worker release source marker drifted"
}

$v38Roles = @(
    @{ Name = "v38-control"; TaskQueue = "pepagent-control-v38"; Maximum = "16" },
    @{ Name = "v38-generator"; TaskQueue = "pepagent-generator-v38"; Maximum = "8" },
    @{ Name = "v38-metrics"; TaskQueue = "pepagent-cpu-metrics-v38"; Maximum = "5" }
)
$autoresearchLocalRoles = @(
    @{
        Name = "autoresearch-control"
        TaskQueue = "pepagent-autoresearch-control-v1"
        Maximum = "16"
    },
    @{
        Name = "autoresearch-persistence"
        TaskQueue = "pepagent-autoresearch-persistence-v1"
        Maximum = "5"
    },
    @{
        Name = "autoresearch-metrics"
        TaskQueue = "pepagent-autoresearch-metrics-v1"
        Maximum = "5"
    }
)
$roles = switch ($RoleSet) {
    "v38" { $v38Roles }
    "autoresearch-local" { $autoresearchLocalRoles }
    "all" { @($v38Roles) + @($autoresearchLocalRoles) }
}
if ($OnlyRole) {
    $roles = @($roles | Where-Object { $_.Name -eq $OnlyRole })
    if ($roles.Count -ne 1) {
        throw "requested worker role is outside the selected role set"
    }
}
$receipts = @()
foreach ($role in $roles) {
    $roleName = $role.Name
    $roleQueue = $role.TaskQueue
    $receiptPath = Join-Path $stateBase ("$roleName.json")
    if (Test-Path -LiteralPath $receiptPath -PathType Leaf) {
        $previous = Get-Content -Raw -LiteralPath $receiptPath | ConvertFrom-Json
        $queueMismatch = (
            $roleName -like "autoresearch-*" -and
            $previous.task_queue -ne $roleQueue
        )
        $environmentIdentityMissing = (
            $roleName -like "autoresearch-*" -and
            $previous.environment_sha256 -notmatch $expected64
        )
        if ($previous.ampgent_owned -ne $true -or $previous.foreign -eq $true -or
            $previous.role -ne $roleName -or $queueMismatch -or
            $environmentIdentityMissing) {
            throw "v38 worker receipt is not exact AMPgent ownership for this role"
        }
        $poller = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$previous.pid)" `
            -ErrorAction SilentlyContinue
        if ($null -ne $poller) {
            if ($null -eq $previous.supervisor_pid) {
                throw "live v38 worker receipt has no supervisor identity"
            }
            $supervisor = Get-CimInstance Win32_Process `
                -Filter "ProcessId=$([int]$previous.supervisor_pid)" -ErrorAction SilentlyContinue
            if ($null -eq $supervisor -or
                $poller.ParentProcessId -ne [int]$previous.supervisor_pid -or
                $poller.CommandLine -notlike "*pepagent.workers.v38_temporal_worker*" -or
                $supervisor.CommandLine -notlike "*pepagent.workers.v38_temporal_worker*") {
                throw "live v38 worker process tree does not match its exact receipt"
            }
            $sameIdentity = (
                $previous.source_revision -eq $SourceRevision -and
                $previous.release_sha256 -eq $ArchiveSha256
            )
            if ($sameIdentity) {
                $receipts += $previous
                continue
            }
            if (-not $ReplaceOwned) {
                throw "live exact-owned v38 worker has another immutable identity; use -ReplaceOwned"
            }
            Stop-Process -Id ([int]$previous.pid) -ErrorAction Stop
            Wait-Process -Id ([int]$previous.pid) -Timeout 15 -ErrorAction SilentlyContinue
            $supervisorStillLive = Get-Process -Id ([int]$previous.supervisor_pid) `
                -ErrorAction SilentlyContinue
            if ($null -ne $supervisorStillLive) {
                # The supervisor normally exits as soon as its poller child is
                # stopped.  Treat that exact-PID exit race as successful.
                Stop-Process -Id ([int]$previous.supervisor_pid) `
                    -ErrorAction SilentlyContinue
                Wait-Process -Id ([int]$previous.supervisor_pid) -Timeout 15 `
                    -ErrorAction SilentlyContinue
            }
        }
    }
    $stdout = Join-Path $stateBase ("$roleName.stdout.log")
    $stderr = Join-Path $stateBase ("$roleName.stderr.log")
    $oldRole = $env:PEPAGENT_WORKER_ROLE
    $oldSource = $env:PEPAGENT_WORKER_SOURCE_REVISION
    $oldMaximum = $env:PEPAGENT_WORKER_MAX_CONCURRENT_ACTIVITIES
    $oldPythonPath = $env:PYTHONPATH
    $oldWorkRoot = $env:PEPAGENT_WORK_ROOT
    $oldPlatformRelease = $env:PEPAGENT_PLATFORM_RELEASE_SHA256
    $oldWorkerEnvironment = $env:PEPAGENT_WORKER_ENVIRONMENT_SHA256
    try {
        $env:PEPAGENT_WORKER_ROLE = $roleName
        $env:PEPAGENT_WORKER_SOURCE_REVISION = $SourceRevision
        $env:PEPAGENT_WORKER_MAX_CONCURRENT_ACTIVITIES = $role.Maximum
        # Avoid decoding a UTF-8 editable-install .pth through the Windows ANSI codec.
        # The same immutable environment packages and release source remain explicit.
        $env:PYTHONPATH = "$sitePackages;$(Join-Path $releasePath 'src')"
        $env:PEPAGENT_WORK_ROOT = $workRoot
        $env:PEPAGENT_PLATFORM_RELEASE_SHA256 = $ArchiveSha256
        $releaseQueue = (& $pythonPath -S -c (
            'import os; from pepagent.workers.v38_temporal_worker import ' +
            'V38_ROLE_CONFIG; print(V38_ROLE_CONFIG[os.environ[' +
            '"PEPAGENT_WORKER_ROLE"]][0])'
        )).Trim()
        if ($LASTEXITCODE -ne 0 -or $releaseQueue -ne $roleQueue) {
            throw "worker release task queue differs from the launcher contract"
        }
        $environmentSha256 = (& $pythonPath -S -c (
            'from pepagent.provenance.environment import fingerprint_runtime; ' +
            'print(fingerprint_runtime()[0])'
        )).Trim()
        if ($LASTEXITCODE -ne 0 -or $environmentSha256 -notmatch $expected64) {
            throw "worker environment fingerprint is invalid"
        }
        $env:PEPAGENT_WORKER_ENVIRONMENT_SHA256 = $environmentSha256
        $process = Start-Process -FilePath $pythonPath -ArgumentList @(
            "-S", "-m", "pepagent.workers.v38_temporal_worker"
        ) -WorkingDirectory $releasePath -WindowStyle Hidden -PassThru `
          -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    }
    finally {
        $env:PEPAGENT_WORKER_ROLE = $oldRole
        $env:PEPAGENT_WORKER_SOURCE_REVISION = $oldSource
        $env:PEPAGENT_WORKER_MAX_CONCURRENT_ACTIVITIES = $oldMaximum
        $env:PYTHONPATH = $oldPythonPath
        $env:PEPAGENT_WORK_ROOT = $oldWorkRoot
        $env:PEPAGENT_PLATFORM_RELEASE_SHA256 = $oldPlatformRelease
        $env:PEPAGENT_WORKER_ENVIRONMENT_SHA256 = $oldWorkerEnvironment
    }
    Start-Sleep -Seconds 2
    if ($process.HasExited) {
        $detail = if (Test-Path -LiteralPath $stderr) { Get-Content -Raw -LiteralPath $stderr } else { "" }
        throw "v38 $roleName worker exited during launch: $detail"
    }
    $children = @(Get-CimInstance Win32_Process | Where-Object {
        $_.ParentProcessId -eq $process.Id -and
        $_.Name -eq "python.exe" -and
        $_.CommandLine -like "*pepagent.workers.v38_temporal_worker*"
    })
    if ($children.Count -ne 1) {
        throw "v38 $roleName launcher did not produce exactly one poller child"
    }
    $receipt = [ordered]@{
        schema_version = "v38.local-sequence-worker-receipt.1"
        role = $roleName
        task_queue = $roleQueue
        pid = [int]$children[0].ProcessId
        supervisor_pid = $process.Id
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
        started_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
    $receipt | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $receiptPath -Encoding utf8
    $receipts += [pscustomobject]$receipt
}
$receipts | ConvertTo-Json -Depth 5
