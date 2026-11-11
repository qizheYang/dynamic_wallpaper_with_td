# 低资源动态壁纸：可行性核验与性能门槛

核验日期：2026-11-10。结论是：**预渲染视频＋原生播放在功能上可行，本机短测也支持较低的播放器进程开销；包含系统合成、解码服务和 GPU 的整体低占用，尚未完成严格验收。** 不应把“用了硬件解码”直接写成“几乎不消耗资源”。

这次比上一轮增加了三类证据：固定提交的现有程序源码审计、已有程序性能报告的核对、四组本机原生播放实验。没有安装现有壁纸应用，也没有把本探针的结果当作 Aerial / LiveDesk / Mural 的实测排名。

## 1. 现有程序证明了哪些可行机制

完整文件和行号见 [现有程序源码核验](04-existing-apps-performance.md)。

| 参考程序 | 可借鉴机制 | 不能直接接受的推论 |
|---|---|---|
| LiveDesk | 预先转码出 12–30 fps 文件，直接减少后续需要处理的源帧；遮挡暂停 | 共享 AVPlayer 不足以证明多屏只解码一次；pause 不等于释放缓冲 |
| Aerial 4.1 | 共享帧、有限缓存、独立暂停原因；长暂停后释放 reader | 私有原生扩展不等于零合成成本；解码后丢帧没有省掉此前的解码工作 |
| Mural | 简洁的 AVQueuePlayer / AVPlayerLooper 本地视频路径 | 源码中的睡眠状态与遮挡后恢复路径需要补齐，不能只读 README 判断 |

