#requires -Version 7.0
param(
    [Parameter(Mandatory = $true)]
    [string]$ArchiveRoot,
    [Parameter(Mandatory = $true)]
    [string]$FreshStudy,
    [Parameter(Mandatory = $true)]
    [string]$ControlSupplementRoot,
    [Parameter(Mandatory = $true)]
    [string]$ReplayRoot
)

$ErrorActionPreference = 'Stop'
$ImageId = 'sha256:80471eba645614a25862a60e6167a66c17e77e3f95aee97442eb5417f33d4c58'

if (Test-Path -LiteralPath $ReplayRoot) { throw '必须使用不存在的新输出目录；保留旧结果或失败。' }
if (-not (Test-Path -LiteralPath $FreshStudy -PathType Container)) { throw 'fresh B8 study 不存在。' }
$SupplementAux = Join-Path $ControlSupplementRoot 'evidence/original_response_format_recovery_v1'
if (-not (Test-Path -LiteralPath $ArchiveRoot -PathType Container)) { throw 'fresh B8 archive 不存在。' }
$ActualImageId = & docker image inspect --format '{{.Id}}' $ImageId
if ($LASTEXITCODE -ne 0 -or $ActualImageId.Trim() -cne $ImageId) { throw '先按 B8 说明加载并核对同一不可变依赖镜像；不能替换版本。' }

New-Item -ItemType Directory -Path $ReplayRoot | Out-Null
$InputDirectory = New-Item -ItemType Directory -Path (Join-Path $ReplayRoot 'input')
$ControlDirectory = New-Item -ItemType Directory -Path (Join-Path $ReplayRoot 'control')
foreach ($FileName in @('recover_original_response_format_v1.py','offline_analysis_runner.py','guard_configuration.json')) {
    Copy-Item -LiteralPath (Join-Path $SupplementAux ('control/' + $FileName)) -Destination $ControlDirectory.FullName
}
Copy-Item -LiteralPath (Join-Path $SupplementAux 'input/inputs.json') -Destination $InputDirectory.FullName
$InputManifestPath = Join-Path $InputDirectory.FullName 'inputs.json'
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $InputManifestPath).Hash.ToLowerInvariant() -cne 'f7dfc16b4dd9042682572e444171fec271b04b4727a1ec30b2744a6e548d202b') { throw '输入清单字节变化。' }
$InputManifest = Get-Content -Raw -LiteralPath $InputManifestPath | ConvertFrom-Json -AsHashtable
$InputSources = @{
    model_io = Join-Path $ArchiveRoot 'virtual/evidence/protocol_repair_20261020_v1/raw/model_io.jsonl'
    provider_calls = Join-Path $ArchiveRoot 'virtual/evidence/protocol_repair_20261020_v1/raw/provider_calls.jsonl'
    observations = Join-Path $FreshStudy 'analysis/full_metric_audit_v3/audited_observations.jsonl'
    formal_plan = Join-Path $ArchiveRoot 'virtual/evidence/protocol_repair_20261020_v1/manifests/formal_plan.json'
    native_configurations = Join-Path $SupplementAux 'input/native_configurations.json'
    decision_source = Join-Path $SupplementAux 'input/decision_source.py'
    contract_source = Join-Path $SupplementAux 'input/contract_source.py'
    bootstrap_source = Join-Path $SupplementAux 'input/bootstrap_source.py'
    real_driver_source = Join-Path $SupplementAux 'input/real_driver_source.py'
    external_runner_source = Join-Path $SupplementAux 'input/external_runner_source.py'
    recorder_source = Join-Path $SupplementAux 'input/recorder_source.py'
    external_adapter_source = Join-Path $SupplementAux 'input/external_adapter_source.py'
    executor_source = Join-Path $SupplementAux 'input/executor_source.py'
    context_source = Join-Path $SupplementAux 'input/context_source.py'
    gateway_source = Join-Path $SupplementAux 'input/gateway_source.py'
    provider_source = Join-Path $SupplementAux 'input/provider_source.py'
}
foreach ($Entry in $InputManifest.files.GetEnumerator()) {
    $SourceFile = $InputSources[$Entry.Key]
    $Pin = $Entry.Value
    if ((Get-Item -LiteralPath $SourceFile).Length -ne $Pin.bytes) { throw ('输入大小变化：' + $Entry.Key) }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $SourceFile).Hash.ToLowerInvariant() -cne $Pin.sha256) { throw ('输入 SHA 变化：' + $Entry.Key) }
    $DestinationName = [IO.Path]::GetFileName($Pin.path)
    Copy-Item -LiteralPath $SourceFile -Destination (Join-Path $InputDirectory.FullName $DestinationName)
}
$ControlPins = @{
    'recover_original_response_format_v1.py' = '96c0d206ea09ebf7f03849a8e521c692bf148b3fe6cd1133747b4ace32f93754'
    'offline_analysis_runner.py' = '7a82d8b4263b127b938958bc6bca74d8db50f0517e2654e94547bd4c67f77e2d'
    'guard_configuration.json' = 'cd3ac8d7795db70129b247ff002e5f08935fb9b6bc44475af0dee04a4800b3d1'
}
foreach ($Entry in $ControlPins.GetEnumerator()) {
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $ControlDirectory.FullName $Entry.Key)).Hash.ToLowerInvariant() -cne $Entry.Value) { throw ('控制源码变化：' + $Entry.Key) }
}

