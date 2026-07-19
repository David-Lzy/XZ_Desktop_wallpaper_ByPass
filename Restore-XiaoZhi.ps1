$ErrorActionPreference = 'Stop'

$taskNames = @(
    'XZWallpaperBypass-WallpaperOnly',
    'XZWallpaperBypass-HybridDesktop',
    'XZWallpaperBypass-SystemDesktop'
)
$executable = Join-Path (Join-Path $PSScriptRoot 'dist') 'XZWallpaperBypass.exe'
$resolvedExecutable = [System.IO.Path]::GetFullPath($executable)

$tasks = foreach ($taskName in $taskNames) {
    Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
}
foreach ($task in $tasks) {
    Stop-ScheduledTask -TaskName $task.TaskName -ErrorAction SilentlyContinue
}

Start-Sleep -Milliseconds 500
$watchers = Get-CimInstance Win32_Process -Filter "Name='XZWallpaperBypass.exe'" |
    Where-Object {
        $_.ExecutablePath -and
        [System.IO.Path]::GetFullPath($_.ExecutablePath) -eq $resolvedExecutable -and
        $_.CommandLine -match '(?i)\bwatch\b'
    }
foreach ($watcher in $watchers) {
    Stop-Process -Id ([int]$watcher.ProcessId) -Force -ErrorAction SilentlyContinue
}

& $executable restore
if ($LASTEXITCODE -ne 0) {
    throw "XZWallpaperBypass restore exited with code $LASTEXITCODE"
}

foreach ($task in $tasks) {
    Unregister-ScheduledTask -TaskName $task.TaskName -Confirm:$false
}

Write-Output 'XiaoZhi desktop layer and original in-memory code were restored.'
