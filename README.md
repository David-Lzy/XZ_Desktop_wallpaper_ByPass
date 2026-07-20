# XZ Desktop Wallpaper ByPass

[English](README_EN.md) | 中文

适用于 **小智桌面 4.1.3.8 x64** 的运行时壁纸兼容工具。它阻止小智的壁纸兼容层覆盖 Windows 壁纸，同时保留新版小智原生的桌面分区、图标、宽度调整、框选和拖拽体验。

当前版本：**2.5.2**

仓库内预编译程序的 SHA-256 见 [SHA256SUMS.txt](SHA256SUMS.txt)。

> 本工具会向正在运行的 `XZDesktop64.exe` 写入经过严格校验的内存补丁。它不会修改小智安装目录，但只应在下面列出的精确版本上使用。目标 DLL 哈希或入口字节不匹配时，工具会拒绝写入。

## 解决的问题

- 小智壁纸兼容层导致黑屏、壁纸不可见或覆盖 WinDynamicDesktop。
- 隐藏 `XZDesktopWnd` 虽然能让 Explorer 绘制壁纸，却会连同小智右侧分区一起隐藏。
- Windows 虚拟桌面保存的是单张 `Wallpaper` 覆盖；在双屏使用不同壁纸时，切换虚拟桌面会先把其中一张图短暂复制到两屏。
- 旧的裁剪/Bypass 方案会干扰新版小智已经优化过的拖拽体验。

2.5.2 的默认模式做三件事：

1. 在内存中阻止小智反向改写系统壁纸。
2. 清除各虚拟桌面的单图覆盖，使所有虚拟桌面继承 Windows 当前的逐显示器壁纸配置。
3. 每 50ms 检测虚拟桌面切换，并立即请求 WinDynamicDesktop 刷新当前桌面。

因此，两个物理显示器仍可分别使用不同主题，而所有虚拟桌面共享这套双屏配置。

## 兼容性

| 项目 | 支持范围 |
| --- | --- |
| 操作系统 | Windows 11 x64 |
| 小智桌面 | 4.1.3.8 x64 |
| 目标进程 | `XZDesktop64.exe` |
| 目标模块 | `XZWallpaperCore64.dll` |
| DLL SHA-256 | `4CE45DEF7FB9A9C146ABD325493C522F8D2EFC15BD1B5C82BD834CE18A716946` |
| WinDynamicDesktop | 可选；已验证 5.7.0 双屏不同主题 |

其他小智版本即使文件名相同，也不会绕过哈希和入口字节检查。更新小智后如果工具报告 `mismatch`，请停止使用并针对新版本重新分析。

## 快速安装

1. 启动小智桌面，并确认版本为 4.1.3.8。
2. 下载或克隆本仓库。
3. 在仓库目录打开 PowerShell：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\dist\XZWallpaperBypass.exe status
.\register-startup-task.ps1
```

安装脚本会创建并立即启动当前用户的登录任务：

```text
XZWallpaperBypass-WallpaperOnly
```

任务以隐藏窗口运行：

```text
XZWallpaperBypass.exe watch --interval 0.05 --desktop-mode xz
```

`xz` 模式保持整个 `XZDesktopWnd` 可见且不设置裁剪区域，所以小智分区和新版原生拖拽都由小智自己管理。

## 验证状态

```powershell
.\dist\XZWallpaperBypass.exe --version
.\dist\XZWallpaperBypass.exe status
Get-ScheduledTask -TaskName 'XZWallpaperBypass-WallpaperOnly'
Get-ScheduledTaskInfo -TaskName 'XZWallpaperBypass-WallpaperOnly'
```

正常状态应显示：

- 工具版本 `2.5.2`；
- 进程状态 `patched`；
- 两个入口均为 `patched`；
- Desktop layer 为 `visible_xiaozhi`；
- Window region 为 `none`；
- 计划任务最近结果为 `0`。

## 恢复与卸载

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\Restore-XiaoZhi.ps1
```

恢复脚本会：

- 停止常驻监视器；
- 恢复小智进程中的原始入口字节；
- 保持小智窗口完整可见；
- 恢复虚拟桌面原来的 `Wallpaper` 值；
- 删除本工具创建的计划任务。

虚拟桌面原值首次修改前会保存到：

```text
%LOCALAPPDATA%\XZWallpaperBypass\virtual-desktop-wallpapers-backup.json
```

## 从源码构建

需要 64 位 Python 3.11 或更高版本。构建依赖安装在仓库内的 `.build-tools`，不会写入全局 Python 环境：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\build.ps1 -Python python
```

输出：

```text
dist\XZWallpaperBypass.exe
```

构建脚本使用 PyInstaller 6.21.0；如果使用 Conda Python，会自动收集存在的 Conda 运行时 DLL，不包含任何开发者本机绝对路径。

## 故障排查

### `XZDesktop64.exe is not running`

先启动小智桌面，再重新运行 `status` 或计划任务。

### `hash mismatch` / `unexpected bytes`

当前小智版本不受支持，或者 DLL 已被其他工具修改。工具不会继续写入。不要关闭校验强行注入。

### 小智以管理员身份运行

本工具必须以相同或更高权限访问目标进程。推荐让小智与本工具都以普通当前用户身份运行。

### WinDynamicDesktop 左右屏主题不同

这是受支持的用法，但 Windows 11 不能为一个虚拟桌面持久保存两块显示器的不同图片。切换时 Shell 可能短暂恢复该桌面的旧缓存；2.5.2 会在检测到切换后立即调用 WinDynamicDesktop `/refresh`。实测错误缓存约持续 58ms，而不是一直保留到手动刷新。

不要在 Windows“个性化”中为某个虚拟桌面单独设置背景，否则 Windows 会重新创建单图覆盖；常驻监视器会再次清除该覆盖。

### 杀毒软件警告

工具使用 `OpenProcess`、`WriteProcessMemory` 和内存保护切换实现运行时补丁，因此启发式扫描可能告警。请从源码自行构建并核对哈希；不要下载来源不明的二进制文件。

## 技术文档

- [实现原理与安全边界](docs/TECHNICAL.md)
- [版本变更记录](CHANGELOG.md)
- [MIT License](LICENSE)
