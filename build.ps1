param(
    [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'

$pythonCommand = Get-Command $Python -ErrorAction Stop
$buildTools = Join-Path $PSScriptRoot '.build-tools'
$spec = Join-Path $PSScriptRoot 'XZWallpaperBypass.spec'
$dist = Join-Path $PSScriptRoot 'dist'
$work = Join-Path $PSScriptRoot 'build'
$previousPythonPath = $env:PYTHONPATH

try {
    & $pythonCommand.Source -m pip install `
        --disable-pip-version-check `
        --target $buildTools `
        'pyinstaller==6.21.0'
    if ($LASTEXITCODE -ne 0) {
        throw "Installing the isolated build dependency failed with code $LASTEXITCODE"
    }

    if ([string]::IsNullOrWhiteSpace($previousPythonPath)) {
        $env:PYTHONPATH = $buildTools
    } else {
        $env:PYTHONPATH = "$buildTools;$previousPythonPath"
    }

    & $pythonCommand.Source -m PyInstaller `
        --clean `
        --noconfirm `
        --distpath $dist `
        --workpath $work `
        $spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with code $LASTEXITCODE"
    }
} finally {
    $env:PYTHONPATH = $previousPythonPath
}

$executable = Join-Path $dist 'XZWallpaperBypass.exe'
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "Build did not produce $executable"
}

& $executable --version
Get-FileHash -Algorithm SHA256 -LiteralPath $executable | Select-Object Hash, Path
