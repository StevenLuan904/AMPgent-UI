[CmdletBinding()]
param(
    [string]$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$StatePath = 'var\state\v38-agent-controller.json',
    [int]$TickSeconds = 300
)

$ErrorActionPreference = 'Stop'
if ($TickSeconds -lt 60) {
    throw 'TickSeconds must be at least 60.'
}

$python = Join-Path $RepositoryRoot '.venv\Scripts\python.exe'
$capacity = Join-Path $RepositoryRoot 'deploy\windows\check_ampgent_gpu_capacity.ps1'
$state = Join-Path $RepositoryRoot $StatePath
$logRoot = Join-Path $RepositoryRoot 'var\log'
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$log = Join-Path $logRoot 'v38-agent-controller.log'

while ($true) {
    $observedAt = [DateTimeOffset]::UtcNow.ToString('o')
    try {
        & $capacity 2>&1 | Out-File -LiteralPath $log -Append -Encoding utf8
        & $python -m pepagent.v38_agent_controller_cli --mode tick --state $state 2>&1 |
            Out-File -LiteralPath $log -Append -Encoding utf8
        "[$observedAt] controller_tick=ok" | Out-File -LiteralPath $log -Append -Encoding utf8
    }
    catch {
        "[$observedAt] controller_tick=failed error=$($_.Exception.Message)" |
            Out-File -LiteralPath $log -Append -Encoding utf8
    }
    Start-Sleep -Seconds $TickSeconds
}
