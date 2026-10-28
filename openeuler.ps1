param(
    [ValidateSet('start','stop','status','cli','test','pilot','analyze','supplements','analyze-supplements','shell')][string]$Action = 'status',
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$RuntimeArgs
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$containerName = 'lingxi-openeuler-2403'
$pythonInContainer = '/opt/lingxi/venv/bin/python'
switch ($Action) {
    'start' { & docker start $containerName; exit $LASTEXITCODE }
    'stop' { & docker stop $containerName; exit $LASTEXITCODE }
    'status' {
        & docker inspect --format 'State={{.State.Status}} CPU_quota={{.HostConfig.NanoCpus}} Memory={{.HostConfig.Memory}}' $containerName
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & docker exec $containerName $pythonInContainer scripts/rt.py version
        & docker exec $containerName cat /etc/os-release
        & docker exec $containerName uname -r
    }
    'cli' { & docker exec $containerName $pythonInContainer scripts/rt.py @RuntimeArgs; exit $LASTEXITCODE }
    'test' { & docker exec $containerName $pythonInContainer /drivers/recheck_openeuler.py; exit $LASTEXITCODE }
    'pilot' {
        # Terminal units are skipped; unresolved API attempts block replay.
        & docker exec $containerName timeout 1800 $pythonInContainer /drivers/pilot_openeuler.py run
        exit $LASTEXITCODE
    }
    'analyze' { & docker exec $containerName $pythonInContainer /drivers/analyze_openeuler_pilot.py; exit $LASTEXITCODE }
    'supplements' {
        Get-Content -LiteralPath (Join-Path $PSScriptRoot 'paper_experiments_20261019_pilot/supplements/summary.json') -Raw
    }
    'analyze-supplements' {
        & docker exec $containerName $pythonInContainer /drivers/finalize_pilot_supplements.py
        exit $LASTEXITCODE
    }
    'shell' { & docker exec -it $containerName bash; exit $LASTEXITCODE }
}
