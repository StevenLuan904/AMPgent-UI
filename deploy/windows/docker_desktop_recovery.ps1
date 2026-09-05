[CmdletBinding()]
param(
    [switch]$AttemptRepair,
    [switch]$StartOnce,
    [string]$ReceiptPath = 'reports/docker_desktop_recovery_receipt_20260905.json'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

$runtimeRoot = 'C:\Users\31948\AppData\Local\Docker\run'
$allowedNames = @('dockerInference', 'dockerEthernetVfkit', 'userAnalyticsOtlpHttp.sock')
$observations = @()
$repairResults = @()

foreach ($name in $allowedNames) {
    $path = Join-Path $runtimeRoot $name
    $item = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    if ($null -eq $item) {
        $observations += [ordered]@{ name = $name; status = 'missing' }
        continue
    }

    $isInvalidRuntimeEntry = (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) -and ($item.Length -eq 0)
    $observations += [ordered]@{
        name = $name
        status = if ($isInvalidRuntimeEntry) { 'zero_byte_reparse_point' } else { 'not_targeted' }
        length = $item.Length
        attributes = $item.Attributes.ToString()
    }

    if (-not $AttemptRepair -or -not $isInvalidRuntimeEntry) {
        continue
    }

    try {
        Remove-Item -LiteralPath $path -Force -ErrorAction Stop
        $repairResults += [ordered]@{ name = $name; method = 'Remove-Item'; status = 'removed' }
        continue
    }
    catch {
        $repairResults += [ordered]@{ name = $name; method = 'Remove-Item'; status = 'failed'; category = $_.Exception.GetType().Name }
    }

    $fsutilOutput = & fsutil.exe reparsepoint delete $path 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) {
        $repairResults += [ordered]@{ name = $name; method = 'fsutil-reparsepoint-delete'; status = 'removed' }
    }
    else {
        $repairResults += [ordered]@{ name = $name; method = 'fsutil-reparsepoint-delete'; status = 'failed'; category = 'error_1920'; detail = $fsutilOutput.Trim() }
    }
}

$launch = [ordered]@{ requested = [bool]$StartOnce; status = 'not_requested' }
if ($StartOnce) {
    $desktopExe = 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
    $existing = Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        $launch = [ordered]@{ requested = $true; status = 'skipped_existing_process'; pids = @($existing.Id) }
    }
    elseif (Test-Path -LiteralPath $desktopExe) {
        $started = Start-Process -FilePath $desktopExe -WindowStyle Hidden -PassThru
        $launch = [ordered]@{ requested = $true; status = 'launched_once'; pid = $started.Id }
    }
    else {
        $launch = [ordered]@{ requested = $true; status = 'executable_missing' }
    }
}

$receipt = [ordered]@{
    schema_version = 1
    generated_at = (Get-Date).ToString('o')
    mode = if ($AttemptRepair) { 'bounded_repair' } else { 'read_only' }
    runtime_root = $runtimeRoot
    allowlist = $allowedNames
    runtime_observations = $observations
    repair_results = $repairResults
    launch = $launch
    safety = [ordered]@{
        factory_reset = $false
        vhd_volume_image_container_database_mutation = $false
        docker_log_cleanup = $false
        remote_or_gpu_action = $false
    }
}

$parent = Split-Path -Parent $ReceiptPath
if (-not [string]::IsNullOrWhiteSpace($parent)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
}
$receipt | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ReceiptPath -Encoding UTF8
Write-Output ("Wrote " + $ReceiptPath)
