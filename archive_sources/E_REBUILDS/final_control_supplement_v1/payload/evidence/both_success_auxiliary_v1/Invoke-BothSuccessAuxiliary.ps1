#requires -Version 7.0
param(
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
$SupplementAux = Join-Path $ControlSupplementRoot 'evidence/both_success_auxiliary_v1'
$PairIndex = Join-Path $ControlSupplementRoot 'evidence/claims_raw_index_v1/primary_pair_raw_support_index.csv'
$ActualImageId = & docker image inspect --format '{{.Id}}' $ImageId
if ($LASTEXITCODE -ne 0 -or $ActualImageId.Trim() -cne $ImageId) { throw '先按 B8 说明加载并核对同一不可变依赖镜像；不能替换版本。' }

New-Item -ItemType Directory -Path $ReplayRoot | Out-Null
$InputDirectory = New-Item -ItemType Directory -Path (Join-Path $ReplayRoot 'input')
$ControlDirectory = New-Item -ItemType Directory -Path (Join-Path $ReplayRoot 'control')
foreach ($FileName in @('both_success_auxiliary_v1.py','offline_analysis_runner.py','guard_configuration.json')) {
    Copy-Item -LiteralPath (Join-Path $SupplementAux ('control/' + $FileName)) -Destination $ControlDirectory.FullName
}
Copy-Item -LiteralPath (Join-Path $SupplementAux 'input/inputs.json') -Destination $InputDirectory.FullName
$InputManifestPath = Join-Path $InputDirectory.FullName 'inputs.json'
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $InputManifestPath).Hash.ToLowerInvariant() -cne 'db6264cd8b87ef1ec25a9e4181e3a77d5b59e85445798e0c23d4c72a32406e19') { throw '输入清单字节变化。' }
$InputManifest = Get-Content -Raw -LiteralPath $InputManifestPath | ConvertFrom-Json -AsHashtable
$InputSources = @{
    observations = Join-Path $FreshStudy 'analysis/full_metric_audit_v3/audited_observations.jsonl'
    primary = Join-Path $FreshStudy 'analysis/stats/formal_primary_v1.json'
    formal_plan = Join-Path $FreshStudy 'manifests/formal_plan.json'
    claims_pair_index = $PairIndex
    kernel = Join-Path $SupplementAux 'input/formal_statistics.py'
    contract = Join-Path $SupplementAux 'input/statistical_contract.json'
    original_protocol = Join-Path $SupplementAux 'input/original_protocol.md'
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
    'both_success_auxiliary_v1.py' = 'dc3c8e4afb91ab991f3c0467a6801e9971525126f798cf84ea527c3cf514df97'
    'offline_analysis_runner.py' = '7a82d8b4263b127b938958bc6bca74d8db50f0517e2654e94547bd4c67f77e2d'
    'guard_configuration.json' = '8281454ca3e3bc4954337572fcbd1532fa27bdf4521de067ecc8a64d554ce410'
}
foreach ($Entry in $ControlPins.GetEnumerator()) {
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $ControlDirectory.FullName $Entry.Key)).Hash.ToLowerInvariant() -cne $Entry.Value) { throw ('控制源码变化：' + $Entry.Key) }
}

$ContainerStem = 'both-success-replay-' + [Guid]::NewGuid().ToString('N').Substring(0,12)
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
    'results.json' = '9bed3f4d9639521ef766d3220f61e09d9a99870b4e44751395920b9c61ac64fd'
    'summary.csv' = '611d501d394c962e9e8dd1f31c80f6e9c94d91578ffc57ad3d396d61eb2fee7c'
    'append_summary.md' = 'de64430cc3f0450b64e1cda2e83c30f5a913dd65701ac7e579890fd5a7d223e1'
    'feishu.xml' = 'b5e863eef947f535d028ce912cf81d953512d6510f61b539995dab05e9137130'
}
foreach ($Entry in $ExpectedOutputs.GetEnumerator()) {
    foreach ($RunName in @('reference','independent_rebuild')) {
        $OutputFile = Join-Path $ReplayRoot ($RunName + '/analysis/' + $Entry.Key)
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $OutputFile).Hash.ToLowerInvariant() -cne $Entry.Value) { throw ('输出不同，保留并审查：' + $RunName + '/' + $Entry.Key) }
    }
}
