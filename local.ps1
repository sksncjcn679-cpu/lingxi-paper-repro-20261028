param(
    [ValidateSet('start','stop','status','cli','test','smoke')][string]$Action = 'start',
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$RuntimeArgs
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonExe = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
$stateDir = Join-Path $PSScriptRoot '.local'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Missing .venv. See LOCAL_DEPLOYMENT.md for installation.' }
New-Item -ItemType Directory -Force -Path "$stateDir/logs" | Out-Null
$pidFile = Join-Path $stateDir 'server.pid'
switch ($Action) {
    'start' {
        try { $health = Invoke-RestMethod 'http://127.0.0.1:8765/api/health' -TimeoutSec 3 } catch { $health = $null }
        if ($health -and $health.version -eq '1.8.6a1') { Write-Output 'Running: http://127.0.0.1:8765'; break }
        $serverProcess = Start-Process -FilePath $pythonExe -ArgumentList @('-m','uvicorn','deployment.local_server:app','--host','127.0.0.1','--port','8765') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput "$stateDir/logs/server.stdout.log" -RedirectStandardError "$stateDir/logs/server.stderr.log"
        $serverProcess.Id | Set-Content -LiteralPath $pidFile
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            Start-Sleep -Milliseconds 500
            if ($serverProcess.HasExited) { throw "Service exited; see $stateDir/logs/server.stderr.log" }
            try { $health = Invoke-RestMethod 'http://127.0.0.1:8765/api/health' -TimeoutSec 1; break } catch {}
        }
        if (-not $health) { throw 'Service health check timed out; see .local/logs.' }
        Write-Output 'Running: http://127.0.0.1:8765'
    }
    'stop' {
        if (Test-Path -LiteralPath $pidFile) {
            $serverPid = [int](Get-Content -LiteralPath $pidFile)
            $serverInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $serverPid"
            if ($serverInfo -and $serverInfo.CommandLine -like '*deployment.local_server:app*' -and $serverInfo.ExecutablePath -eq $pythonExe) { Stop-Process -Id $serverPid }
            Remove-Item -LiteralPath $pidFile
        }
        Write-Output 'Local service stopped.'
    }
    'status' { Invoke-RestMethod 'http://127.0.0.1:8765/api/health' -TimeoutSec 3 | ConvertTo-Json -Depth 5 }
    'cli' { & $pythonExe 'final-submit/src/scripts/rt.py' @RuntimeArgs; exit $LASTEXITCODE }
    'test' {
        & $pythonExe 'deployment/prepare_validation.py'
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Push-Location -LiteralPath '.local/validation'
        try { & $pythonExe -m pytest tests/unit tests/integration -q @RuntimeArgs; $testCode = $LASTEXITCODE } finally { Pop-Location }
        exit $testCode
    }
    'smoke' {
        & $pythonExe -m deployment.smoke
        exit $LASTEXITCODE
    }
}
