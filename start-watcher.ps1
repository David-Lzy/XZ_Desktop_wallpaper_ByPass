$ErrorActionPreference = 'Stop'

$workingDirectory = Join-Path $PSScriptRoot 'dist'
$executable = Join-Path $workingDirectory 'XZWallpaperBypass.exe'
$sessionId = (Get-Process -Id $PID).SessionId

if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "Executable not found: $executable"
}
while ($true) {
    $xzDesktop = @(Get-CimInstance Win32_Process -Filter "Name='XZDesktop64.exe'" |
        Where-Object { $_.SessionId -eq $sessionId })
    $xzDirectory = (Get-ItemProperty -Path 'HKCU:\Software\XZDesktop' -Name AppPath -ErrorAction SilentlyContinue).AppPath
    $xzLauncher = if ($xzDirectory) { Join-Path $xzDirectory 'XZDesktop.exe' }
    if ($xzDesktop.Count -eq 0 -and $xzLauncher -and (Test-Path -LiteralPath $xzLauncher -PathType Leaf)) {
        Start-Process `
            -FilePath $xzLauncher `
            -ArgumentList '/startup', 'system' `
            -WorkingDirectory $xzDirectory `
            -WindowStyle Hidden
        Start-Sleep -Seconds 10
    }

    $watchers = @(Get-CimInstance Win32_Process -Filter "Name='XZWallpaperBypass.exe'" |
        Where-Object {
            $_.SessionId -eq $sessionId -and
            $_.ExecutablePath -eq $executable -and
            $_.CommandLine -match '\bwatch\b' -and
            $_.CommandLine -match '--desktop-mode\s+hybrid' -and
            $_.CommandLine -match '--wallpaper-refresh\s+external'
        })

    if ($watchers.Count -eq 0) {
        Start-Process `
            -FilePath $executable `
            -ArgumentList @(
                'watch',
                '--interval', '0.25',
                '--desktop-mode', 'hybrid',
                '--wallpaper-refresh', 'external',
                '--startup-delay', '10'
            ) `
            -WorkingDirectory $workingDirectory `
            -WindowStyle Hidden | Out-Null
        Start-Sleep -Milliseconds 750
        $watchers = @(Get-CimInstance Win32_Process -Filter "Name='XZWallpaperBypass.exe'" |
            Where-Object {
                $_.SessionId -eq $sessionId -and
                $_.ExecutablePath -eq $executable -and
                $_.CommandLine -match '\bwatch\b'
            })
    }

    if ($watchers.Count -eq 1) {
        Stop-Process -Id $watchers[0].ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 5
}
