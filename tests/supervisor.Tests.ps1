$scriptPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'start-watcher.ps1'

Describe 'Startup supervisor' {
    BeforeEach {
        $session = (Get-Process -Id $PID).SessionId
        $watcherPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'dist\XZWallpaperBypass.exe'
        $launcherPath = Join-Path $TestDrive 'XZDesktop.exe'
        New-Item -ItemType File -Path $launcherPath -Force | Out-Null
        Mock Get-ItemProperty { [pscustomobject]@{ AppPath = $TestDrive } }
        Mock Start-Process { }
        Mock Stop-Process { }
        Mock Start-Sleep { if ($Seconds -eq 5) { throw 'end test iteration' } }
    }

    It 'restarts a missing XiaoZhi in the current session' {
        Mock Get-CimInstance {
            if ($Filter -like '*XZWallpaperBypass*') {
                1..2 | ForEach-Object { [pscustomobject]@{ SessionId = $session; ExecutablePath = $watcherPath; CommandLine = 'watch --desktop-mode hybrid --wallpaper-refresh external' } }
            }
        }
        { & $scriptPath } | Should Throw 'end test iteration'
        Assert-MockCalled Start-Process -Scope It -Times 1 -Exactly -ParameterFilter { $FilePath -eq $launcherPath }
    }

    It 'restarts a missing watcher without restarting XiaoZhi' {
        Mock Get-CimInstance {
            if ($Filter -like '*XZDesktop64*') { [pscustomobject]@{ SessionId = $session } }
        }
        { & $scriptPath } | Should Throw 'end test iteration'
        Assert-MockCalled Start-Process -Scope It -Times 1 -Exactly -ParameterFilter { $FilePath -eq $watcherPath }
    }

    It 'keeps the watcher running if the XiaoZhi installation is unknown' {
        Mock Get-ItemProperty { $null }
        Mock Get-CimInstance {
            if ($Filter -like '*XZWallpaperBypass*') {
                1..2 | ForEach-Object { [pscustomobject]@{ SessionId = $session; ExecutablePath = $watcherPath; CommandLine = 'watch --desktop-mode hybrid --wallpaper-refresh external' } }
            }
        }
        { & $scriptPath } | Should Throw 'end test iteration'
        Assert-MockCalled Start-Process -Scope It -Times 0 -Exactly
    }
}
