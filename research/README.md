# 动态壁纸与 TouchDesigner 研究

研究日期：2026-11-10。按“macOS 如何实现动态壁纸 → TouchDesigner 如何自动生成视频 → 两者如何接起来”的顺序阅读。

| 文档 | 内容 |
|---|---|
| [01 · macOS 动态壁纸实现](01-macos-dynamic-wallpaper.md) | 昼夜 HEIC、系统 aerial、持续视频的区别；桌面窗口和原生私有扩展；多屏、Spaces 与省电 |
| [02 · TouchDesigner 视频自动化](02-touchdesigner-video-automation.md) | 模板、Python/DAT、确定性循环、逐帧导出、批处理、许可证与版本限制 |
| [03 · 建议实施路线](03-proposed-pipeline.md) | 架构图、任务 JSON、状态机、FFmpeg 命令、本机检查与分阶段验收 |
| [04 · 现有程序源码核验](04-existing-apps-performance.md) | LiveDesk、Aerial、Mural 的固定提交；降帧率、遮挡、缓存和资源释放的实际实现 |
| [05 · 低资源可行性与实测](05-performance-feasibility.md) | 四组本机原生播放实验、CPU/内存数据、系统 GPU 的测量限制，以及性能门槛 |

建议路线仍是 **TD 参数化模板 → PNG 帧序列 → FFmpeg 编码 → macOS 视频播放**。当前以低 CPU、GPU、内存为首要约束：先验收单屏原生播放和不可见时暂停，再接入 TD 的参数化作品。默认从 720p/1080p、24 fps、无音频开始；早期文档中的 30 fps 示例用于验证导出计数，不是默认播放档位。

两条替代路线也值得保留：只需要日夜变化时导出动态 HEIC；需要系统壁纸选择器和锁屏衔接时，单独验证 Aerial 等原生壁纸扩展。目前这些扩展依赖私有接口，不能当作公开稳定的系统 SDK。[Aerial 技术说明](https://github.com/AerialScreensaver/Aerial/blob/main/README.md)

资料优先采用 Apple、Derivative、FFmpeg 官方文档和项目维护者的源码说明。文中区分了来源已确认的能力、拟议方案和待实测项；参数与版本结论以调研日期为界。

已完成文献／源码研究、FFmpeg 小样本编码及四组各约 60 秒的原生桌面层播放测试。播放器自身播放 CPU 约 1.31%–1.74%（单核=100%），暂停约 0.05%–0.06%，但这些不是整个壁纸系统的总开销。脱敏测量记录、探针源码及重跑方法在 [benchmarks](benchmarks/README.md)；完整实施顺序与最新验收门槛在 [plan](../plan/README.md)。

整体 GPU/能耗、可归属的辅助进程内存、30 分钟稳定性和复杂桌面生命周期尚未验收；TD 实际导出、作品色彩与无缝接缝也仍待验证。当前结论是“功能路线成立，低资源方向有支持，完整性能门槛尚未全部通过”。
