# 实现原理与安全边界

## 组件关系

2.6.4 把“阻止小智写壁纸和切屏整屏重绘”与“让 WDD 管理双屏动态壁纸”拆成两个独立部分：

1. `XZWallpaperBypass.exe` 给小智壁纸模块打内存补丁；默认 `hybrid` 模式只保留 `XZDesktopWnd` 的分区区域。
2. 配套 WDD 分支的内置同步器或 `WDD-SpannedVirtualDesktops.ps1` 在图片更新后生成跨区合成图，并同步全部 Windows 虚拟桌面。

虚拟桌面切换本身不再触发 watcher 调用 WDD。watcher 监视 WDD 脚本在成功应用跨区图后写出的 `state.json`，同时读取 Windows 当前虚拟桌面 ID；首次注入、状态标记变化或虚拟桌面切换时，它将 `state.json.output` 指向的完整合成图直接载入小智的 DuiLib 背景控件。

旧版实验代码曾把 `this+0xD61` 当作 repaint gate。4.1.3.8 中该字节实际属于别的对象状态，写零会触发小智“程序出错了”并退出。2.6.3 不使用这条路径，也不发送同步 `WM_SETTINGCHANGE`。

## 小智内存补丁

目标模块：

```text
XZWallpaperCore64.dll
SHA-256 4CE45DEF7FB9A9C146ABD325493C522F8D2EFC15BD1B5C82BD834CE18A716946

XZDesktopImpl64.dll
SHA-256 23E4D014D79639D5C600D16F93017562421ED537054D7EDB88A1112A9925DB12
```

| 模块 | 功能 | RVA | 原始字节 | 补丁 |
| --- | --- | ---: | --- | --- |
| `XZWallpaperCore64.dll` | `XZ_SetWallpaper` | `0x3270` | 完整函数入口 | `31 C0 C3` |
| `XZWallpaperCore64.dll` | `XZ_SetWallpaperThread` | `0x40A0` | 完整函数入口 | `31 C0 C3` |
| `XZDesktopImpl64.dll` | 虚拟桌面切换后的完整重绘调用 | `0x3695A1` | `E8 1A 39 01 00` | `90 90 90 90 90` |

`31 C0 C3` 返回零值并立即退出。写入前验证进程名、模块名、磁盘 DLL SHA-256、完整入口字节和当前内存状态。混合或未知状态会中止，不做偏移猜测。

补丁只存在于进程内存；小智退出后自然消失，watcher 会在下次启动时重新验证并注入。

### 虚拟桌面切换重绘

切换 Windows 虚拟桌面时，小智并未死锁；`XZDesktop64.exe` 的 UI 线程持续占用一个逻辑核心，并临时分配约 140–310 MB。调用栈落在 `XZDesktopWnd2::HandleCustomMessage`：解析结果为 `0x14/0x15` 后，RVA `0x3695A1` 调用包装器 `0x37CEC0`，最终进入完整虚拟屏幕的 DuiLib/GDI 重绘。

将 `CheckDesktopStatus` 固定返回 false、隐藏桌面层或停用 Explorer 右键扩展均不能消除 CPU 峰值。2.6.3 因此只把上述 5 字节调用替换为 NOP，不修改共享包装器，也不屏蔽 Windows 虚拟桌面 API。watcher 仍可在 `state.json` 更新或桌面切换后调用经过校验的 `SetBkImage` 重载背景。

## 小智背景刷新

小智 4.1.3.8 自己的 `xzdesktopwnd2::SetWallpaper` 会把 WDD 的 7680x2160 跨区图重新生成成 3840x2160 的单屏 `Wallpaper.jpg`，不能用于双屏刷新。2.6.3 调用现有背景控件的 `DuiLib64!CControlUI::SetBkImage`：

```text
XZDesktopImpl64.dll SHA-256
23E4D014D79639D5C600D16F93017562421ED537054D7EDB88A1112A9925DB12

DuiLib64.dll SHA-256
F03187A54BB18CC71369B4A6EB36BCFD6BCD5BA70D7315FB92AA428B64E6515E

CControlUI::SetBkImage RVA 0x6DA70
```

