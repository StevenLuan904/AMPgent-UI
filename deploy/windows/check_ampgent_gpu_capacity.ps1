[CmdletBinding()]
param(
    [string]$Host19SshTarget = "TargetServerDirect",
    [int]$Host19SshPort = 32222,
    [string]$Host32SshTarget = "LabServerNewDirect",
    [int]$Host32SshPort = 32223,
    [string]$StatePath = "var/state/ampgent-gpu-capacity.json",
    [int]$ConnectTimeoutSeconds = 10,
    [int]$MaximumUsedMemoryMiB = 256,
    [int]$MaximumUtilizationPercent = 5
)

$ErrorActionPreference = "Stop"

function Invoke-CardProbe {
    param(
        [Parameter(Mandatory)] [string]$SshTarget,
        [Parameter(Mandatory)] [string]$HostLabel,
        [Parameter(Mandatory)] [int]$GpuIndex,
        [int]$SshPort = 0
    )

    # Probe one explicitly allowed device at a time. Never replace this with an
    # unscoped nvidia-smi call: .32 GPU2/GPU3 are read-only probes and must
    # never become dispatchable capacity.
    $remote = @"
set -eu
nvidia-smi -i $GpuIndex --query-gpu=index,uuid,memory.used,utilization.gpu --format=csv,noheader,nounits
echo __PROCESSES__
nvidia-smi -i $GpuIndex --query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits || true
echo __DECLARATIONS__
for p in /proc/[0-9]*; do
  if { tr '\0' '\n' < "`$p/environ"; } 2>/dev/null | grep -q "^CUDA_VISIBLE_DEVICES=$GpuIndex`$"; then
    echo "`${p##*/}"
  fi
done
"@
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $sshArguments = @('-o', 'BatchMode=yes', '-o', "ConnectTimeout=$ConnectTimeoutSeconds")
    if ($SshPort -gt 0) {
        $sshArguments += @('-p', "$SshPort")
    }
    $sshArguments += @($SshTarget, $remote)
    $output = & ssh @sshArguments 2>&1
    $sshExitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorActionPreference
    if ($sshExitCode -ne 0) {
        return [ordered]@{
            host = $HostLabel
            gpu_index = $GpuIndex
            status = "unreachable"
            detail = (($output | Out-String).Trim())
            idle = $false
        }
    }

    $lines = @($output | ForEach-Object { "$($_)".Trim() } | Where-Object { $_ })
    $processMarker = [Array]::IndexOf($lines, "__PROCESSES__")
    $declarationMarker = [Array]::IndexOf($lines, "__DECLARATIONS__")
    if ($processMarker -lt 1 -or $declarationMarker -lt $processMarker) {
        return [ordered]@{
            host = $HostLabel
            gpu_index = $GpuIndex
            status = "invalid_probe_output"
            detail = ($lines -join " | ")
            idle = $false
        }
    }

    $gpuFields = @($lines[0].Split(",") | ForEach-Object { $_.Trim() })
    $processes = if ($declarationMarker -gt ($processMarker + 1)) {
        @($lines[($processMarker + 1)..($declarationMarker - 1)])
    } else { @() }
    $declarations = if ($lines.Count -gt ($declarationMarker + 1)) {
        @($lines[($declarationMarker + 1)..($lines.Count - 1)])
    } else { @() }
    $usedMemory = [int]$gpuFields[2]
    $utilization = [int]$gpuFields[3]
    $idle = (
        $usedMemory -le $MaximumUsedMemoryMiB -and
        $utilization -le $MaximumUtilizationPercent -and
        $processes.Count -eq 0 -and
        $declarations.Count -eq 0
    )
    return [ordered]@{
        host = $HostLabel
        gpu_index = $GpuIndex
        status = "observed"
        uuid = $gpuFields[1]
        memory_used_mib = $usedMemory
        utilization_percent = $utilization
        compute_processes = $processes
        cuda_visible_devices_declarations = $declarations
        idle = $idle
    }
}

$observations = @()
foreach ($gpuIndex in 0..7) {
    $observations += Invoke-CardProbe -SshTarget $Host19SshTarget -HostLabel "192.168.99.19" -GpuIndex $gpuIndex -SshPort $Host19SshPort
}
# GPU0/GPU1 may become dispatchable after normal ownership checks. GPU2/GPU3
# are read-only observation lanes and are excluded from idle capacity below.
foreach ($gpuIndex in 0..3) {
    $observations += Invoke-CardProbe -SshTarget $Host32SshTarget -HostLabel "192.168.99.32" -GpuIndex $gpuIndex -SshPort $Host32SshPort
}

$idleKeys = @(
    $observations |
        Where-Object { $_.idle -and -not ($_.host -eq "192.168.99.32" -and $_.gpu_index -in @(2, 3)) } |
        ForEach-Object { "$($_.host):$($_.gpu_index)" } |
        Sort-Object
)
$observationKeys = @(
    $observations |
        ForEach-Object { "$($_.host):$($_.gpu_index):$($_.status):$($_.idle)" } |
        Sort-Object
)
$previousObservationKeys = @()
if (Test-Path -LiteralPath $StatePath) {
    try {
        $previous = Get-Content -Raw -LiteralPath $StatePath | ConvertFrom-Json
        $previousObservationKeys = @($previous.observation_keys | Sort-Object)
    } catch {
        $previousObservationKeys = @()
    }
}
$wakeRequired = (Compare-Object $previousObservationKeys $observationKeys).Count -gt 0
$state = [ordered]@{
    schema_version = "ampgent.gpu-capacity-snapshot.1"
    captured_at = [DateTimeOffset]::UtcNow.ToString("o")
    allowed_probe_scope = @(
        [ordered]@{ host = "192.168.99.19"; gpu_indices = @(0, 1, 2, 3, 4, 5, 6, 7) },
        [ordered]@{ host = "192.168.99.32"; gpu_indices = @(0, 1, 2, 3) }
    )
    prohibited_use_scope = @(
        [ordered]@{ host = "192.168.99.32"; gpu_indices = @(2, 3) }
    )
    idle_gpu_keys = $idleKeys
    observation_keys = $observationKeys
    wake_required = $wakeRequired
    observations = $observations
}

$stateDirectory = Split-Path -Parent $StatePath
if ($stateDirectory) {
    New-Item -ItemType Directory -Force -Path $stateDirectory | Out-Null
}
$json = $state | ConvertTo-Json -Depth 8
Set-Content -LiteralPath $StatePath -Value $json -Encoding utf8
$json
"WAKE_REQUIRED=$($wakeRequired.ToString().ToLowerInvariant())"
