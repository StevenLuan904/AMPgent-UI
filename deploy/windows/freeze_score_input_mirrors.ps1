[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string[]]$ChainTip
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$mirrorRoot = '/data0/ampgent-pepglad-huangyueshan/v1/artifacts/score-input-mirror'
$handoffRoot = '/data0/ampgent-pepglad-huangyueshan/v1/artifacts/score-handoff'
$tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$cacheRoot = Join-Path $tempBase ('asm-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
$null = New-Item -ItemType Directory -Path $cacheRoot

$toolRoot = Join-Path $env:LOCALAPPDATA 'Programs\remote-gpu'
$credentialPath = Join-Path $toolRoot 'credentials\synth-target.dpapi'
$askPassPath = Join-Path $toolRoot 'ssh-askpass.cmd'

function Invoke-SshCaptured {
    param([string[]]$Arguments)
    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = 'ssh.exe'
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    foreach ($argument in $Arguments) {
        $null = $startInfo.ArgumentList.Add($argument)
    }
    $process = [Diagnostics.Process]::Start($startInfo)
    $standardOutput = $process.StandardOutput.ReadToEnd()
    $standardError = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    return [ordered]@{
        exit_code = $process.ExitCode
        stdout = @($standardOutput -split "`r?`n" | Where-Object { $_ -ne '' })
        stderr = $standardError.Trim()
    }
}

function Invoke-SourceSsh {
    param([string]$HostName, [string]$Command)
    if ($HostName -eq '192.168.99.2') {
        $result = Invoke-SshCaptured @('-o', 'BatchMode=no', '-o', 'ConnectTimeout=20', '-p', '32224', 'synth@127.0.0.1', $Command)
    } elseif ($HostName -eq '192.168.99.32') {
        $result = Invoke-SshCaptured @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20', '-p', '32223', 'LabServerNewDirect', $Command)
    } else {
        throw "Unsupported source host: $HostName"
    }
    if ($result.exit_code -ne 0) {
        throw "Source SSH failed: host=$HostName rc=$($result.exit_code) stderr=$($result.stderr)"
    }
    return @($result.stdout)
}

function Copy-SourceRun {
    param([string]$HostName, [string]$RunRoot, [string]$Destination)
    if ($HostName -eq '192.168.99.2') {
        & scp -q -r -o BatchMode=no -o ConnectTimeout=20 -P 32224 "synth@127.0.0.1`:$RunRoot" $Destination
    } elseif ($HostName -eq '192.168.99.32') {
        & scp -q -r -o BatchMode=yes -o ConnectTimeout=20 -P 32223 "LabServerNewDirect`:$RunRoot" $Destination
    } else {
        throw "Unsupported source host: $HostName"
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Source SCP failed: host=$HostName root=$RunRoot rc=$LASTEXITCODE"
    }
}

function Invoke-TargetSsh {
    param([string]$Command)
    $result = Invoke-SshCaptured @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20', '-p', '32222', 'TargetServerDirect', $Command)
    if ($result.exit_code -ne 0) {
        throw "Target SSH failed: rc=$($result.exit_code) stderr=$($result.stderr) command=$Command"
    }
    return @($result.stdout)
}

function Write-TargetCanonicalJson {
    param([object]$Payload, [string]$RemotePath)
    $json = $Payload | ConvertTo-Json -Depth 30 -Compress
    $token = [Guid]::NewGuid().ToString('N')
    $localRaw = Join-Path $cacheRoot "json-$token.json"
    $remoteRaw = "$RemotePath.raw-$token"
    try {
        [IO.File]::WriteAllText($localRaw, $json, [Text.UTF8Encoding]::new($false))
        & scp -q -o BatchMode=yes -o ConnectTimeout=20 -P 32222 $localRaw "TargetServerDirect`:$remoteRaw"
        if ($LASTEXITCODE -ne 0) {
            throw "Target JSON SCP failed: path=$RemotePath rc=$LASTEXITCODE"
        }
        $canonicalize = "import json,pathlib;s=pathlib.Path('$remoteRaw');d=json.loads(s.read_text(encoding='utf-8'));p=pathlib.Path('$RemotePath');p.write_text(json.dumps(d,sort_keys=True,separators=(',',':'))+'\n',encoding='utf-8');s.unlink()"
        $canonicalizeB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($canonicalize))
        $remote = "python3 -c 'import base64;exec(base64.b64decode(`"$canonicalizeB64`"))'"
        $null = Invoke-TargetSsh $remote
    } finally {
        if (Test-Path -LiteralPath $localRaw -PathType Leaf) {
            Remove-Item -LiteralPath $localRaw -Force
        }
    }
}

function Get-LocalSha256 {
    param([string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-RemoteShaMap {
    param([string[]]$Lines)
    $result = @{}
    foreach ($line in $Lines) {
        if ($line -match '^([0-9a-f]{64})\s+(.+)$') {
            $result[$Matches[2].Trim()] = $Matches[1]
        }
    }
    return $result
}

$summary = [System.Collections.Generic.List[object]]::new()
try {
    if (-not (Test-Path -LiteralPath $credentialPath -PathType Leaf)) {
        throw "Missing synth credential: $credentialPath"
    }
    if (-not (Test-Path -LiteralPath $askPassPath -PathType Leaf)) {
        throw "Missing SSH askpass helper: $askPassPath"
    }
    $secure = (Get-Content -LiteralPath $credentialPath -Raw).Trim() | ConvertTo-SecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $env:REMOTE_GPU_TARGET_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
    $env:REMOTE_GPU_TARGET_MATCH = 'synth@127.0.0.1'
    $env:SSH_ASKPASS = $askPassPath
    $env:SSH_ASKPASS_REQUIRE = 'force'
    $env:DISPLAY = 'remote-gpu'

    $targetDisk = Invoke-TargetSsh 'df -Pk /data0 | tail -n 1'
    Write-Output ('TARGET_DF ' + ($targetDisk -join ' '))

    foreach ($tip in $ChainTip) {
        $finalPath = "$mirrorRoot/$tip"
        $presence = Invoke-TargetSsh "if [ -e '$finalPath' ]; then echo EXISTS; else echo MISSING; fi"
        if (($presence -join '').Trim() -ne 'MISSING') {
            throw "Append-only mirror already exists: $finalPath"
        }

        $handoffSource = "$handoffRoot/$tip/score_handoff.receipt.json"
        $handoffRaw = Invoke-TargetSsh "cat '$handoffSource'"
        $handoff = ($handoffRaw -join "`n") | ConvertFrom-Json
        $tipCache = Join-Path $cacheRoot $tip.Substring(0, 8)
        $null = New-Item -ItemType Directory -Path $tipCache
        $stagingPath = "$mirrorRoot/.staging-$tip-$([Guid]::NewGuid().ToString('N'))"
        $null = Invoke-TargetSsh "mkdir -p '$stagingPath'"
        $manifestEntries = [System.Collections.Generic.List[object]]::new()

        foreach ($entry in $handoff.entries) {
            $hostName = [string]$entry.host
            if ($hostName -eq '192.168.99.19') {
                continue
            }
            if ([string]$entry.generator -ne 'pepmlm') {
                throw "Unexpected cross-host generator: $($entry.generator)"
            }

            $resultSha = [string]$entry.result_sha256
            $runRoot = ([string]$entry.result_path) -replace '/outputs/[^/]+$', ''
            $target = [string]$entry.target
            $sourcePaths = [ordered]@{
                result = [string]$entry.result_path
                completion = [string]$entry.completion_path
                launch = [string]$entry.launch_receipt_path
                workload = "$runRoot/workload_spec.json"
                request = "$runRoot/requests/$target.json"
            }
            $quotedPaths = $sourcePaths.Values | ForEach-Object { "'$_'" }
            $sourceSha = Get-RemoteShaMap (Invoke-SourceSsh $hostName ('sha256sum ' + ($quotedPaths -join ' ')))
            if ($sourceSha.Count -ne 5) {
                throw "Expected five source hashes: result=$resultSha found=$($sourceSha.Count)"
            }
            $handoffExpected = @{
                result = [string]$entry.result_sha256
                completion = [string]$entry.completion_sha256
                launch = [string]$entry.launch_receipt_sha256
                workload = [string]$entry.workload_spec_file_sha256
            }
            foreach ($role in $handoffExpected.Keys) {
                if ($sourceSha[$sourcePaths[$role]] -ne $handoffExpected[$role]) {
                    throw "Handoff/source SHA mismatch: tip=$tip result=$resultSha role=$role"
                }
            }

            # Keep the transit path below the legacy Windows MAX_PATH boundary used by scp.exe.
            $downloadParent = Join-Path $tipCache ('d-' + $resultSha.Substring(0, 8))
            $null = New-Item -ItemType Directory -Path $downloadParent
            Copy-SourceRun $hostName $runRoot $downloadParent
            $runName = ($runRoot -split '/')[-1]
            $downloadedRoot = Join-Path $downloadParent $runName
            if (-not (Test-Path -LiteralPath $downloadedRoot -PathType Container)) {
                throw "Downloaded run root missing: $downloadedRoot"
            }

            $standardRoot = Join-Path $tipCache $resultSha
            $null = New-Item -ItemType Directory -Path $standardRoot
            $localSources = [ordered]@{
                result = Join-Path $downloadedRoot "outputs\$target.json"
                completion = Join-Path $downloadedRoot 'completion_receipt.json'
                launch = Join-Path $downloadedRoot 'launch_receipt.json'
                workload = Join-Path $downloadedRoot 'workload_spec.json'
                request = Join-Path $downloadedRoot "requests\$target.json"
            }
            $destinationNames = [ordered]@{
                result = 'result.json'
                completion = 'completion_receipt.json'
                launch = 'launch_receipt.json'
                workload = 'workload_spec.json'
                request = 'request.json'
            }
            $fileEntries = [System.Collections.Generic.List[object]]::new()
            foreach ($role in @('result', 'completion', 'launch', 'workload', 'request')) {
                $source = [string]$localSources[$role]
                if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
                    throw "Downloaded evidence missing: $source"
                }
                $localSha = Get-LocalSha256 $source
                if ($localSha -ne $sourceSha[$sourcePaths[$role]]) {
                    throw "Source/local SHA mismatch: tip=$tip result=$resultSha role=$role"
                }
                $destination = Join-Path $standardRoot $destinationNames[$role]
                Copy-Item -LiteralPath $source -Destination $destination
                if ((Get-LocalSha256 $destination) -ne $localSha) {
                    throw "Local standardization SHA mismatch: $destination"
                }
                $fileEntries.Add([ordered]@{
                    host = $hostName
                    role = $role
                    source_path = [string]$sourcePaths[$role]
                    mirrored_path = "$finalPath/$resultSha/$($destinationNames[$role])"
                    sha256 = $localSha
                    result_sha256 = $resultSha
                })
            }

            & scp -q -r -o BatchMode=yes -o ConnectTimeout=20 -P 32222 $standardRoot "TargetServerDirect`:$stagingPath/"
            if ($LASTEXITCODE -ne 0) {
                throw "Target SCP failed: tip=$tip result=$resultSha rc=$LASTEXITCODE"
            }
            foreach ($fileEntry in $fileEntries) {
                $manifestEntries.Add($fileEntry)
            }
            Write-Output "MIRRORED tip=$($tip.Substring(0, 8)) result=$($resultSha.Substring(0, 12)) host=$hostName target=$target files=5"
        }

        $semanticSource = [string]$handoff.semantic_correction_context.receipt_path
        $semanticSha = [string]$handoff.semantic_correction_context.receipt_sha256
        $null = Invoke-TargetSsh "cp -- '$semanticSource' '$stagingPath/semantic_correction.json' && cp -- '$handoffSource' '$stagingPath/handoff_receipt.json'"
        $auxiliarySha = Get-RemoteShaMap (Invoke-TargetSsh "sha256sum '$stagingPath/semantic_correction.json' '$stagingPath/handoff_receipt.json'")
        if ($auxiliarySha["$stagingPath/semantic_correction.json"] -ne $semanticSha) {
            throw "Semantic-correction SHA mismatch: tip=$tip"
        }
        if ($auxiliarySha["$stagingPath/handoff_receipt.json"] -ne $tip) {
            throw "Handoff receipt SHA mismatch: tip=$tip"
        }

        $createdAt = [DateTimeOffset]::UtcNow.ToString('o')
        $manifest = [ordered]@{
            schema_version = 'ampgent.score-input-mirror-manifest.v1'
            append_only = $true
            chain_tip = $tip
            created_at = $createdAt
            external_result_count = ($manifestEntries.Count / 5)
            files_per_result = 5
            file_count = $manifestEntries.Count
            entries = $manifestEntries
            semantic_correction = [ordered]@{
                source_path = $semanticSource
                mirrored_path = "$finalPath/semantic_correction.json"
                sha256 = $semanticSha
            }
            handoff_receipt = [ordered]@{
                source_path = $handoffSource
                mirrored_path = "$finalPath/handoff_receipt.json"
                sha256 = $tip
            }
        }
        Write-TargetCanonicalJson $manifest "$stagingPath/mirror_manifest.json"
        $manifestShaLine = Invoke-TargetSsh "sha256sum '$stagingPath/mirror_manifest.json'"
        $manifestSha = ((@($manifestShaLine)[0]) -split '\s+')[0]

        $receipt = [ordered]@{
            schema_version = 'ampgent.score-input-mirror-receipt.v1'
            append_only = $true
            chain_tip = $tip
            created_at = [DateTimeOffset]::UtcNow.ToString('o')
            manifest_path = "$finalPath/mirror_manifest.json"
            manifest_sha256 = $manifestSha
            file_count = $manifestEntries.Count
            external_result_count = ($manifestEntries.Count / 5)
            files_per_result = 5
            auxiliary_file_count = 2
            semantic_correction = [ordered]@{
                path = "$finalPath/semantic_correction.json"
                sha256 = $semanticSha
                verified = $true
            }
            handoff_receipt = [ordered]@{
                path = "$finalPath/handoff_receipt.json"
                sha256 = $tip
                verified = $true
            }
            verified = $true
        }
        Write-TargetCanonicalJson $receipt "$stagingPath/mirror_receipt.json"
        $receiptShaLine = Invoke-TargetSsh "sha256sum '$stagingPath/mirror_receipt.json'"
        $receiptSha = ((@($receiptShaLine)[0]) -split '\s+')[0]

        $verifyScript = @"
import hashlib,json,pathlib
staging=pathlib.Path('$stagingPath')
manifest=json.loads((staging/'mirror_manifest.json').read_text())
assert manifest['chain_tip']=='$tip'
assert manifest['files_per_result']==5
assert len(manifest['entries'])==manifest['file_count']
by_result={}
for item in manifest['entries']:
    by_result.setdefault(item['result_sha256'],[]).append(item)
for result_sha,items in by_result.items():
    assert len(items)==5,(result_sha,len(items))
    assert {x['role'] for x in items}=={'result','completion','launch','workload','request'}
    for item in items:
        path=staging/result_sha/pathlib.Path(item['mirrored_path']).name
        assert path.is_file(),path
        actual=hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual==item['sha256'],(path,actual,item['sha256'])
assert hashlib.sha256((staging/'semantic_correction.json').read_bytes()).hexdigest()=='$semanticSha'
assert hashlib.sha256((staging/'handoff_receipt.json').read_bytes()).hexdigest()=='$tip'
assert hashlib.sha256((staging/'mirror_manifest.json').read_bytes()).hexdigest()=='$manifestSha'
assert hashlib.sha256((staging/'mirror_receipt.json').read_bytes()).hexdigest()=='$receiptSha'
print(json.dumps({'external_results':len(by_result),'entry_files':manifest['file_count'],'verified':True},sort_keys=True))
"@
        $verifyB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($verifyScript))
        $verifyRemote = "python3 -c 'import base64;exec(base64.b64decode(`"$verifyB64`"))'"
        $verification = Invoke-TargetSsh $verifyRemote

        $null = Invoke-TargetSsh "if [ -e '$finalPath' ]; then exit 17; fi; mv -- '$stagingPath' '$finalPath'"
        $postFreezeSha = Get-RemoteShaMap (Invoke-TargetSsh "sha256sum '$finalPath/mirror_manifest.json' '$finalPath/mirror_receipt.json' '$finalPath/semantic_correction.json' '$finalPath/handoff_receipt.json'")
        if ($postFreezeSha["$finalPath/mirror_manifest.json"] -ne $manifestSha -or
            $postFreezeSha["$finalPath/mirror_receipt.json"] -ne $receiptSha -or
            $postFreezeSha["$finalPath/semantic_correction.json"] -ne $semanticSha -or
            $postFreezeSha["$finalPath/handoff_receipt.json"] -ne $tip) {
            throw "Post-freeze SHA mismatch: tip=$tip"
        }
        $summary.Add([ordered]@{
            tip = $tip
            external_results = ($manifestEntries.Count / 5)
            entry_files = $manifestEntries.Count
            manifest_sha256 = $manifestSha
            receipt_sha256 = $receiptSha
            uri = "ssh://huangyueshan@192.168.99.19$finalPath/"
            verification = ($verification -join '')
        })
        Write-Output "FROZEN tip=$tip manifest_sha=$manifestSha receipt_sha=$receiptSha external_results=$($manifestEntries.Count / 5) entry_files=$($manifestEntries.Count)"
    }

    $summary | ConvertTo-Json -Depth 10 -Compress
} finally {
    foreach ($name in 'REMOTE_GPU_TARGET_PASSWORD', 'REMOTE_GPU_TARGET_MATCH', 'SSH_ASKPASS', 'SSH_ASKPASS_REQUIRE', 'DISPLAY') {
        Remove-Item "Env:$name" -ErrorAction SilentlyContinue
    }
    $resolvedCache = [IO.Path]::GetFullPath($cacheRoot)
    if (-not $resolvedCache.StartsWith($tempBase, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing cache cleanup outside temp: $resolvedCache"
    }
    if (Test-Path -LiteralPath $resolvedCache) {
        Remove-Item -LiteralPath $resolvedCache -Recurse -Force
    }
}
