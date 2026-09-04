[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$ReceiptPath = 'reports/postgresql_migration_plan_20260905.json',
    [switch]$Execute
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# This revision is deliberately inventory/plan-only.  It contains the
# replayable command contract, but never starts pg_dump, restore, ssh copy,
# or local deletion.  A future execution change needs a separately reviewed
# revision and an explicit user approval gate.
if ($Execute) {
    throw 'Execution is disabled for this inventory-only plan; obtain explicit migration approval first.'
}

function Get-ChinaTimestamp {
    $zone = [TimeZoneInfo]::FindSystemTimeZoneById('China Standard Time')
    return [TimeZoneInfo]::ConvertTimeFromUtc([DateTime]::UtcNow, $zone).ToString('yyyyMMdd_HHmmss')
}

function Get-RelativeOrAbsolutePath([string]$Path) {
    if ([IO.Path]::IsPathRooted($Path)) { return $Path }
    return [IO.Path]::GetFullPath((Join-Path (Get-Location) $Path))
}

$timestamp = Get-ChinaTimestamp
$resolvedReceipt = Get-RelativeOrAbsolutePath $ReceiptPath
$parent = Split-Path -Parent $resolvedReceipt
if (-not (Test-Path -LiteralPath $parent)) {
    $null = New-Item -ItemType Directory -Path $parent -Force
}

$databaseNames = @('mlflow', 'pepagent', 'postgres', 'temporal', 'temporal_visibility')
$backupNames = [ordered]@{
    globals = "ampgent_pg_globals_${timestamp}_CST.sql"
    databases = @($databaseNames | ForEach-Object {
        "ampgent_pg_$($_)_${timestamp}_CST.dumpdir"
    })
}

$plan = [ordered]@{
    schema_version = 'ampgent.postgresql-migration-plan.1'
    status = 'inventory_only_plan_blocked_until_review'
    generated_at_beijing = [DateTimeOffset]::UtcNow.ToOffset([TimeSpan]::FromHours(8)).ToString('o')
    timestamp_basis = 'Asia/Shanghai'
    backup_timestamp = $timestamp
    source = [ordered]@{
        workstation_endpoint = '127.0.0.1:55432'
        actual_shape = 'remote_authoritative_postgresql_via_existing_ssh_forward'
        local_native_service_or_process = $false
        local_data_directory = $null
        local_data_directory_status = 'not_present_for_active_database; endpoint owner is ssh.exe'
        authoritative_host = '192.168.99.19'
        authoritative_endpoint = '127.0.0.1:55433'
        authoritative_data_directory = '/data1/huangyueshan/pepagent/data/postgresql-v1/data'
        credential_method = 'external DPAPI files plus SSH_ASKPASS; secret values excluded'
    }
    targets = [ordered]@{
        host19 = [ordered]@{
            host = '192.168.99.19'
            approved_root = '/data1/huangyueshan/pepagent/data/migrations/postgresql'
            observed_connectivity = 'ssh_read_only_passed'
            observed_owner = 'huangyueshan:huangyueshan'
            observed_free_bytes = 22391730276
            postgres = '16.4; loopback 55433; read-only psql inventory passed'
            write_gate = 'requires exact AMPgent child-directory owner/capacity recheck'
        }
        synth2 = [ordered]@{
            host = '192.168.99.2'
            approved_roots = @('/sdd_data/pepagent', '/amax')
            observed_connectivity = 'ssh_read_only_passed'
            observed_free_bytes = [ordered]@{ '/sdd_data' = 1182806236; '/amax' = 819207076 }
            observed_owner = [ordered]@{ '/sdd_data' = 'root:root'; '/amax' = 'root:root' }
            postgres = 'not found on PATH; no checked listener/container'
            write_gate = 'blocked_capacity_and_exact_child_owner_unverified'
        }
    }
    databases = $databaseNames
    backup_files = [ordered]@{
        globals = $backupNames.globals
        database_format = 'directory_or_custom_pg_dump; one immutable directory per database'
        database_files = $backupNames.databases
        timestamped_remote_subdirectory = "postgresql/$timestamp`_CST"
    }
    replay_steps = @(
        'Recheck source identity, all non-template databases, roles/ACLs/extensions, and read-only access.',
        'Create exact AMPgent-owned timestamp directory on each target only after owner/capacity gate passes.',
        'Run pg_dumpall --globals-only and one pg_dump --format=directory (or custom) per non-template database.',
        'Compute ordered per-file SHA-256 and byte manifest on each target; compare against source-side manifest.',
        'On .19 only, restore into a fresh timestamped namespace/database names; refuse existing unknown names.',
        'Verify schema/table/index/sequence/function/extension counts plus AMPgent key counts against source receipt.',
        'Replay/read back restore checks and persist a migration receipt with source/target identities and hashes.',
        'Only after both backup manifests and .19 restore verification pass, present the exact local deletion candidate for separate approval.'
    )
    planned_commands = @(
        'pg_dumpall --globals-only --file=<timestamped .19/.2 globals file>',
        'pg_dump --format=directory --file=<timestamped target dir> --dbname=<one non-template database>',
        'sha256sum --check <ordered manifest>',
        'pg_restore --clean is prohibited; restore only into a newly verified namespace/database',
        'DROP/DELETE local PostgreSQL data is prohibited in this plan and requires a separate approval'
    )
    gates = [ordered]@{
        source_inventory_complete = $true
        source_backup_read = $false
        host19_backup_manifest = $false
        synth2_backup_manifest = $false
        host19_independent_restore = $false
        schema_and_ampgent_counts_verified = $false
        local_delete_approved = $false
    }
    local_delete_candidate = $null
    local_delete_status = 'not_authorized_and_no_active_local_pg_data_directory_identified'
}

$json = $plan | ConvertTo-Json -Depth 12
[IO.File]::WriteAllText($resolvedReceipt, $json + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
Write-Output $resolvedReceipt
