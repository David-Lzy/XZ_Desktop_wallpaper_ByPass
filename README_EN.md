# XZ Desktop Wallpaper ByPass

[中文说明](README.md) | English

Runtime wallpaper compatibility tool for **XZ Desktop 4.1.3.8 x64** on Windows 11.

Version **2.6.0** blocks XZ Desktop's wallpaper compatibility layer in memory while keeping the complete native `XZDesktopWnd` visible. Desktop groups, resizing, selection, and dragging remain managed by XZ Desktop.

For different themes on two physical monitors, a WinDynamicDesktop post-update script combines WDD's per-display `imagePaths` into one native-resolution virtual-screen image. Windows uses the **Span** layout, and `Set-AllDesktopWallpapers` assigns the composite to every virtual desktop before the user switches. The resident watcher no longer calls WDD `/refresh` on virtual-desktop changes.

## Supported target

- Windows 11 x64
- XZ Desktop 4.1.3.8 x64
- `XZWallpaperCore64.dll`
- SHA-256 `4CE45DEF7FB9A9C146ABD325493C522F8D2EFC15BD1B5C82BD834CE18A716946`
- WinDynamicDesktop 5.7.0 tested with two different 4K themes

Unknown DLL hashes or entry bytes are rejected.

## Install

Enable **More Options -> Enable PowerShell scripts** in WDD, then run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
./dist/XZWallpaperBypass.exe status
./install-wdd-span.ps1
./register-startup-task.ps1
```

If WDD is not running, pass its directory explicitly:

```powershell
./install-wdd-span.ps1 -WddDirectory 'D:/Apps/WinDynamicDesktop'
```

The WDD script installs the `VirtualDesktop` PowerShell module for the current user on first use. The startup task runs:

```text
XZWallpaperBypass.exe watch --interval 0.25 --desktop-mode xz --wallpaper-refresh external
```

Generated composites and state are under `%LOCALAPPDATA%/XZWallpaperBypass/WDD-Span`.

## Restore

Remove `WDD-SpannedVirtualDesktops.ps1` from WDD's `scripts` directory, run `Restore-XiaoZhi.ps1`, and refresh WDD once.

See the [Chinese README](README.md), [technical documentation](docs/TECHNICAL.md), and [changelog](CHANGELOG.md) for implementation details and validation evidence.
