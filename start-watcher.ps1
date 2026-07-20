$ErrorActionPreference = 'Stop'

$workingDirectory = Join-Path $PSScriptRoot 'dist'
$executable = Join-Path $workingDirectory 'XZWallpaperBypass.exe'

if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "Executable not found: $executable"
}

$existing = Get-CimInstance Win32_Process -Filter "Name='XZWallpaperBypass.exe'" |
    Where-Object {
        $_.ExecutablePath -eq $executable -and
        $_.CommandLine -match '\bwatch\b' -and
        $_.CommandLine -match '--desktop-mode\s+xz' -and
        $_.CommandLine -match '--wallpaper-refresh\s+external'
    }

if ($existing) {
    exit 0
}

$process = Start-Process `
    -FilePath $executable `
    -ArgumentList @(
        'watch',
        '--interval', '0.25',
        '--desktop-mode', 'xz',
        '--wallpaper-refresh', 'external'
    ) `
    -WorkingDirectory $workingDirectory `
    -WindowStyle Hidden `
    -PassThru

Start-Sleep -Milliseconds 750
if ($process.HasExited) {
    throw "XZWallpaperBypass watcher exited immediately with code $($process.ExitCode)"
}
