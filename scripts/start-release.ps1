[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)] [int]$Port = 4173,
    [switch]$Install,
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $repoRoot

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw 'npm is required. Install a supported Node.js runtime first.'
}

function Invoke-Npm {
    param([Parameter(Mandatory)] [string[]]$Arguments)
    & npm.cmd @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "npm $($Arguments -join ' ') failed with exit code $LASTEXITCODE."
    }
}

if ($Install -or -not (Test-Path -LiteralPath (Join-Path $repoRoot 'node_modules'))) {
    Invoke-Npm -Arguments @('ci')
}
if (-not $SkipBuild) {
    Invoke-Npm -Arguments @('run', 'build')
}

Write-Host "`nAMPgent release preview: http://127.0.0.1:$Port/" -ForegroundColor Green
Write-Host 'Press Ctrl+C to stop the server.' -ForegroundColor DarkGray
Invoke-Npm -Arguments @('run', 'preview', '--', '--port', "$Port")

