[CmdletBinding()]
param(
    [ValidateSet('progress-float')] [string]$Mode = 'progress-float',
    [switch]$Close,
    [switch]$UseInstalledProgressFloat,
    [int]$RefreshSeconds = 30,
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
    $remote = 'cat /data1/huangyueshan/pepagent/data/run-cache/rosetta-poola-top150-coarse20-host019-gpu0-1-20260902-v1/progress.json'
    $output = & ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 -p 32222 TargetServerDirect $remote 2>&1
    if ($LASTEXITCODE -ne 0) { throw "host .19 unavailable: $($output -join ' ')" }
    return ($output -join "`n") | ConvertFrom-Json
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
        $remote = 'cat /sdd_data/pepagent/ampgent/structure/rosetta-poola-top150-coarse20-synth-gpu1-3-20260902-v1/progress.json'
        $output = & ssh -o BatchMode=no -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 -p 32224 synth@127.0.0.1 $remote 2>&1
        if ($LASTEXITCODE -ne 0) { throw "synth unavailable: $($output -join ' ')" }
        return ($output -join "`n") | ConvertFrom-Json
    } finally {
        'REMOTE_GPU_TARGET_PASSWORD', 'REMOTE_GPU_TARGET_MATCH', 'SSH_ASKPASS', 'SSH_ASKPASS_REQUIRE', 'DISPLAY' |
            ForEach-Object { Remove-Item "Env:$_" -ErrorAction SilentlyContinue }
    }
}

function Get-RosettaSnapshot {
    $host19 = Invoke-Host19Progress
    $synth = Invoke-SynthProgress
    $completed = [int]$host19.rosetta_succeeded + [int]$synth.rosetta_succeeded
    $boltz = [int]$host19.boltz_succeeded + [int]$synth.boltz_succeeded
    $failed = [int]$host19.failed + [int]$synth.failed
    return [ordered]@{
        schema_version = 'ampgent.rosetta-progress-float.1'
        observed_at = [DateTimeOffset]::UtcNow.ToString('o')
        completed = $completed
        total = 900
        boltz_succeeded = $boltz
        failed = $failed
        host19 = [ordered]@{
            completed = [int]$host19.rosetta_succeeded
            total = 360
            boltz_succeeded = [int]$host19.boltz_succeeded
            pending = [int]$host19.pending
        }
        synth = [ordered]@{
            completed = [int]$synth.rosetta_succeeded
            total = 540
            boltz_succeeded = [int]$synth.boltz_succeeded
            pending = [int]$synth.pending
        }
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
                label = "AMPgent Rosetta dG · .19 $($snapshot.host19.completed)/$($snapshot.host19.total) · synth $($snapshot.synth.completed)/$($snapshot.synth.total) · Boltz $($snapshot.boltz_succeeded)"
            } | ConvertTo-Json -Compress
            & $installedBridge $payload | Out-Null
        } catch {
            $errorPayload = [ordered]@{
                current = 0
                total = 900
                label = "AMPgent Rosetta progress refresh failed"
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
        $title.Text = "Rosetta dG  $($snapshot.completed)/$($snapshot.total)  ($($percent.ToString('0.0'))%)"
        $detail.Text = ".19   $($snapshot.host19.completed)/$($snapshot.host19.total)  Boltz $($snapshot.host19.boltz_succeeded)`r`nsynth $($snapshot.synth.completed)/$($snapshot.synth.total)  Boltz $($snapshot.synth.boltz_succeeded)"
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