$ContainerStem = 'response-format-replay-' + [Guid]::NewGuid().ToString('N').Substring(0,12)
foreach ($RunName in @('reference','independent_rebuild')) {
    $RunDirectory = New-Item -ItemType Directory -Path (Join-Path $ReplayRoot $RunName)
    $StartedUtc = [DateTime]::UtcNow.ToString('o')
    $DockerArguments = @(
        'run','--pull','never','--name',($ContainerStem + '-' + $RunName.Replace('_','-')),
        '--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
        '--cpus','2','--memory','2g','--pids-limit','512','--tmpfs','/tmp:rw,noexec,nosuid,size=128m',
        '--env','PYTHONDONTWRITEBYTECODE=1',
        '--mount',('type=bind,source=' + $InputDirectory.FullName.Replace('\','/') + ',target=/input,readonly'),
        '--mount',('type=bind,source=' + $ControlDirectory.FullName.Replace('\','/') + ',target=/control,readonly'),
        '--mount',('type=bind,source=' + $RunDirectory.FullName.Replace('\','/') + ',target=/output'),
        '--entrypoint','/opt/lingxi/formal-venv/bin/python',$ImageId,'-X','utf8','-B',
        '/control/offline_analysis_runner.py','/control/guard_configuration.json'
    )
    & docker @DockerArguments 1> (Join-Path $RunDirectory.FullName 'stdout.log') 2> (Join-Path $RunDirectory.FullName 'stderr.log')
    $ActualExitCode = $LASTEXITCODE
    @{ started_utc=$StartedUtc; finished_utc=[DateTime]::UtcNow.ToString('o'); actual_exit_code=$ActualExitCode; command=$DockerArguments; image=$ImageId } |
        ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $RunDirectory.FullName 'execution_receipt.json') -Encoding utf8
    if ($ActualExitCode -ne 0) { throw '实际执行失败，保留现场并停止。' }
    $GuardResult = Get-Content -Raw -LiteralPath (Join-Path $RunDirectory.FullName 'guard_result.json') | ConvertFrom-Json
    if ($GuardResult.exit_code -ne 0 -or $GuardResult.denied_count -ne 0 -or $GuardResult.status -cne 'OFFLINE_BOUNDARY_COMPLETED') { throw '防护门未通过，保留现场并停止。' }
}
$ExpectedOutputs = @{
    'response_format_flags.csv' = '5f621fb1d70189d9a3d1ee9eb15462c0bbaf68556c79df204bb5f17a6c49d5e4'
    'summary.csv' = '81b50cd5b1482bc5df274b73f3ae77c0efab314f6bf09937ad39a098661ee8f8'
    'results.json' = '13d3ed7304918a2a8ff7ac6482caedc70ab718c34bd7821d5a22581c10a3fbee'
    'append_summary.md' = '33ddcc147699c7340544ca647c0d0edaac9794ad6035afec5cc2f787e6722c0b'
}
foreach ($Entry in $ExpectedOutputs.GetEnumerator()) {
    foreach ($RunName in @('reference','independent_rebuild')) {
        $OutputFile = Join-Path $ReplayRoot ($RunName + '/analysis/' + $Entry.Key)
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $OutputFile).Hash.ToLowerInvariant() -cne $Entry.Value) { throw ('输出不同，保留并审查：' + $RunName + '/' + $Entry.Key) }
    }
}
