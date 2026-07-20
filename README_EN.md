# XZ Desktop Wallpaper ByPass

[中文说明](README.md) | English

Runtime wallpaper compatibility tool for **XZ Desktop 4.1.3.8 x64** on Windows 11.

Version 2.5.2 blocks XZ Desktop's wallpaper compatibility layer in memory while keeping the complete native `XZDesktopWnd` visible. Desktop groups, resizing, selection and dragging therefore remain managed by the current XZ Desktop release.

It also removes the single-picture overrides stored for individual Windows virtual desktops. Because Windows can still restore an in-memory wallpaper cache when switching desktops, version 2.5.2 detects switches every 50 ms and immediately requests a WinDynamicDesktop refresh. In the validated dual-monitor setup, the stale cache remained visible for about 58 ms instead of persisting until a manual refresh.

## Supported target

- Process: `XZDesktop64.exe`
- Module: `XZWallpaperCore64.dll`
- SHA-256: `4CE45DEF7FB9A9C146ABD325493C522F8D2EFC15BD1B5C82BD834CE18A716946`
- Tested XZ Desktop version: 4.1.3.8 x64
- Tested OS: Windows 11 x64

The tool refuses to patch an unknown module hash or unexpected entry bytes.

## Install

Open PowerShell in the repository directory:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\dist\XZWallpaperBypass.exe status
.\register-startup-task.ps1
```

The installer registers and immediately starts the per-user task `XZWallpaperBypass-WallpaperOnly`.

## Restore

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\Restore-XiaoZhi.ps1
```

This stops the watcher, restores the original in-memory bytes and virtual-desktop wallpaper records, and removes the scheduled task.

## Build

Use a 64-bit Python 3.11+ installation:

```powershell
.\build.ps1 -Python python
```

The isolated build dependency is stored under `.build-tools`; the executable is written to `dist\XZWallpaperBypass.exe`.

See [the Chinese README](README.md) and [technical documentation](docs/TECHNICAL.md) for compatibility details, validation evidence and troubleshooting.
