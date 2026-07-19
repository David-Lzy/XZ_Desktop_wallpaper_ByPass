# 实现原理与安全边界

## 目标

本项目解决两个相互关联的问题：

1. 小智桌面的壁纸兼容代码会覆盖 Windows/WinDynamicDesktop 管理的壁纸。
2. Windows 为虚拟桌面保存单张壁纸覆盖，与“双屏分别使用不同图片”的配置冲突，切换时会产生短暂错误画面。

默认实现不隐藏、裁剪或替换 `XZDesktopWnd`。小智 4.1.3.8 继续负责桌面分区与全部鼠标交互；Windows/WinDynamicDesktop 负责壁纸路径。

## 内存补丁

目标模块：

```text
XZWallpaperCore64.dll
SHA-256 4CE45DEF7FB9A9C146ABD325493C522F8D2EFC15BD1B5C82BD834CE18A716946
```

工具只处理以下入口：

| 功能 | RVA | 补丁 |
| --- | ---: | --- |
| `XZ_SetWallpaper` | `0x3270` | `31 C0 C3` |
| `XZ_SetWallpaperThread` | `0x40A0` | `31 C0 C3` |

`31 C0 C3` 等价于返回成功/零值并立即退出。写入前会同时验证：

- 目标进程名；
- 目标模块名；
- 磁盘 DLL 的完整 SHA-256；
- 每个入口的原始机器码；
- 当前内存必须处于完整原始状态或本工具的完整补丁状态。

任何混合或未知状态都会中止，不做猜测式偏移扫描。

补丁只存在于进程内存。关闭小智进程会自然清除；`restore` 会在进程仍运行时写回原始字节。

## 虚拟桌面继承

Windows 虚拟桌面记录位于当前用户注册表：

```text
HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\VirtualDesktops\Desktops\{GUID}
```

当子键包含 `Wallpaper` 时，切换到该桌面会把这张单图应用到壁纸系统。对于左右屏不同图片的配置，这会暂时把同一张图应用到两屏。

2.5.1 在首次修改前备份每个桌面是否存在 `Wallpaper`、原路径和注册表类型，然后删除这些值。没有单桌面覆盖时，所有虚拟桌面继承当前 `IDesktopWallpaper` 的逐显示器路径。

监视器每两秒检查一次新建或重新写入的覆盖，但不会因为普通虚拟桌面切换而调用 WinDynamicDesktop `/refresh`，从而避免不必要的重绘。

## 实测切换时序

修复前，在“左 Big Sur、右 Catalina”的双屏配置下自动切换虚拟桌面，高频读取 `IDesktopWallpaper` 得到：

| 切换后时间 | 左屏 | 右屏 |
| ---: | --- | --- |
| 约 620 ms | Big Sur | Big Sur |
| 约 650 ms | Big Sur | Big Sur |
| 约 962 ms | Big Sur | Catalina |

清除虚拟桌面 `Wallpaper` 覆盖后，用相同方法切换并切回，两个显示器的路径全程没有发生变化。

## 常驻监视器

`watch --interval 1.0 --desktop-mode xz` 执行以下工作：

- 每秒低频检查小智进程；
- 小智重启或 DLL 重新加载后再次执行全部校验和内存补丁；
- 每 30 秒重新验证补丁状态；
- 每两秒确保虚拟桌面继续继承逐显示器配置；
- WinDynamicDesktop 启动或主题设置文件变化时请求刷新；
- 普通虚拟桌面切换不触发刷新。

## 桌面窗口策略

默认 `xz` 模式：

- `XZDesktopWnd` 保持可见；
- 窗口 region 为 `none`；
- 不隐藏右侧整理分区；
- 不接管鼠标拖拽；
- 不使用旧版按区域裁剪的 Hybrid 绕行方案。

源码中仍保留 `hybrid` 和 `system` 参数，主要用于诊断和回滚旧部署；登录任务只使用 `xz`。

## 回滚数据

备份文件：

```text
%LOCALAPPDATA%\XZWallpaperBypass\virtual-desktop-wallpapers-backup.json
```

备份采用增量保护：监视器首次看到新虚拟桌面时，会先保存其当前状态，再清除覆盖。恢复时只处理仍存在的桌面键，不会重新创建已被用户删除的虚拟桌面。

## 版本更新流程

升级小智后应先停止监视器并运行恢复脚本。新版本支持必须重新完成：

1. 计算目标 DLL SHA-256；
2. 确认目标函数语义；
3. 记录并校验完整入口字节；
4. 在双屏、虚拟桌面切换和小智进程重启场景验证；
5. 更新版本说明与二进制。

不要仅根据旧 RVA 对未知 DLL 强制打补丁。
