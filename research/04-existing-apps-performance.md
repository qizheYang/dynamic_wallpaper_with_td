# 现有 macOS 动态壁纸程序：源码与资源成本核验

调研日期：2026-11-10。目标是判断“TD 离线生成视频 → macOS 播放”能否做成低 CPU、GPU、内存的方案。本次直接阅读三个项目的固定提交源码，没有安装或运行这些应用；**源码能证明机制存在，不能证明目标 Mac 上的资源数值或谁最省电**。

结论：这条路线的功能已有现成实现支持；要达到低资源目标，关键是让 TD 退出播放阶段、使用低帧率的本地视频、在不可见时真正停止工作。现有项目已经实现这些部分，但不能完整照搬某一个程序并假定性能问题已经解决。本机探针与性能门槛见 [实测报告](05-performance-feasibility.md)。

## 1. 比较范围

| 项目与核验提交 | 本地视频播放路径 | 已确认的减负机制 | 主要限制 |
| --- | --- | --- | --- |
| [LiveDesk · 64f5d7a](https://github.com/yoshidan/LiveDesk/tree/64f5d7a3fc3528f9abf712eb7cff3b284f3fc70e) | AppKit 桌面窗口、共享 AVPlayer、每屏 AVPlayerLayer | 预转码为 12/15/20/24/30 fps；遮挡时暂停 | 暂停保留播放器；未见电池、热状态和睡眠状态策略；多屏需验证 |
| [Aerial 4.1 · 8b59840](https://github.com/AerialScreensaver/Aerial/tree/8b59840058217aaea73e1b4f6754899c7a4a0093) | 私有原生扩展、AVAssetReader、共享解码帧、AVSampleBufferDisplayLayer | 共享渲染管线；遮挡、电源、热状态策略；延迟释放 reader | 私有 API；多层、多实例及缓冲逻辑复杂；降低呈现帧数不等于降低解码帧数 |
| [Mural · 7df7ae](https://github.com/NoahLaforet/Mural/tree/7df7aef02574063c34cbb7141c5c10a47dd58644) | AppKit 桌面窗口、每屏 AVQueuePlayer + AVPlayerLooper；网页另走 WKWebView | 按屏遮挡检测、电池和低电量模式暂停 | 视频 pause 不释放队列；睡眠与恢复策略存在实现缺口；网页暂停依赖内容配合 |

这些提交分别来自 2026-01-24、2026-11-02、2026-06-30，比较的是上述代码快照，不是所有发行版的统一基准。以下“建议”均为根据源码得出的工程判断。

## 2. LiveDesk：低帧率素材是可借鉴的主思路

**源码事实。** `VideoWindowController.swift` 的 `VideoTranscoder` 使用 `AVAssetExportSession`，通过 `AVMutableVideoComposition.frameDuration = 1 / targetFPS` 生成并缓存新 MP4。它是真正制作低帧率文件，之后正常播放，不是让 60 fps 视频解码后再丢帧。菜单提供 12、15、20、24、30 fps；未设置时默认 Original。项目说明称降低 FPS 可降低 WindowServer CPU，但没有同机、同素材的公开完整对照数据。[转码代码 L375–412](https://github.com/yoshidan/LiveDesk/blob/64f5d7a3fc3528f9abf712eb7cff3b284f3fc70e/LiveDesk/LiveDesk/VideoWindowController.swift#L375)，[菜单代码 L121–145](https://github.com/yoshidan/LiveDesk/blob/64f5d7a3fc3528f9abf712eb7cff3b284f3fc70e/LiveDesk/LiveDesk/AppDelegate.swift#L121)，[作者说明](https://github.com/yoshidan/LiveDesk/blob/64f5d7a3fc3528f9abf712eb7cff3b284f3fc70e/README.md#fps-settings)

**暂停与内存。** 每秒取得窗口列表并计算遮挡区域并集，跨屏总遮挡达到 70% 时暂停共享 player；功能默认关闭。暂停路径只调用 `pause()`，播放器和图层仍在。关闭播放才清除 player、layers 和 windows。因此“暂停”可以减少持续工作，却不能推出内存已释放。[遮挡与暂停代码 L208–347](https://github.com/yoshidan/LiveDesk/blob/64f5d7a3fc3528f9abf712eb7cff3b284f3fc70e/LiveDesk/LiveDesk/VideoWindowController.swift#L208)

**需要避免照搬的部分。**

- 循环使用播放结束通知后 `seek(.zero)`、`play()`，不是预排队循环，短片首尾停顿需实测。
- 多屏复用同一 AVPlayer，属于代码意图；不能据此保证系统只解码一次。遮挡计算把 CGWindow 顶部原点坐标直接与 `NSScreen.frame` 相交，外接屏上下排列需特别检查。
- 1 Hz timer 即使关闭遮挡功能仍创建，只是在回调中跳过计算。读取的主控制器中没有电池、热状态和睡眠暂停状态机。

这些判断依据 [player／窗口代码 L140–204](https://github.com/yoshidan/LiveDesk/blob/64f5d7a3fc3528f9abf712eb7cff3b284f3fc70e/LiveDesk/LiveDesk/VideoWindowController.swift#L140)、[坐标计算 L237–294](https://github.com/yoshidan/LiveDesk/blob/64f5d7a3fc3528f9abf712eb7cff3b284f3fc70e/LiveDesk/LiveDesk/VideoWindowController.swift#L237) 和 [AppDelegate](https://github.com/yoshidan/LiveDesk/blob/64f5d7a3fc3528f9abf712eb7cff3b284f3fc70e/LiveDesk/LiveDesk/AppDelegate.swift)。对本项目最有用的是提前生成低帧率版本；锁屏素材覆盖功能不纳入推荐路线。

## 3. Aerial：暂停、共享和缓冲管理比“原生”标签更重要

**共享管线。** 当前源码支持按屏独立 renderer，或 cloned／spanned 模式共享一个 renderer。reader 请求解压后的 IOSurface-backed sample，设置 `alwaysCopiesSampleData = false`，再分发给多个显示层。这样设计意在避免每个订阅层重复解码；代码注释所称硬件解码及零拷贝收益，仍需通过实际解码路径和 Instruments 核实。[VideoRenderer L1–25、L368–449](https://github.com/AerialScreensaver/Aerial/blob/8b59840058217aaea73e1b4f6754899c7a4a0093/WallpaperExtension/Rendering/VideoRenderer.swift#L368)

**有边界的缓存，不等于低内存保证。** 当前／下一段视频各维护一个 reader，并保留最后一帧。转场 `PresentingRing` 上限 64 个 sample；这是该环的上限，不是应用所有解码 surface、显示层队列和快照的总上限。高分辨率和多实例仍会放大内存。暂停 30 秒后 `enterDeepPause()` 清空 ring，取消并释放两个 reader/output，保留恢复位置。它比单纯 `pause()` 更明确地回收解码管线，但也不能推出所有显示层资源已清空。[PresentingRing L20–61](https://github.com/AerialScreensaver/Aerial/blob/8b59840058217aaea73e1b4f6754899c7a4a0093/WallpaperExtension/Rendering/PresentingRing.swift#L20)，[Deep Pause L1910–1953](https://github.com/AerialScreensaver/Aerial/blob/8b59840058217aaea73e1b4f6754899c7a4a0093/WallpaperExtension/Rendering/VideoRenderer.swift#L1910)

**遮挡检测也有成本。** Companion 用一次窗口快照供全部屏幕使用，每秒采样；已因用户、电池、热状态等原因暂停时，改为每 15 个 tick 检查一次。遮挡面积用 50×50 网格估算，并缓存不变结果。这样避免每屏重复查询，但仍有常驻监测成本；退出 Companion 后，不能默认这些由 Companion 驱动的监控仍持续更新。[WallpaperAutoPauseCoordinator L190–245](https://github.com/AerialScreensaver/Aerial/blob/8b59840058217aaea73e1b4f6754899c7a4a0093/Aerial/Model/Wallpaper/WallpaperAutoPauseCoordinator.swift#L190)，[DesktopOcclusionMonitor L84–175](https://github.com/AerialScreensaver/Aerial/blob/8b59840058217aaea73e1b4f6754899c7a4a0093/Aerial/Model/System/DesktopOcclusionMonitor.swift#L84)

电池、严重热状态、低电量模式会设置独立暂停原因；扩展还区分睡眠策略与用户／遮挡暂停，屏保或锁屏正在展示时可暂缓部分桌面暂停原因。因此应测试每种状态，不能笼统声称“锁屏必定停止”。[PlaybackManager L350–513](https://github.com/AerialScreensaver/Aerial/blob/8b59840058217aaea73e1b4f6754899c7a4a0093/Aerial/Model/Playback/PlaybackManager.swift#L350)，[暂停优先级 L112–126](https://github.com/AerialScreensaver/Aerial/blob/8b59840058217aaea73e1b4f6754899c7a4a0093/WallpaperExtension/Rendering/VideoRenderer.swift#L112)

**关键限制：** `shouldDropForRefresh()` 在解码之后、分发之前丢弃多余帧，能减少后续 enqueue／合成工作，却没有省掉已进行的 HEVC 解码。对于本项目，直接导出 24/30 fps 比先导出 120/240 fps 再期待播放器丢帧更可控。[分发前丢帧 L2652–2661](https://github.com/AerialScreensaver/Aerial/blob/8b59840058217aaea73e1b4f6754899c7a4a0093/WallpaperExtension/Rendering/VideoRenderer.swift#L2652)

## 4. Mural：简单视频链可借鉴，省电逻辑需补齐

视频路径简洁：每屏独立 `AVQueuePlayer + AVPlayerLooper + AVPlayerLayer`；没有网页运行时参与本地视频播放。`pause()` 只暂停 player，`teardown()` 才停止 looper、移除 items、断开 layer。因此多个屏幕、多个 player 的内存成本必须实际统计。[VideoWallpaperRenderer L13–44](https://github.com/NoahLaforet/Mural/blob/7df7aef02574063c34cbb7141c5c10a47dd58644/Sources/Mural/Wallpaper/VideoWallpaperRenderer.swift#L13)

`PlaybackGovernor` 默认在电池、低电量模式／低电量、被单个窗口完全覆盖时暂停；播放时每五秒复查。源码存在两项不能被 README 掩盖的限制：睡眠／锁屏通知只触发 `recompute()`，`desiredState()` 并不读取独立 asleep／locked 状态；所有屏幕都暂停后停止轮询，若仅拖动原应用窗口重新露出桌面而没有触发已订阅通知，存在无法及时恢复的静态风险。应在复用前增加显式状态和恢复路径。[PlaybackGovernor L63–175](https://github.com/NoahLaforet/Mural/blob/7df7aef02574063c34cbb7141c5c10a47dd58644/Sources/Mural/Power/PlaybackGovernor.swift#L63)

网页路径另建 WKWebView。暂停时隐藏 view、暂停媒体并发送 JavaScript 事件；注入脚本只是设置标志和事件，没有通用地取消任意页面的 `requestAnimationFrame`／timer。第三方网页若不配合，不能保证逻辑停止。对于纯 TD 视频交付，引入网页路线会增加不必要的进程与控制范围。[WebWallpaperRenderer L45–120](https://github.com/NoahLaforet/Mural/blob/7df7aef02574063c34cbb7141c5c10a47dd58644/Sources/Mural/Wallpaper/WebWallpaperRenderer.swift#L45)

## 5. 对本项目方案的具体修正

1. **生成与播放彻底分离。** TD、PNG 序列和 FFmpeg 只用于生产；常驻端只接收已完成的视频与封面，播放时关闭 TD、转码器和编辑器预览。
2. **先提供 1080p、24/30 fps 的 SDR 档位。** 这是待比较的起点，不是已证实最优值；慢速抽象动画可另测 15 fps。需要更高清时再升分辨率，避免源帧率远高于实际呈现需求。
3. **使用两个暂停层次。** 短暂停保留画面以便快速恢复；长时间不可见则释放队列／reader，保存时间点。保留资源越少，恢复开销通常越高，应实测折中。
4. **建立明确的每屏状态机。** 用户暂停、睡眠、锁屏、电源、热状态、遮挡分别记录；恢复一个原因不能清除其他暂停原因。遮挡查询统一快照、低频采样，并保证暂停后仍有可靠恢复触发。
5. **先原生视频，暂不加入 WebGL、WKWebView 或常驻 TD。** 需要实时交互时再评估额外管线；“无需交互”的视频作品没有必要长期重算原始场景。

验收应统计播放器、扩展／辅助进程、WindowServer 的整体变化，并分别测量可见、遮挡、长暂停和恢复；单独报主应用 CPU 或 RSS 容易漏掉合成和共享 surface 成本。本次源码审计支持上述方向，尚不能给出“资源一定低于某值”或“三个程序的性能排名”。
