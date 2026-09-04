param(
    [Parameter(Mandatory = $true)]
    [string]$BaseUrl
)

$ErrorActionPreference = 'Stop'
$startedAt = Get-Date

try {
    $runsResponse = Invoke-RestMethod -Method Get -Uri "$BaseUrl/v1/observer/runs?limit=12" -TimeoutSec 35
    $latestRunId = @($runsResponse.runs)[0].id
    if ($latestRunId) {
        Invoke-RestMethod -Method Get -Uri "$BaseUrl/v1/observer/runs/$latestRunId" -TimeoutSec 35 | Out-Null
    }
    $elapsed = [math]::Round(((Get-Date) - $startedAt).TotalSeconds, 2)
    Write-Output "Observer cache refreshed in ${elapsed}s."
}
catch {
    # Warm-up is deliberately non-blocking. The interface owns retry and the
    # visible error state, so this helper only records a concise diagnostic.
    Write-Error "Observer cache warm-up failed: $($_.Exception.Message)"
    exit 1
}
