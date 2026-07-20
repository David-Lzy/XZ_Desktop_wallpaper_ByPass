[CmdletBinding()]
param(
    [string]$WddDirectory
)

$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot 'wdd-scripts\WDD-SpannedVirtualDesktops.ps1'
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw "WDD script not found: $source"
}

if ([string]::IsNullOrWhiteSpace($WddDirectory)) {
    $running = Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -like 'WinDynamicDesktop*.exe' -and
            -not [string]::IsNullOrWhiteSpace($_.ExecutablePath)
        } |
        Select-Object -First 1
    if ($null -eq $running) {
        throw 'WinDynamicDesktop is not running. Start it or pass -WddDirectory explicitly.'
    }
    $WddDirectory = Split-Path -Parent $running.ExecutablePath
}

$resolvedDirectory = (Resolve-Path -LiteralPath $WddDirectory).Path
$wddExecutable = Get-ChildItem -LiteralPath $resolvedDirectory -Filter 'WinDynamicDesktop*.exe' -File |
    Where-Object { $_.Name -notlike '*Update*' } |
    Select-Object -First 1
if ($null -eq $wddExecutable) {
    throw "WinDynamicDesktop executable not found in: $resolvedDirectory"
}

$scriptsDirectory = Join-Path $resolvedDirectory 'scripts'
New-Item -ItemType Directory -Path $scriptsDirectory -Force | Out-Null
$destination = Join-Path $scriptsDirectory 'WDD-SpannedVirtualDesktops.ps1'
Copy-Item -LiteralPath $source -Destination $destination -Force

$scriptsEnabled = $false
$settingsPath = Join-Path $resolvedDirectory 'settings.json'
if (Test-Path -LiteralPath $settingsPath -PathType Leaf) {
    try {
        $settings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
        $scriptsEnabled = $settings.enableScripts -eq $true
    }
    catch {
        Write-Warning "Could not read WDD settings: $settingsPath"
    }
}

if ($scriptsEnabled) {
    & $wddExecutable.FullName /refresh
    Write-Output 'Installed and requested one WDD wallpaper refresh.'
}
else {
    Write-Warning 'Enable "More Options -> Enable PowerShell scripts" in the WDD tray menu, then refresh wallpaper once.'
}

[PSCustomObject]@{
    WddDirectory = $resolvedDirectory
    Script = $destination
    ScriptsEnabled = $scriptsEnabled
}
