# 原生壁纸播放探针

`WallpaperProbe.swift` 是一次性 AppKit / AVFoundation 测量程序，用于验证“预渲染视频 + 原生桌面播放器”的资源占用。它不是成品壁纸应用，也不改系统壁纸或登录项。源码经本机 Swift 编译检查；实际 GUI 运行结果以对应 JSONL 为准。

## 编译与运行

从项目根目录编译；模块缓存放在临时目录：

```sh
mkdir -p research/benchmarks/build
xcrun swiftc -O \
  -module-cache-path /private/tmp/wallpaper-probe-swift-cache \
  research/benchmarks/WallpaperProbe.swift \
  -o research/benchmarks/build/WallpaperProbe \
  -framework AppKit -framework AVFoundation -framework CoreGraphics
research/benchmarks/build/WallpaperProbe --help
```

下面会运行一个最长 60 秒的 GUI 探针，仅在登录的 macOS 图形会话中执行；自动代理应先按环境要求取得 GUI 启动权限。视频必须是本地文件，输出目录须已存在，输出 JSONL 须不存在，以免覆盖测量记录。

```sh
research/benchmarks/build/WallpaperProbe \
  --video /absolute/path/to/loop.mp4 \
  --output /absolute/path/to/new-run.jsonl \
  --schedule idle:10,play:25,pause:15,idle:10
```

`idle` 不创建播放器；后续进入 idle 时解绑图层、停止循环、清空队列并关闭释放窗口。框架缓存和系统服务占用可能在释放后继续保留。`pause` 保持播放器和最后画面，仅暂停播放。`play` / `uncover` 请求播放并移走遮挡窗口。

显式遮挡实验：

```sh
research/benchmarks/build/WallpaperProbe \
  --video /absolute/path/to/loop.mp4 \
  --output /absolute/path/to/new-occlusion-run.jsonl \
  --schedule idle:10,play:15,cover:15,uncover:15,idle:5 \
  --cover --pause-on-occlusion
```

`cover` 必须额外给 `--cover`。它创建一个不透明、鼠标穿透的桌面层窗口，遮挡本探针；不会置于普通应用窗口之上。`--pause-on-occlusion` 根据播放器窗口的 AppKit occlusion 状态决定是否暂停，默认不启用，以便比较“持续请求播放”和“不可见时暂停”。必须查看 `window_visible` 与 `player_rate` 是否实际改变；命令名本身不能证明遮挡检测成功。

默认总时长限制仍为 1–120 秒；只有显式传入 `--sustained` 才允许最长 3600 秒，仍自动退出，不取消上限。Ctrl-C / SIGTERM 会停止自己的播放器、关闭自己的窗口并保存日志。进程关闭也会由 WindowServer 清理其窗口。探针不改变屏保、系统壁纸、能耗设置或焦点，不发起网络请求，不开启声音，不阻止屏幕睡眠；收到屏幕睡眠通知时暂停。

## 每秒记录什么

| 字段 | 含义与边界 |
| --- | --- |
| `event`, `elapsed_seconds`, `phase`, `phase_index` | 事件、单调时钟相对运行时间、当前计划阶段 |
| `cpu_seconds` | 本进程累计 user + system CPU 时间，来自 `getrusage` |
| `cpu_percent_one_core` | 两次采样差分；一个逻辑核心满载为 100%，不是全机归一化值 |
| `physical_footprint_bytes` | Mach `TASK_VM_INFO.phys_footprint`；不能与 RSS 混用 |
| `resident_bytes`, `compressed_bytes` | 本进程驻留和压缩内存；分别记录，不自行相加声称全系统成本 |
| `window_visible` | AppKit 认为至少部分窗口可见；不代表全部画面可见 |
| `layer_ready`, `player_rate`, `media_seconds`, `loop_count` | 帧已可显示、播放器 rate 属性、媒体时间和循环次数；结合 timeControlStatus 判断媒体时间是否推进，不能代替逐帧呈现测量 |
| `player_status`, `time_control_status`, `looper_status` | AVFoundation 状态原始枚举，`-1` 表示没有相应对象 |
| `playback_error` / `event=error` | 播放失败原因；不能把这种运行当作低资源的有效样本 |

首条 `event=start` 包含系统、逻辑 CPU 数、屏幕数量/点尺寸/像素尺寸/缩放因子及视频路径。本地原始日志包含文件路径。公开前使用 `sanitize_results.py` 制作新副本并人工核对；仓库现有公开记录已规范化路径与 PID、移除精确墙钟时间，测量值保持不变。`finish.reason=schedule_complete` 表示计划正常结束；其他结束原因应检查。参数错误返回 2，运行错误返回 3，信号终止返回 `128 + signal`，正常结束返回 0。

