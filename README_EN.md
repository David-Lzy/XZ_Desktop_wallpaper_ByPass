# XZ Desktop Wallpaper ByPass

[中文说明](README.md) | English

Runtime wallpaper compatibility tool for **XZ Desktop 4.1.3.8 x64** on Windows 11.

Version **2.6.3** blocks XZ Desktop's wallpaper compatibility layer in memory while keeping the complete native `XZDesktopWnd` visible. It also suppresses the single full-virtual-screen redraw that XZ Desktop requests after virtual-desktop results `0x14/0x15`. Windows virtual desktops, desktop switching, and the watcher's targeted background reload remain enabled.

For different themes on two physical monitors, a WinDynamicDesktop post-update script combines WDD's per-display `imagePaths` into one native-resolution virtual-screen image. Windows uses the **Span** layout, and `Set-AllDesktopWallpapers` assigns the composite to every virtual desktop before the user switches. The resident watcher never calls WDD `/refresh`; after startup, a completed WDD update, or a virtual-desktop switch it loads the full composite directly into XZ Desktop's verified DuiLib background control. This avoids XZ 4.1.3.8 collapsing the span into a one-monitor cache.

## Supported target

- Windows 11 x64
- XZ Desktop 4.1.3.8 x64
- `XZWallpaperCore64.dll`
- SHA-256 `4CE45DEF7FB9A9C146ABD325493C522F8D2EFC15BD1B5C82BD834CE18A716946`
- `XZDesktopImpl64.dll`
- SHA-256 `23E4D014D79639D5C600D16F93017562421ED537054D7EDB88A1112A9925DB12`
- WinDynamicDesktop 5.7.0 tested with two different 4K themes

Unknown DLL hashes or entry bytes are rejected.

On dual 4K desktops, XZ Desktop also used to repaint the entire virtual screen on its UI thread after every virtual-desktop switch. The verified call-site patch reduces measured XZ CPU time per switch from about 8–12 seconds to 0.39–0.48 seconds and removes the temporary 140–310 MB private-memory spike. Native selection can still consume one full CPU core because DuiLib clears and repaints the entire 7680x2144 backing surface for every mouse move; the default mode deliberately does not throttle mouse input.

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

`status` should report version `2.6.3`, process state `patched`, and `Block virtual-desktop full redraw: patched`.

## Restore

Remove `WDD-SpannedVirtualDesktops.ps1` from WDD's `scripts` directory, run `Restore-XiaoZhi.ps1`, and refresh WDD once.

See the [Chinese README](README.md), [technical documentation](docs/TECHNICAL.md), and [changelog](CHANGELOG.md) for implementation details and validation evidence.
