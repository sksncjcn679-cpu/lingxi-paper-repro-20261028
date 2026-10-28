# Read-only collection. Administrative rights are needed to read Hyper-V event channels.
$ErrorActionPreference = 'Stop'
$workspaceRoot = Split-Path -Parent $PSScriptRoot
$outputDirectory = Join-Path $workspaceRoot '.local/wsl-repair/admin'
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$collectionErrors = @()
$channels = @(
    'Microsoft-Windows-Hyper-V-Compute-Admin',
    'Microsoft-Windows-Hyper-V-Compute-Operational',
    'Microsoft-Windows-Hyper-V-Hypervisor-Admin',
    'Microsoft-Windows-Hyper-V-Hypervisor-Operational',
    'Microsoft-Windows-Hyper-V-Worker-Admin',
    'Microsoft-Windows-Resource-Exhaustion-Detector/Operational'
)
foreach ($channel in $channels) {
    try {
        $events = @(Get-WinEvent -FilterHashtable @{LogName=$channel;StartTime=(Get-Date).AddHours(-3)} -ErrorAction Stop)
        $events | Select-Object TimeCreated,Id,LevelDisplayName,ProviderName,Message | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $outputDirectory ($channel.Replace('/','_')+'.json'))
    } catch {
        $collectionErrors += @{channel=$channel;error=$_.Exception.Message}
    }
}
& bcdedit.exe /enum all 2>&1 | Out-File -Encoding UTF8 -LiteralPath (Join-Path $outputDirectory 'boot-configuration.txt')
Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,TotalVisibleMemorySize,FreePhysicalMemory,TotalVirtualMemorySize,FreeVirtualMemory | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $outputDirectory 'memory.json')
Get-CimInstance Win32_PerfFormattedData_HvStats_HyperVHypervisor | Select-Object LogicalProcessors,VirtualProcessors,Partitions,TotalPages | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $outputDirectory 'hypervisor.json')
@{is_admin=$isAdmin;completed_at=(Get-Date).ToString('o');errors=$collectionErrors} | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $outputDirectory 'completed.json')
