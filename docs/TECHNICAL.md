# 实现原理与安全边界

## 组件关系

2.6.0 把“阻止小智写壁纸”和“让 WDD 管理双屏动态壁纸”拆成两个独立部分：

1. `XZWallpaperBypass.exe` 只给小智壁纸模块打内存补丁，并保持 `XZDesktopWnd` 完整可见。
2. `WDD-SpannedVirtualDesktops.ps1` 由 WinDynamicDesktop 在图片更新后调用，生成跨区合成图并同步全部 Windows 虚拟桌面。

虚拟桌面切换本身不再触发任何 watcher 动作。

## 小智内存补丁

目标模块：

```text
XZWallpaperCore64.dll
SHA-256 4CE45DEF7FB9A9C146ABD325493C522F8D2EFC15BD1B5C82BD834CE18A716946
```

| 功能 | RVA | 补丁 |
| --- | ---: | --- |
| `XZ_SetWallpaper` | `0x3270` | `31 C0 C3` |
| `XZ_SetWallpaperThread` | `0x40A0` | `31 C0 C3` |

`31 C0 C3` 返回零值并立即退出。写入前验证进程名、模块名、磁盘 DLL SHA-256、完整入口字节和当前内存状态。混合或未知状态会中止，不做偏移猜测。

补丁只存在于进程内存；小智退出后自然消失，watcher 会在下次启动时重新验证并注入。

## WDD 脚本输入

WDD 官方脚本接口在壁纸更新后通过标准输入传入 JSON，其中 `imagePaths` 是各显示器当前图片，最后一项可包含锁屏图片。WDD 自身按显示器索引调用 `IDesktopWallpaper::SetWallpaper`，脚本保留相同枚举顺序，将前 N 项映射到 N 个活动显示器。

参考：

- [WDD-scripts README](https://github.com/t1m0thyj/WDD-scripts#readme)
- [Installing scripts](https://github.com/t1m0thyj/WinDynamicDesktop/wiki/Installing-scripts)

## 合成算法

PowerShell 的 `Screen.AllScreens` 会受 DPI 虚拟化影响，而且它的枚举顺序不一定与 WDD 相同。脚本改为直接调用 WDD 同样使用的 `IDesktopWallpaper::GetMonitorDevicePathAt(index)` 和 `GetMonitorRECT`。因此图片索引与显示器位置来自同一套 API；两块 4K 显示器在 150% 缩放时仍得到物理坐标 `3840x2160`，而不是逻辑坐标 `2560x1440`。

对每块屏幕：

1. 按 `max(目标宽/原图宽, 目标高/原图高)` 计算居中 Fill 比例；
2. 从源图中心裁剪到目标纵横比；
3. 以显示器物理 `Bounds` 放入整个虚拟屏幕画布；
4. 以 JPEG 95 保存到内容签名路径。

输入路径、文件时间、大小或显示布局改变时签名会变化；相同输入复用缓存。临时文件完成后才原子重命名，最多保留最近 8 张合成图。

## Windows 跨区与虚拟桌面

脚本设置：

```text
HKCU\Control Panel\Desktop\WallpaperStyle = 22
HKCU\Control Panel\Desktop\TileWallpaper = 0
```

随后调用 `VirtualDesktop` 模块的 `Set-AllDesktopWallpapers`，把同一合成图路径写入所有有效虚拟桌面，并用 `SystemParametersInfo(SPI_SETDESKWALLPAPER)` 立即应用当前桌面。

这把原来的二维状态“虚拟桌面 × 物理显示器”降为每个虚拟桌面都支持的一条合成图路径。切换虚拟桌面时 Shell 无需恢复两条逐屏路径，也不需要 WDD `/refresh` 补救。

`VirtualDesktop` 使用非公开 Windows 接口，Windows 大版本更新后可能需要升级模块。脚本首次运行会从 PowerShell Gallery 为当前用户安装该模块，失败会写入 WDD 脚本日志而不会让 WDD 退出。

## 并发和失败边界

- 命名互斥锁防止 WDD 重叠启动两个合成进程；等待上限 30 秒。
- 所有异常均记录到 `scripts\WDD-SpannedVirtualDesktops.log`，不向 WDD 返回非零退出码，避免重复弹出脚本错误框。
- watcher 使用 `--wallpaper-refresh external` 后不扫描 WDD 设置、不清空虚拟桌面键、不调用 `/refresh`。
- 小智窗口保持可见，窗口 region 为 `none`，鼠标拖拽仍由 4.1.3.8 原生实现。

## 本机验证

环境：Windows 11 build 26200、WinDynamicDesktop 5.7.0 Portable、小智 4.1.3.8、双 4K 横向排列、150% 缩放，左 Big Sur、右 Catalina。

- 合成图尺寸：`7680x2160`。
- 9 个有效虚拟桌面全部写入同一合成图路径。
- 连续切换 9 个桌面并在 `Switch-Desktop` 返回后立即读取目标桌面：9/9 路径正确，单次 27–153 ms。
- 验证期间 WDD 错误日志时间未变化，WDD 与 watcher 均保持运行。

## 版本更新流程

升级小智后必须重新计算 DLL 哈希、确认目标函数语义和入口字节，并在小智重启、WDD 换图、显示器布局变化及虚拟桌面切换场景复测。不要对未知 DLL 强制沿用旧 RVA。