每次阶段开始后的数秒包含初始化、预读和上个阶段的采样差分，统计稳定区间时应去掉。`play` 阶段必须同时观察 `layer_ready=true`、媒体时间推进和有效 rate；用户应实际看见动画。如果到结束仍没有 ready 图层或媒体时间未推进，即使程序按计划返回 0，也只能标为“播放未确认，结论不足”。如果桌面被其他窗口挡住，或缩略预览未真正显示，不能据其数据判断完整可见时的合成成本。

## 能证明什么、还不能证明什么

探针测的是本进程 CPU 和内存。硬件解码可能在系统服务和专用媒体引擎中运行，桌面合成发生于 WindowServer；**小 CPU 数值不等于零 GPU、零解码成本或全系统低功耗**。需要同时测 WindowServer、解码服务和系统 GPU / 能耗，并与同机近邻的 idle 段比较。已有 GPU 重负载、屏幕录制和远程桌面会污染对照。

程序只覆盖一个主屏窗口，不验证多屏、Spaces 切换、长时间运行、锁屏恢复和成品安装行为。AppKit occlusion 只提供“至少部分可见”，不提供精确可见面积；透明窗口、桌面图标和系统特殊层级都可能影响判断。程序没有采样每帧实际呈现时间，因此不会声称验证了所有帧都按源 FPS 展示。

