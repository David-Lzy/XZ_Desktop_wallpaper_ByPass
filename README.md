# XZ Desktop Wallpaper ByPass

[English](README_EN.md) | 中文

适用于 **小智桌面 4.1.3.8 x64** 的运行时壁纸兼容工具。它阻止小智的壁纸兼容层覆盖 Windows 壁纸，同时保留新版小智原生的桌面分区、图标、宽度调整、框选和拖拽。

当前版本：**2.6.0**

预编译程序的 SHA-256 见 [SHA256SUMS.txt](SHA256SUMS.txt)。

> 本工具会向正在运行的 `XZDesktop64.exe` 写入经过严格校验的内存补丁。它不修改小智安装目录；目标 DLL 哈希或入口字节不匹配时会拒绝写入。

## 2.6.0 的方案

Windows 11 的每个虚拟桌面只能可靠保存一条壁纸路径，不能可靠持久化“左、右显示器各一张图”。旧方案在切换虚拟桌面后调用 WinDynamicDesktop `/refresh` 补救，既会短暂显示错误壁纸，快速切换时还可能撞上 WDD 的 `EventScheduler` 竞态。

2.6.0 不再在切屏后补救：

1. WDD 每次换图时，把各显示器当前图片通过官方脚本参数 `imagePaths` 交给 [WDD-SpannedVirtualDesktops.ps1](wdd-scripts/WDD-SpannedVirtualDesktops.ps1)。
2. 脚本从 WDD 同样使用的 `IDesktopWallpaper` API 读取显示器索引和物理坐标，分别做居中填充裁剪，再拼成覆盖整个虚拟屏幕的图片。
3. Windows 壁纸布局设为“跨区”，合成图通过 `VirtualDesktop` 模块一次写入所有虚拟桌面。
4. 常驻注入器只负责小智内存补丁，不再检测虚拟桌面切换、调用 `/refresh` 或清除桌面壁纸值。

因此两个物理显示器仍可在 WDD 中选择不同主题，而虚拟桌面切换时使用的是已经准备好的同一张跨区图。

上游资料：[WDD-scripts](https://github.com/t1m0thyj/WDD-scripts#readme)、[Installing scripts](https://github.com/t1m0thyj/WinDynamicDesktop/wiki/Installing-scripts)、[PSVirtualDesktop](https://github.com/MScholtes/PSVirtualDesktop)。

## 兼容性

| 项目 | 支持范围 |
| --- | --- |
| 操作系统 | Windows 11 x64 |
| 小智桌面 | 4.1.3.8 x64 |
| 目标模块 | `XZWallpaperCore64.dll` |
| DLL SHA-256 | `4CE45DEF7FB9A9C146ABD325493C522F8D2EFC15BD1B5C82BD834CE18A716946` |
| WinDynamicDesktop | 可选；已验证 5.7.0 Portable、双 4K、左右不同主题 |

其他小智版本不会绕过哈希和入口字节检查。更新小智后如果工具报告 `mismatch`，请停止使用并重新分析新版本。

## 快速安装

先启动小智桌面和 WinDynamicDesktop，并在 WDD 托盘菜单中开启：

```text
更多选项 -> 启用 PowerShell 脚本
```

然后在仓库目录打开 PowerShell：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
./dist/XZWallpaperBypass.exe status
./install-wdd-span.ps1
./register-startup-task.ps1
```

安装器会从正在运行的 WDD 自动识别目录。WDD 未运行时可显式指定：

```powershell
./install-wdd-span.ps1 -WddDirectory 'D:/GreenApps/WinDynamicDesktop_5.7.0_Portable'
```

WDD 脚本首次运行会为当前用户自动安装 `VirtualDesktop` PowerShell 模块。登录任务 `XZWallpaperBypass-WallpaperOnly` 使用：

```text
XZWallpaperBypass.exe watch --interval 0.25 --desktop-mode xz --wallpaper-refresh external
```

`external` 表示壁纸完全由 WDD 脚本管理，watcher 不会调用 WDD `/refresh`。

## 验证

```powershell
./dist/XZWallpaperBypass.exe --version
./dist/XZWallpaperBypass.exe status
Get-Content "$env:LOCALAPPDATA/XZWallpaperBypass/WDD-Span/state.json"
Get-ScheduledTask -TaskName 'XZWallpaperBypass-WallpaperOnly'
```

正常状态应显示版本 `2.6.0`、进程 `patched`、Desktop layer 为 `visible_xiaozhi`，且 `state.json` 的 `size` 与物理虚拟屏幕一致。双 4K 横向排列时应为 `7680, 2160`。

合成图片和状态位于 `%LOCALAPPDATA%/XZWallpaperBypass/WDD-Span`；脚本日志位于 WDD 的 `scripts/WDD-SpannedVirtualDesktops.log`。

## 恢复与卸载

先从 WDD 的 `scripts` 目录删除 `WDD-SpannedVirtualDesktops.ps1`，再运行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
./Restore-XiaoZhi.ps1
```

最后在 WDD 菜单中执行一次“刷新壁纸”。缓存目录可按需删除。

## 故障排查

### WDD 换图后没有生成合成图

确认 WDD 的“启用 PowerShell 脚本”已勾选，并检查脚本日志。首次安装模块失败时可手动运行：

```powershell
Install-Module VirtualDesktop -Scope CurrentUser -Force
```

### 左右图片顺序错误

脚本使用 WDD 同源的 `IDesktopWallpaper` 显示器索引和物理 `RECT`，不依赖 .NET 的屏幕枚举顺序。显卡驱动更新后可先在 Windows“显示设置”中重新识别显示器，再在 WDD 中重新选择每屏主题并刷新一次。

### `hash mismatch` / `unexpected bytes`

当前小智版本不受支持，或者 DLL 已被其他工具修改。工具不会继续写入，不要关闭校验强行注入。

### 杀毒软件警告

工具使用 `OpenProcess`、`WriteProcessMemory` 和内存保护切换实现运行时补丁，因此启发式扫描可能告警。请从源码自行构建并核对哈希。

## 从源码构建

需要 64 位 Python 3.11 或更高版本：

```powershell
./build.ps1 -Python python
```

构建依赖保存在仓库内 `.build-tools`，输出为 `dist/XZWallpaperBypass.exe`。

## 技术文档

- [实现原理与安全边界](docs/TECHNICAL.md)
- [版本变更记录](CHANGELOG.md)
- [MIT License](LICENSE)