Apple 的现行渲染效率指南优先推荐 AVPlayer；自写低层管线需要用测量证明收益。高帧率增加功耗，背景更新还可能引起相邻透明／Liquid Glass 区域重新计算。因此首版采用原生播放器，不需要再套一层网页、WebGL 或常驻 TouchDesigner。[Apple 渲染效率指南](https://developer.apple.com/documentation/xcode/improving-your-app-s-rendering-efficiency)

两份公开的第一方自测还表明，不能跨机器套用一个数字：

- **Wallper 作者自测**：M4 Mac mini、24 GB、5K2K 屏、4K 视频，报告应用平均 CPU 1.46%、RSS 约 55 MB；WindowServer 播放时 35.7%、退出时 42.2%。这种反向差值说明背景噪声很大，不能证明合成成本为零；Mac mini 测试也不是电池续航测试。[原报告及方法](https://www.wallper.app/blog/2026/07/live-wallpaper-battery-test-mac)
- **LiveWallpaper 作者自测**：M3 Pro、三屏连接且两屏动画、30 fps，报告播放时比基线约多 3.3 W，WindowServer 从 2.50% 到 31.56%；手动暂停后接近基线。作者披露了后台应用、粗粒度电池读数和仅 60 秒采样等限制。这不能预测本机瓦数，却足以反驳“原生视频壁纸必然没有明显系统成本”。[原报告与原始数据入口](https://github.com/DB-AI-Utils/LiveWallpaper#measured)

## 2. 本机实际执行的实验

环境：Apple M5 Max、18 个逻辑 CPU、40 核 GPU、64 GiB 内存；macOS 27.2.1；单块内屏，原生 3472×2266、逻辑 1736×1133、120 Hz。未改变亮度、刷新率、电源设置或原有应用窗口状态。[硬件记录](benchmarks/results/2026-11-10/hardware.json)

使用本项目的 [Swift 探针](benchmarks/WallpaperProbe.swift)：AppKit 桌面层窗口、AVQueuePlayer、AVPlayerLooper、AVPlayerLayer，静音、鼠标穿透、不抢焦点。它不是成品应用；测试结束释放窗口并退出，不修改系统壁纸或登录项。

三段测试素材由 FFmpeg `testsrc2` 生成：720p24、1080p24、1080p30，均为 4 秒 H.264 / yuv420p、无音频。它们用于复现解码与呈现负担，不是 TD 作品，也不代表已通过最终色彩或无缝循环验收。编码命令、帧数与文件哈希保存在 [fixtures.json](benchmarks/results/2026-11-10/fixtures.json)。

前三组各执行 `idle 10s → play 25s → pause 15s → idle 10s`；第四组执行遮挡与恢复。探针每秒记录自身累计 CPU 时间、physical footprint、RSS、媒体时间、循环数和可见性；外部脚本每两秒采集 WindowServer、全部 VTDecoder 服务及全局 GPU 计数。统计剔除阶段边界，CPU 用累计时间差，不使用 `ps %cpu` 的平滑值。[方法与运行说明](benchmarks/README.md)、[汇总脚本](benchmarks/summarize.py)、[原始数据汇总](benchmarks/results/2026-11-10/summary.json)

### 播放器进程结果

CPU 的 100% 表示占满一个逻辑核心。下表为各阶段保留区间的结果，每个档位只测了一轮，**不是完整系统开销**。

| 素材 | 播放 CPU | 播放 footprint | 播放 RSS | 暂停 CPU | 暂停 footprint |
|---|---:|---:|---:|---:|---:|
| 720p24 | 1.31% | 15.28 MiB | 68.54 MiB | 0.05% | 15.05 MiB |
| 1080p24 | 1.53% | 16.19 MiB | 69.35 MiB | 0.06% | 16.07 MiB |
| 1080p30 | 1.74% | 16.38 MiB | 71.31 MiB | 0.06% | 16.17 MiB |

三组都出现 ready 图层、推进的媒体时间、有效播放状态和多次循环，正常结束且没有播放错误。720p24 的保留样本中有 2 次完全遮挡，不能称为全程可见；1080p 两组的保留样本均被 AppKit 判为至少部分可见，但不代表整个桌面完全无遮挡。没有逐帧测量最终屏幕呈现 FPS。

**暂停的 CPU 收益已经观察到，内存释放则没有同样发生。** 1080p24 暂停后 footprint 仍约 16.07 MiB；完全拆掉播放器后的短区间约 14.14 MiB，而创建播放器前约 7.74 MiB。框架、分配器、显示服务可能保留缓存，不能要求瞬时回到初始值，也不能据这次一分钟测试认定有泄漏或没有泄漏。

footprint 与 RSS 是不同口径，不相加。解码服务和共享图面资源可能记在其他进程；这些低数字绝不能表述为“整个动态壁纸只用 16 MB”。Apple 也建议用 footprint 分析内存负担，而非只看虚拟地址或 RSS。[Apple 内存分析](https://developer.apple.com/videos/play/wwdc2022/10106/)

### 遮挡后自动暂停与恢复

1080p24 的第四组启用窗口级遮挡判断，用探针自己的不透明桌面层窗口遮住视频，再移走遮挡。它没有覆盖用户普通应用窗口。

| 阶段 | 可见样本 | 播放状态 | 本进程 CPU |
|---|---:|---|---:|
| 播放 | 9/9 | rate=1、timeControlStatus=playing | 1.32% |
| 遮挡 | 0/9 | rate=0、媒体时间保持不变 | 0.06% |
| 移开遮挡 | 9/9 | rate=1、媒体时间与循环恢复 | 1.53% |

这验证了本机单屏上的暂停／恢复闭环。遮挡窗口本身额外占内存，因此不能用该阶段的 20 MiB footprint 衡量真实其他应用遮挡时的壁纸成本。测试未覆盖多个普通窗口拼接遮挡、所有 Spaces、锁屏或外接屏变化。[该次日志](benchmarks/results/2026-11-10/1080p24-occlusion/probe.jsonl)

### 为什么还没有给 GPU 和整体内存“通过”

1080p24 运行中，短 idle 段 WindowServer CPU 约 46.44%，播放段约 47.99%，暂停段约 44.39%；全局 GPU 在播放段从 35% 到 57% 波动，释放播放器后又出现 55–60%。这不能用简单相减算出可靠的壁纸增量。其他应用保持运行，初始 GPU 就有显著负载，基线也太短。

全部 VTDecoder 服务的 RSS 在同组基线约 205.86 MiB、播放约 245.80 MiB；这是**全系统服务集合**，不是已归属到本探针的内存，也不是 physical footprint。结果说明必须追踪辅助服务，不能只报主进程的小数字。

本轮未测瓦数，也未查询 AVPlayer 内部实际使用的解码器。独立 VideoToolbox 会话能查询硬解选择，但它不能替 AVPlayer 的内部会话作证明。[Apple 硬件解码状态属性](https://developer.apple.com/documentation/videotoolbox/kvtdecompressionpropertykey_usinghardwareacceleratedvideodecoder)

## 3. 以低资源为优先的具体设计

1. **TD 只做生产，播放阶段不运行 TD。** 已完成视频才交给播放端；不在背景持续生成 PNG、转码、运行 Python 每帧控制或采集音频。
2. **默认单屏、720p/1080p、24 fps、SDR、无音频。** TD Non-Commercial 原生输出用 720p；具备相应许可后再制作 1080p。慢速图形可另外提供 15 fps 省电档。30 fps 用于确实需要更流畅的内容；4K、60/120 fps 和多屏动画不作为默认。
3. **低 FPS 必须落实到素材。** 保持同样时长重新采样／渲染；单纯调低 `player.rate` 会改变动画速度，呈现前丢帧则可能仍然解码全部源帧。低码率主要影响压缩数据量，不会自动降低同尺寸同 FPS 的合成成本。
4. **每屏独立暂停原因。** 用户暂停、完全遮挡、约 90% 遮挡、电池、低电量模式、严重热状态、锁屏／睡眠分别记录；解除一种原因不能覆盖其他原因。90% 是拟定策略，需要验证视觉体验和坐标转换。默认在电池与低电量状态使用静态封面。
5. **短暂停保留最后画面，长暂停尝试释放播放管线。** 起步可用 30 秒延迟释放，保留一张封面；恢复时再加载。用内存实测决定是否值得，不能把 `pause()` 或解绑 layer 当作系统解码池立即清空的保证。
6. **检测本身也要轻。** 使用事件触发，必要时统一取得一次所有屏幕的窗口快照；遮挡轮询可先从 2–5 秒开始，设置 timer tolerance。暂停后仍需低频恢复检查，避免“停掉检查后永远不知道桌面露出”的错误。生产版本不常驻每秒性能采样器。

Apple 文档说明，菜单栏 status item 会使应用整体仍被认为可见，所以不能只检查 `NSApplication.occlusionState`；应按壁纸窗口判断。部分可见、透明窗口等仍需额外策略。[Apple 可见性与节能指南](https://developer.apple.com/library/archive/documentation/Performance/Conceptual/power_efficiency_guidelines_osx/WorkWhenVisible.html)

内存大小取决于解码图面和缓冲数量，不能用 MP4 文件大小推算。忽略 stride、对齐和池开销时，NV12 单帧约 `1.5×宽×高` 字节，BGRA 约 `4×宽×高`：

| 分辨率 | NV12 单帧 | BGRA 单帧 |
|---|---:|---:|
| 720p | 1.32 MiB | 3.52 MiB |
| 1080p | 2.97 MiB | 7.91 MiB |
| 4K | 11.87 MiB | 31.64 MiB |

这是理论容量，不是 AVPlayer 实测格式或缓冲数。Apple TN3121 讨论了采集场景中 BGRA 相对 YUV 的内存／带宽开销；比例可用于理解格式成本，不能把该采集结论直接当作播放器的内存配置。[Apple TN3121](https://developer.apple.com/documentation/technotes/tn3121-selecting-a-pixel-format-for-an-avcapturevideodataoutput)

## 4. “不能太吃资源”的初始验收门槛

以下是为本项目提出的目标，**不是已经测得或行业保证的数值**。应在安静、固定条件下至少三轮对照后判定；不达标就降档或切静态。

本节保留研究时的初始预算。后续计划审阅新增了 WindowServer 稳态 footprint 增量 ≤64 MiB 的独立门槛，并将真实锁屏信号验证提前到架构选择前；最新验收条件以 [实施计划](../plan/README.md#8-performance-acceptance) 为准。新增门槛同样是待验证目标，不改变下列实验结果。

| 项目 | 建议门槛 | 当前证据 |
|---|---|---|
| 单屏 1080p24 播放 CPU | 播放器及可归属 helper，加 WindowServer 的平均 CPU 增量 ≤5 个百分点，100%=一个核心 | 主进程低占用已观察；整体未验收 |
| 暂停 CPU | 上述总增量 ≤0.5 个百分点；没有无用逐帧回调 | 主进程约 0.06%；系统总量未验收 |
| 常驻内存 | 播放器及可归属 helper 的 footprint 预算先定 200 MiB；另记录 WindowServer 增量、全系统压力与峰值 | 仅主进程和系统服务 RSS 已采集；不能宣告总预算通过 |
| GPU | 固定测量口径下，可见播放平均增量目标 ≤3 个百分点；暂停回到基线波动范围 | 背景负载太高，未验收；此百分比不能跨芯片比较 |
| 持续运行 | 预热后连续 30 分钟、多次暂停／恢复，footprint 无持续增长；无新增内存压力 | 未执行长测 |
| 电池／睡眠 | 默认静态或暂停；恢复后遵守其他暂停原因，不阻止熄屏 | 电池、锁屏、睡眠实际事件尚未测试 |

正式对照：固定显示器、亮度、刷新率、电源模式、素材和其他应用状态；交替测静态基线／可见播放／完全遮挡／长暂停，预热 60 秒，每组采样 120 秒，至少三轮，再做 30 分钟内存趋势。记录中位数、尾部峰值及方差；如果噪声大于目标差值，结果标为不足，不能强行判定通过。需要功耗结论时使用同机实际功耗测量；Activity Monitor 的 Energy Impact 是相对指标，不能直接写成瓦数。[Apple Energy Impact 说明](https://support.apple.com/en-us/guide/activity-monitor/actmntr43697/mac)

目前推荐继续做一个**受上述预算约束的简洁原生播放器**，或用同一套方法验收选定的现成应用。Aerial 可作为锁屏／系统原生集成候选；以低资源为第一优先时，先验证单屏本地视频和可靠暂停，达到门槛后再增加复杂功能。