源文件通过 Apple SDK 中的 `AVPlayer.h`、`NSWindow.h`、`mach/task_info.h` 和 `sys/resource.h` 核对并编译。官方概念参考：[AVPlayerLooper](https://developer.apple.com/documentation/avfoundation/avplayerlooper)、[NSWindow 遮挡变化](https://developer.apple.com/documentation/appkit/nswindow/didchangeocclusionstatenotification)、[OcclusionState](https://developer.apple.com/documentation/appkit/nswindow/occlusionstate-swift.struct)、[播放时是否阻止睡眠](https://developer.apple.com/documentation/avfoundation/avplayer/preventsdisplaysleepduringvideoplayback)。

## 外部系统采样和复现

`measure.py` 启动上述探针，同时采样播放器、WindowServer、WallpaperAgent、全部 VTDecoderXPCService 的累计 CPU、RSS、进程启动身份、IOKit 全局 GPU，以及低频的 `top MEM` footprint。它只保存这些目标进程和一小组已知活动程序名称，不保存完整进程参数；名称存在不能证明正在渲染，名单为空也不能证明机器安静。脚本有超时和信号清理，只终止自己启动的探针。

```sh
python3 research/benchmarks/measure.py \
  --probe research/benchmarks/build/WallpaperProbe \
  --video /absolute/path/to/loop.mp4 \
  --output-dir research/benchmarks/results_private/my-new-batch/1080p24 \
  --schedule idle:10,play:25,pause:15,idle:10

python3 research/benchmarks/summarize.py research/benchmarks/results_private/my-new-batch
```

外部脚本使用全新结果目录，记录二进制／视频和脚本哈希、执行参数、平台、电源来源与采样耗时。`ps TIME` 精度为百分之一秒，2 秒差分约有 0.5 个百分点量化尺度，不能把短时间的零增量当成零 CPU。新版 schema 2 使用跨进程相同的 `CLOCK_MONOTONIC`，按实际阶段起点对齐整个采集区间，默认去掉开头 4 秒和末尾 1 秒；`--warmup-seconds` 可调整预热。跨阶段采集被排除，最近一次探针采样的阶段标签只用于诊断。旧 schema 1 保留原先的短测算法和原始汇总值。

IOKit GPU 百分比是驱动提供的全局快照，不是本进程 GPU 占用，也不是瓦数；新版按设备 ID 分别汇总，不能将不同 GPU 平均成一个设备。全部 VTDecoder 的 RSS/footprint 包含其他应用，不能直接归属给壁纸。不要相加 footprint、RSS 和 GPU 分配数字作为“总内存”。`top MEM` 是经过显示单位取整的 footprint 观测，不是精确到字节的原始 ledger；身份在读取前后必须一致。

本次素材命令及哈希在 [fixtures.json](results/2026-11-10/fixtures.json)，将其中 `$FIXTURE_DIR` 路径占位符替换为自己的新目录即可重建。示例：

```sh
ffmpeg -hide_banner -n -f lavfi -i 'testsrc2=size=1920x1080:rate=24' \
  -t 4 -an -c:v libx264 -threads 4 -preset fast -crf 20 \
  -pix_fmt yuv420p -movflags +faststart /absolute/new/1080p24.mp4
```

结果解读见 [性能报告](../05-performance-feasibility.md)。第一次 720p24 日志中的 `pixel_width/height` 来自 CGDisplayPixelsWide/High，在本机报告的是逻辑尺寸；后续改用 CGDisplayMode.pixelWidth/pixelHeight。该次测量字段按原样保留；私有原始文件保存在被 Git 忽略的 `results_private/`，公开副本仅规范化元数据。实际原生屏幕尺寸见 [hardware.json](results/2026-11-10/hardware.json)，该元数据修正不改变播放或测量代码路径。

## 公开测量副本

新运行默认写入 `results_private/`；该目录不提交。检查原始记录后，用下列命令写入一个尚不存在的公开目录：

```sh
python3 research/benchmarks/sanitize_results.py \
  research/benchmarks/results_private/my-new-batch \
  research/benchmarks/results/my-new-batch \
  --project-root "$PWD" \
  --fixture-root /absolute/original/fixture-directory
```

若日志使用了 `/tmp` 与 `/private/tmp` 两种同义路径，请重复传入 `--fixture-root`。参数应填写生成记录时的实际路径，而非复制记录后的新路径。工具只复制 JSON/JSONL，稳定映射每个运行目录内的 `pid` / `probe_pid`，将 schema 2 的 `identity.start_time` 映射为匿名启动标识，并删除 `started_unix_seconds` / `unix_seconds`；测量数值保持不变，同 PID 重启仍能区分。路径替换只匹配字符串开头，不处理自由文本内嵌路径、JSON 键或文件名；PID 映射按直接父目录分组，嵌套样本目录或跨目录引用需要另行处理。它不会检测任意秘密或自由文本中的个人信息，发布前仍须检查文件列表、内容与汇总一致性。参见 [本批数据的公开处理记录](results/2026-11-10/SANITIZATION.md)。

## M1 工具入口与实际边界（schema 2）

先编译本页的 Swift 源码。无 GUI 的参数验证不会创建输出目录或 JSONL：

```sh
python3 research/benchmarks/measure.py \
  --probe research/benchmarks/build/WallpaperProbe \
  --output-dir research/benchmarks/results_private/validation-only \
  --schedule idle:3600 --sustained --validate

python3 research/benchmarks/measure.py --capabilities
```

`--capabilities` 只做一次普通用户 `ps`/`ioreg`/定向 `top MEM` 检查，不启动探针，不提权。受环境沙盒限制时需要正常的命令访问权限，不能误认为必须 sudo。结果会包含 PID/启动时间，应先保存在被忽略目录。字段缺失、超时、权限失败是显式错误与 null，不用零值代替。

下面是 **待安静桌面条件具备后执行** 的有界长测示例，不代表已经做过：

```sh
python3 research/benchmarks/measure.py \
  --probe research/benchmarks/build/WallpaperProbe \
  --video /absolute/path/to/720p24.mp4 \
  --output-dir research/benchmarks/results_private/controlled-batch/run-01 \
  --schedule idle:180,play:1920,pause:180,idle:180 \
  --sustained --purpose controlled --warmup-seconds 60 \
  --sample-interval 2 --footprint-interval 10 --pause-on-occlusion

python3 research/benchmarks/summarize.py research/benchmarks/results_private/controlled-batch
```

总计划 2460 秒；播放阶段在剔除 60 秒预热和末尾 1 秒后，留出约 31 分钟的观测窗口，仍需检查实际有效采样跨度至少达到 30 分钟。`--purpose controlled` 只是记录意图，不会自动确认桌面完全可见或基线合格。第一阶段应先做短 smoke 确认日志、播放与退出，然后按计划组织三次重复、近邻静态对照和采样器开销校准。不同条件不能仅凭一个长脚本就声称完整矩阵已完成。不能在 TD/编码器工作时运行受控采样，IDE/桌面活动也须保持安静。

- 默认每 2 秒采集 CPU/GPU，每 10 秒尝试定向 `top MEM`；`--footprint-interval 0` 可关闭 footprint，用于独立测量采样开销。每个子工具有超时，每次采样保留起止时间和墙钟耗时。
- CPU 按 PID **和进程启动时间**核对；身份改变、缺失或累计时间倒退会拒绝差分。`ps lstart` 只有本地日历秒精度，属于防误配措施，不是不可碰撞的进程标识。
- 汇总器报告单次观测和质量问题；当前始终把整体验收记为 `inconclusive`。重复静态基线资格、helper 归属、sampler 开销、30 分钟内存趋势与整套资源预算仍需后续分析和实测。
- 暂停原因分别记录 `schedule`、`occluded`、`displaySleeping`、`sessionInactive`。未知会话状态显式记录并保守暂停；这些不是锁屏通知。初始状态来自公开 console/login 信息，会话通知仍需实机生命周期验证。
- 手动锁屏试验先启动有限日志，再由用户真实锁定/解锁，记录事件时段。检查窗口可见性、media time、rate 与其他暂停原因；模拟回调或应用失焦不能替代真实锁屏。
- 当前未实现或执行特权 `powermetrics` 工作流；GPU task ms/s 也不能替代全局利用率百分点门槛。

相关实现：[system_metrics.py](system_metrics.py)、[schema 2 汇总器](summarize_v2.py)。运行测试：`python3 -m unittest discover -s tests -v`；macOS 参数测试会编译临时 Swift 二进制，或使用 `WALLPAPER_PROBE_BINARY` 指定已编译程序。
