# Changelog

## Unreleased

- 登录任务持续监督当前会话中的小智和 watcher，分别在缺失时尝试恢复；小智安装目录从当前用户注册表读取。
- 说明分区位置由小智保存，显示器布局变化后可能需要重新移动分区。

## 2.6.4 - 2026-07-25

- Explorer/StartAllBack 重启后，watcher 在安全等待重新注入期间先隐藏小智背景层，避免旧壁纸覆盖已经正确恢复的 Windows 跨区壁纸。
- 常驻任务改用 `hybrid` 桌面层，只保留小智分区区域并露出 Windows 跨区壁纸，避免 150% 缩放下把 7680 合成图压缩进左屏。
- 记录 StartAllBack 完全透明任务栏会露出最大化窗口 DWM 外框的独立视觉问题；该现象不遮挡客户端内容，也不属于小智壁纸或 `hybrid` region 故障。

## 2.6.3 - 2026-07-25

- 修复双 4K 环境切换 Windows 虚拟桌面后，小智桌面背景和桌面层冻结约 8–12 秒的问题。
- 定位根因为 `XZDesktopWnd2::HandleCustomMessage` 处理结果 `0x14/0x15` 时调用完整虚拟屏幕重绘；`CheckDesktopStatus` 查询本身和 Explorer 右键扩展均不是瓶颈。
- 对受支持的 `XZDesktopImpl64.dll` 在 RVA `0x3695A1` 的 5 字节 `CALL` 做严格哈希和原始指令校验后替换为 NOP；不禁用 Windows 虚拟桌面，也不影响 watcher 的按需背景重载。
- 小智重启后 watcher 会自动重新注入。实测切换阶段小智 CPU 从约 8–12 秒降至 0.39–0.48 秒，临时私有内存增量从约 140–310 MB 降至 0 MB。

## 2.6.2 - 2026-07-20

- 删除会在小智 4.1.3.8 上破坏对象内存的旧 `this+0xD61` repaint gate 写入；不再发送同步 `WM_SETTINGCHANGE`。
- 新增严格校验的 `DuiLib64!CControlUI::SetBkImage` 调用，直接把 WDD 的 7680x2160 合成图交给小智背景控件，避免其 `SetWallpaper` 退化成 3840x2160 单屏缓存。
- watcher 在首次注入、WDD `state.json` 更新和 Windows 虚拟桌面切换后重载合成图；全程不调用 WDD `/refresh`。
- 冷启动不再等待或强制刷新 WDD；已有 `state.json` 时可直接恢复背景，之后的 WDD 更新会再次覆盖。
- 实测定位拖框满核为 DuiLib 每帧清空并用 GDI+ 重绘完整 `7680x2144` 缓冲区；未保留无收益的循环/限帧实验补丁，避免以降低跟手性换取表面 CPU 数字。

## 2.6.1 - 2026-07-20

- 修复冷启动时小智先创建不透明桌面层、WDD 后应用跨区图，导致桌面长期保持纯黑的问题。
- watcher 在首次注入时主动重绘，并监视 WDD `state.json`；每次成功合成后以 200ms 防抖刷新背景控件。
- 4.1.3.8 明确跳过已停用的旧版逐显示器内部钩子，状态显示为 `disabled_4.1.3.8`。
- 保持 `external` 模式不调用 WDD `/refresh`，不恢复虚拟桌面切换轮询。

## 2.6.0 - 2026-07-20

- 用 WDD PowerShell 脚本把每块显示器的当前图片按物理像素合成为一张跨区壁纸。
- 通过 `VirtualDesktop` 的 `Set-AllDesktopWallpapers` 在换图时主动更新全部虚拟桌面，切屏后不再补调 WDD `/refresh`。
- 直接使用 WDD 同源的 `IDesktopWallpaper` 索引和物理 `RECT`；双 4K、150% 缩放仍生成原生 `7680x2160` 合成图，并避免左右枚举顺序猜测。
- watcher 新增 `--wallpaper-refresh external`，该模式不调用 WDD、不清除虚拟桌面壁纸值。
- 新增互斥锁、内容签名缓存、原子写入、错误日志和自动安装 `VirtualDesktop` 模块。
- 新增 `install-wdd-span.ps1` 和完整安装文档。
- 在 9 个有效虚拟桌面间连续切换，9/9 在切换返回时已指向正确合成图；WDD 错误日志无新增。

## 2.5.3 - 2026-07-20

- 修复 2.5.2 连续切屏时重叠 `/refresh` 可能导致 WinDynamicDesktop 5.7.0 `EventScheduler.Run` 崩溃的问题。
- 增加 120ms 防抖和 2 秒刷新冷却，连续切屏只处理最后状态。
- 增加 5 秒失败退避；WinDynamicDesktop 暂时退出或刷新失败时 watcher 不再退出。
- 压力测试以 250ms 间隔连续切换四次，WinDynamicDesktop 与 watcher 均保持运行且日志没有新增异常。

## 2.5.2 - 2026-07-20

- 修复 WinDynamicDesktop 只更新当前虚拟桌面后，切到其他桌面会长期恢复旧壁纸的问题。
- 恢复虚拟桌面切换后的 WinDynamicDesktop `/refresh`，并把检测间隔从 1 秒降到 50ms。
- 保留 2.5.1 的虚拟桌面单图覆盖清理与可回滚备份。
- 去掉旧版刷新后的固定 1 秒等待。
- 双屏不同主题实测中，旧 Shell 缓存从持续到手动刷新缩短为约 58ms。

## 2.5.1 - 2026-07-19

- 支持小智桌面 4.1.3.8 x64 的精确 DLL 哈希和入口字节校验。
- 默认使用 wallpaper-only `xz` 模式，完整保留 `XZDesktopWnd` 与新版原生拖拽。
- 阻止 `XZWallpaperCore64.dll` 的两个壁纸写入入口。
- 清除并持续抑制各虚拟桌面的单图 `Wallpaper` 覆盖，使所有虚拟桌面继承当前双屏逐显示器壁纸。
- 增加虚拟桌面壁纸记录备份与恢复。
- 普通虚拟桌面切换不再调用 WinDynamicDesktop `/refresh`，避免额外重绘。
- 登录任务支持小智重启后自动重新注入。
- 增加可移植 PyInstaller 构建脚本、中文/英文使用说明与技术文档。

## 安全策略

- 不修改小智安装目录。
- 未知 DLL 哈希、未知入口字节或混合补丁状态全部拒绝写入。
- 安装任务以当前交互用户和 Limited 权限运行。