调用前会验证两个完整磁盘哈希、`SetBkImage` 的 16 字节入口、`XZDesktopWnd` userdata、背景控件指针及其 vtable 是否落在受支持的 `XZDesktopImpl64.dll` 映像内。远程线程先设置空字符串再设置合成图路径，确保虚拟桌面切换后即使缓存文件名不变也会重新加载；线程完成后立即释放远程代码和参数页。

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
- watcher 使用 `--wallpaper-refresh external` 后不扫描 WDD 设置、不清空虚拟桌面键、不调用 `/refresh`；只跟踪 `state.json` 和当前虚拟桌面 ID，并触发一次 200ms 去抖的背景重载。
- 默认 `hybrid` 模式把小智窗口 region 限制为分区矩形；分区外由 Explorer 显示跨区壁纸并处理桌面输入。

## StartAllBack 透明任务栏边界

双 4K、150% 缩放实测中，两块显示器物理坐标已对齐为 `Y=0..2160`，工作区均为 `Y=0..2100`，两条任务栏均为 60 像素高。最大化 Codex 窗口的 Win32/DWM 外框为 `Y=-11..2111`，客户端为 `Y=-1..2099`；因此客户端停在任务栏上方 1 像素，但外框进入任务栏区域 11 像素。

StartAllBack 3.9.23 配置为 `TaskbarStyle=Windows 7.msstyles`、`TaskbarAlpha=0`、`TaskbarBlur=0` 时，截图逐像素显示任务栏顶部约 9–10 像素透明，后面的 DWM 外框因此可见。该视觉重叠不由 XZWallpaperBypass、WDD 跨区壁纸或 `hybrid` 窗口 region 产生；不应为此改变注入器窗口逻辑。处理点是 StartAllBack 的任务栏不透明度或模糊设置。

## 框选性能边界

双 4K 实测中，拖动一个很小的桌面选择框即可让小智 UI 线程占满一个逻辑核心。指令采样显示热点位于 `DuiLib64!CPaintManagerUI::MessageHandler`：每个鼠标移动事件都会清空并通过 GDI+ 重绘完整 `7680x2144` 后备缓冲区，而不是选择框附近的脏矩形。

测试过的逐像素循环批量化、跳过透明清零和 16ms 鼠标消息限流均未降低持续拖动时的单核饱和；限流还会直接降低跟手性，因此没有进入正式补丁。默认 `hybrid` 模式让 Explorer 接管分区外的桌面输入，并避免 150% 缩放下错误缩放跨区背景；`xz` 模式可完整保留小智原生行为，但会重新暴露上述背景缩放问题。

## 本机验证

环境：Windows 11 build 26200、WinDynamicDesktop 5.7.0 Portable、小智 4.1.3.8、双 4K 横向排列、150% 缩放，左 Big Sur、右 Catalina。

- 合成图尺寸：`7680x2160`。
- 9 个有效虚拟桌面全部写入同一合成图路径。
- 连续切换 9 个桌面并在 `Switch-Desktop` 返回后立即读取目标桌面：9/9 路径正确，单次 27–153 ms。
- 验证期间 WDD 错误日志时间未变化，WDD 与 watcher 均保持运行。
- 未屏蔽整屏重绘时，小智每个切换阶段消耗约 8–12 CPU 秒并临时增加约 140–310 MB 私有内存。
- 应用 RVA `0x3695A1` 补丁并由 watcher 常驻后，两次往返切换分别消耗 0.39 和 0.48 CPU 秒，私有内存增量均为 0 MB；小智、Explorer 和 DWM 始终可响应。

## 版本更新流程

升级小智后必须重新计算 DLL 哈希、确认目标函数语义和入口字节，并在小智重启、WDD 换图、显示器布局变化及虚拟桌面切换场景复测。不要对未知 DLL 强制沿用旧 RVA。
