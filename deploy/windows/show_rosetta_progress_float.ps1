[CmdletBinding()]
param(
    [ValidateSet('progress-float')] [string]$Mode = 'progress-float',
    [switch]$Close,
    [switch]$UseInstalledProgressFloat,
    [int]$RefreshSeconds = 10,
    [string]$StatePath = 'var/state/ampgent-rosetta-progress-float.json'
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$stateAbsolute = if ([IO.Path]::IsPathRooted($StatePath)) {
    $StatePath
} else {
    Join-Path $projectRoot $StatePath
}
$pidPath = Join-Path (Split-Path -Parent $stateAbsolute) 'ampgent-rosetta-progress-float.pid'

if ($Close) {
    if (Test-Path -LiteralPath $pidPath) {
        $floatPid = [int](Get-Content -LiteralPath $pidPath -Raw).Trim()
        Stop-Process -Id $floatPid -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
    }
    $installedBridge = Join-Path $env:LOCALAPPDATA 'Programs\ProgressFloat\progress-float.exe'
    if (Test-Path -LiteralPath $installedBridge) {
        & $installedBridge --close | Out-Null
    }
    exit 0
}

$stateDirectory = Split-Path -Parent $stateAbsolute
New-Item -ItemType Directory -Force -Path $stateDirectory | Out-Null
Set-Content -LiteralPath $pidPath -Value $PID -Encoding ascii

function Invoke-Host19Progress {
    $remote = @'
for f in \
 /data1/huangyueshan/pepagent/data/run-cache/rosetta-poola-priority276-coarse5-host019-gpu2-7-20260902-v1/coarse5_progress.json \
 /data1/huangyueshan/pepagent/data/run-cache/rosetta-poola-priority-v3-append16-host019-gpu0-1-20260902-v1/coarse5_progress.json \
 /data1/huangyueshan/pepagent/data/run-cache/rosetta-poola-v6-reserve100-diff53-host019-gpu0-1-20260902-v1/coarse5_progress.json \
 /data1/huangyueshan/pepagent/data/run-cache/rosetta-poola-v7-pbp2a-extension59-host019-gpu0-1-20260902-v1/coarse5_progress.json \
 /data1/huangyueshan/pepagent/data/run-cache/rosetta-poola-v8-gap-targets-extension177-host019-gpu0-1-20260902-v1/coarse5_progress.json; do
  printf 'POOL_A_PROGRESS '; jq -c . "$f"
done
'@
    $output = & ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 -p 32222 TargetServerDirect $remote 2>&1
    if ($LASTEXITCODE -ne 0) { throw "host .19 unavailable: $($output -join ' ')" }
    return $output
}

function Invoke-SynthProgress {
    $toolRoot = Join-Path $env:LOCALAPPDATA 'Programs\remote-gpu'
    $credentialPath = Join-Path $toolRoot 'credentials\synth-target.dpapi'
    $secure = (Get-Content -LiteralPath $credentialPath -Raw).Trim() | ConvertTo-SecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $env:REMOTE_GPU_TARGET_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
    $env:REMOTE_GPU_TARGET_MATCH = 'synth@127.0.0.1'
    $env:SSH_ASKPASS = Join-Path $toolRoot 'ssh-askpass.cmd'
    $env:SSH_ASKPASS_REQUIRE = 'force'
    $env:DISPLAY = 'remote-gpu'
    try {
        $remote = @'
for f in \
 /sdd_data/pepagent/ampgent/structure/rosetta-poola-priority-v2-append22-synth-gpu5-7-20260902-v1/coarse5_progress.json \
 /sdd_data/pepagent/ampgent/structure/rosetta-poola-v4-reserve100-diff206-synth-gpu2-3-20260902-v1/coarse5_progress.json \
 /sdd_data/pepagent/ampgent/structure/rosetta-poola-v5-reserve100-diff75-synth-gpu1-6-20260902-v1/coarse5_progress.json; do
  printf 'POOL_A_PROGRESS '; jq -c . "$f"
done
'@
        $output = & ssh -o BatchMode=no -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 -p 32224 synth@127.0.0.1 $remote 2>&1
        if ($LASTEXITCODE -ne 0) { throw "synth unavailable: $($output -join ' ')" }
        return $output
    } finally {
        'REMOTE_GPU_TARGET_PASSWORD', 'REMOTE_GPU_TARGET_MATCH', 'SSH_ASKPASS', 'SSH_ASKPASS_REQUIRE', 'DISPLAY' |
            ForEach-Object { Remove-Item "Env:$_" -ErrorAction SilentlyContinue }
    }
}

function Get-RosettaSnapshot {
    function Parse-ProgressLines($lines) {
        $items = @()
        foreach ($line in @($lines)) {
            $text = [string]$line
            if ($text.StartsWith('POOL_A_PROGRESS')) {
                $items += ,($text.Substring(16).Trim() | ConvertFrom-Json)
            }
        }
        return $items
    }
    $host19 = @(Parse-ProgressLines (Invoke-Host19Progress))
    $synth = @(Parse-ProgressLines (Invoke-SynthProgress))
    [int]$host19Pending = 0; [int]$synthPending = 0; [int]$boltz = 0; [int]$failed = 0
    [int]$host19Completed = 0; [int]$synthCompleted = 0
    [int]$host19Total = 0; [int]$synthTotal = 0
    foreach ($item in $host19) {
        [int]$pending = [int](@($item.pending)[0])
        [int]$completedItem = [int](@($item.rosetta_succeeded)[0])
        [int]$failedItem = [int](@($item.failed)[0])
        $host19Pending += $pending
        $host19Completed += $completedItem
        $host19Total += $pending + $completedItem + $failedItem
        $boltz += [int](@($item.boltz_succeeded)[0])
        $failed += $failedItem
    }
    foreach ($item in $synth) {
        [int]$pending = [int](@($item.pending)[0])
        [int]$completedItem = [int](@($item.rosetta_succeeded)[0])
        [int]$failedItem = [int](@($item.failed)[0])
        $synthPending += $pending
        $synthCompleted += $completedItem
        $synthTotal += $pending + $completedItem + $failedItem
        $boltz += [int](@($item.boltz_succeeded)[0])
        $failed += $failedItem
    }
    [int]$completed = [int]$host19Completed + [int]$synthCompleted
    [int]$total = [int]$host19Total + [int]$synthTotal
    return [pscustomobject]@{
        schema_version = 'ampgent.rosetta-progress-float.1'
        observed_at = [DateTimeOffset]::UtcNow.ToString('o')
        completed = [int]$completed
        total = [int]$total
        boltz_succeeded = [int]$boltz
        failed = [int]$failed
        host19_completed = [int]$host19Completed
        host19_total = [int]$host19Total
        host19_boltz_succeeded = [int]($host19 | ForEach-Object {[int]$_.boltz_succeeded} | Measure-Object -Sum).Sum
        host19_pending = [int]$host19Pending
        synth_completed = [int]$synthCompleted
        synth_total = [int]$synthTotal
        synth_boltz_succeeded = [int]($synth | ForEach-Object {[int]$_.boltz_succeeded} | Measure-Object -Sum).Sum
        synth_pending = [int]$synthPending
    }
}

if ($UseInstalledProgressFloat) {
    $installedBridge = Join-Path $env:LOCALAPPDATA 'Programs\ProgressFloat\progress-float.exe'
    if (-not (Test-Path -LiteralPath $installedBridge)) {
        throw "ProgressFloat bridge is not installed: $installedBridge"
    }
    while ($true) {
        try {
            $snapshot = Get-RosettaSnapshot
            $temporary = "$stateAbsolute.$PID.tmp"
            $snapshot | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $temporary -Encoding utf8
            Move-Item -LiteralPath $temporary -Destination $stateAbsolute -Force
            $payload = [ordered]@{
                current = [int]$snapshot.completed
                total = [int]$snapshot.total
                label = "AMPgent Pool A Rosetta 5-decoy · $($snapshot.completed)/$($snapshot.total) · .19 $($snapshot.host19_completed)/$($snapshot.host19_total) · synth $($snapshot.synth_completed)/$($snapshot.synth_total)"
            } | ConvertTo-Json -Compress
            & $installedBridge $payload | Out-Null
        } catch {
            $errorSnapshot = [ordered]@{
                schema_version = 'ampgent.rosetta-progress-float.1'
                observed_at = [DateTimeOffset]::UtcNow.ToString('o')
                completed = 0
                total = 0
                failed = 0
                error = $_.Exception.Message
                stack = $_.ScriptStackTrace
            }
            $errorSnapshot | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $stateAbsolute -Encoding utf8
            $errorPayload = [ordered]@{
                current = 0
                total = 0
                label = "AMPgent Pool A Rosetta progress refresh failed"
            } | ConvertTo-Json -Compress
            & $installedBridge $errorPayload | Out-Null
        }
        Start-Sleep -Seconds ([Math]::Max(10, $RefreshSeconds))
    }
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()

$form = New-Object Windows.Forms.Form
$form.Text = 'AMPgent Rosetta dG'
$form.TopMost = $true
$form.FormBorderStyle = [Windows.Forms.FormBorderStyle]::FixedToolWindow
$form.StartPosition = [Windows.Forms.FormStartPosition]::Manual
$form.Size = New-Object Drawing.Size(430, 155)
$workingArea = [Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$form.Location = New-Object Drawing.Point(($workingArea.Right - $form.Width - 16), ($workingArea.Bottom - $form.Height - 16))

$title = New-Object Windows.Forms.Label
$title.Location = New-Object Drawing.Point(14, 12)
$title.Size = New-Object Drawing.Size(390, 24)
$title.Font = New-Object Drawing.Font('Segoe UI', 11, [Drawing.FontStyle]::Bold)
$form.Controls.Add($title)

$bar = New-Object Windows.Forms.ProgressBar
$bar.Location = New-Object Drawing.Point(14, 42)
$bar.Size = New-Object Drawing.Size(390, 20)
$bar.Minimum = 0
$bar.Maximum = 900
$form.Controls.Add($bar)

$detail = New-Object Windows.Forms.Label
$detail.Location = New-Object Drawing.Point(14, 70)
$detail.Size = New-Object Drawing.Size(390, 42)
$detail.Font = New-Object Drawing.Font('Consolas', 9)
$form.Controls.Add($detail)

$status = New-Object Windows.Forms.Label
$status.Location = New-Object Drawing.Point(14, 113)
$status.Size = New-Object Drawing.Size(390, 18)
$status.ForeColor = [Drawing.Color]::DimGray
$form.Controls.Add($status)

$refresh = {
    try {
        $snapshot = Get-RosettaSnapshot
        $temporary = "$stateAbsolute.$PID.tmp"
        $snapshot | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $temporary -Encoding utf8
        Move-Item -LiteralPath $temporary -Destination $stateAbsolute -Force
        $bar.Value = [Math]::Min($bar.Maximum, [int]$snapshot.completed)
        $percent = 100.0 * [int]$snapshot.completed / [int]$snapshot.total
        $title.Text = "Pool A Rosetta dG  $($snapshot.completed)/$($snapshot.total)  ($($percent.ToString('0.0'))%)"
        $detail.Text = ".19   $($snapshot.host19_completed)/$($snapshot.host19_total)  Boltz $($snapshot.host19_boltz_succeeded)`r`nsynth $($snapshot.synth_completed)/$($snapshot.synth_total)  Boltz $($snapshot.synth_boltz_succeeded)"
        $status.Text = "failed=$($snapshot.failed)  refreshed $([DateTime]::Now.ToString('HH:mm:ss'))"
    } catch {
        $status.Text = "refresh failed: $($_.Exception.Message)"
        $status.ForeColor = [Drawing.Color]::Firebrick
    }
}

& $refresh
$timer = New-Object Windows.Forms.Timer
$timer.Interval = [Math]::Max(10, $RefreshSeconds) * 1000
$timer.Add_Tick($refresh)
$timer.Start()
$form.Add_FormClosed({
    $timer.Stop()
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
})
[Windows.Forms.Application]::Run($form)
