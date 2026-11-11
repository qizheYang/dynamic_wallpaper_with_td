# TouchDesigner 自动化生成壁纸视频

调研日期：2026-11-10。本文承接 [macOS 动态壁纸研究](01-macos-dynamic-wallpaper.md)。范围是方案研究；尚未创建或运行 TD 网络，下面的流程、任务字段和验收标准是本项目的设计建议。

建议把 TouchDesigner 定位为**离线内容生成器**：制作一个可调参数的视觉模板，批量生成无缝循环视频，再交给 macOS 播放端。日常播放已导出的视频，无需长期运行 TD。若以后需要随天气、音乐或鼠标实时变化，再考虑实时渲染。

## 1. 哪些自动化能力已有官方支持

| 能力 | 已确认的机制 | 对本项目的用途 |
| --- | --- | --- |
| 项目与模块复用 | `.toe` 保存完整项目，`.tox` 保存组件 | 一个导出工程，多种视觉模板 |
| 启动、逐帧执行 | Execute DAT 的 `onStart()`、`onFrameStart()`、`onFrameEnd()` | 读取任务、推进导出状态、记录进度 |
| 脚本保存在磁盘 | Text DAT / Execute DAT 可加载外部文件；Sync to File 是双向同步 | Python、GLSL 可用 Git 审查和版本管理 |
| 时间控制 | `project.realTime`；Time COMP 的 `frame`、`rate`、`play`、范围和循环设置 | 让输出由帧编号驱动 |
| 输出与检查 | Movie File Out TOP；TOP 的 `save()` | 视频、图像序列、缩略图和诊断帧 |

来源：[文件类型与基本概念](https://derivative.ca/UserGuide/Terminology)、[Execute DAT](https://docs.derivative.ca/Execute_DAT)、[Text DAT](https://docs.derivative.ca/Text_DAT)、[Project Class](https://derivative.ca/UserGuide/Project_Class)、[TimeCOMP Class](https://docs.derivative.ca/TimeCOMP_Class)、[TOP 保存接口](https://derivative.ca/UserGuide/TOP_Class)。

建议准备 `export.toe`，其中固定视觉模块、输出 TOP 和任务控制器；配色、随机种子、速度、时长、分辨率从 JSON 任务读取。启动回调只负责初始化；渲染采用跨帧状态机。不要在一个回调中用长时间 `while` 循环等待渲染，否则容易阻塞 TD 的处理循环。

`.toe` 内的 TD Python API 不是普通 Python 包。官方对 `app.pythonExecutable` 的说明是运行纯 Python 与附带的包；不能据此认为在终端执行 `python render.py` 就能创建、烹制 TD 节点。[App Class](https://derivative.ca/UserGuide/App_Class)

## 2. 先解决循环和可复现性

以下为项目设计，不是 TD 内置的“自动无缝循环”功能。

给定时长 `T`、帧率 `F`，约定帧数 `N = T × F` 为整数，输出编号 `i = 0 … N-1`：

```text
phase = i / N
theta = 2π × phase
periodic_x = cos(theta)
periodic_y = sin(theta)
```

把周期坐标用于旋转、形变或噪声采样，或者直接在 GLSL 中构造周期函数。最后不再输出 `i=N`，因为它与下一轮的 `i=0` 重复。循环的判断是末帧到首帧的变化是否与正常相邻帧连贯，**不是要求已导出首末两帧完全相同**。

对首个模板，优先选取无状态的几何变换、周期纹理或 shader。Feedback、粒子模拟、积分型 CHOP 等依赖历史状态，仅让时间线回到起点不能使状态自动闭合。此类模板必须固定重置过程、步长、预热帧数；预热也不能保证形成周期轨道，仍需独立检查接缝。

所有动画绑定任务帧或局部时间线，固定随机种子。`absTime` 从进程启动后累积，不随循环时间线重置，不适合作为每次任务可重现的相位来源。[Absolute Time](https://derivative.ca/UserGuide/Absolute_Time)

离线导出使用 `project.realTime = False`；官方定义是按顺序处理每一帧，而不靠跳帧追赶实时速度。局部时间线帧率和输出 FPS 需一致。这个设置解决时间推进策略，但仍必须检查编码器、录制边界和输出文件。[Project Class](https://derivative.ca/UserGuide/Project_Class)

“同一任务可复现”首先指同一 build、模板、素材、种子和时间采样得到相同运动。不同 GPU、TD build 或编码器之间的像素级、文件级完全一致，需要另外测量，不能直接承诺。

## 3. 导出路径：先图像序列，后优化直接录制

Movie File Out TOP 支持电影、图像和序列。与自动化直接相关的参数是：`record`、`fps`、`limitlength`、`length`、`lengthunit`；`stallforopen` 可避免打开文件期间漏掉起始帧。`addframe` 用于单帧写入，需配合 Pause。[Movie File Out TOP](https://derivative.ca/UserGuide/Movie_File_Out_TOP)

| 路径 | 适用场景 | 取舍 |
| --- | --- | --- |
| PNG / TIFF 序列 → FFmpeg | 首轮验证、批量任务、可恢复输出 | 能数清帧、检查和补渲，磁盘占用大 |
| ProRes 中间文件 → FFmpeg | 长片段、已有稳定录制模板 | 文件较大，先验证当前 Mac/build 的支持情况 |
| TD 直接 H.264 | 流程稳定后的省空间优化 | 受版本、许可证、编码器和容器组合影响 |

若需要最明确的单帧完成信号，可对最终 TOP 调用 `save()`。该方法返回 `FileSaveStatus`；异步保存时可跨帧查询 `isCompleted()`，确认完成后再宣布这一帧已落盘。这适合作为首个确定性导出的保守实现；性能要在真实分辨率下测量。[TOP 保存接口](https://derivative.ca/UserGuide/TOP_Class)、[FileSaveStatus](https://derivative.ca/UserGuide/FileSaveStatus_Class)

Movie File Out 的 Info CHOP 提供 `total_frames_written`、`total_frames_dropped`。序列模式还提供 `active_records`，用于观察尚在写入的图像。它不是所有电影格式的通用完成回调；不要把 `record=0` 或“文件已出现”当作整个交付完成。[Movie File Out TOP](https://derivative.ca/UserGuide/Movie_File_Out_TOP)

建议首版使用无音轨输出，并采用以下状态推进：

```text
读取并验证任务
  → 加载模板和种子
  → 重置 / 预热
  → 从第 0 帧顺序渲染到 N-1 帧
  → 停止接收新帧并等候写盘
  → 核验序列完整性
  → FFmpeg 编码到临时产物
  → 检查可解码性、帧数、时长和循环接缝
  → 改为最终文件名，写入成功状态
```

任务运行期间保持 TD 进程存活。图像序列以“所有预期文件可读且写入完成”为条件；直接电影输出则还要验证容器收尾和完整解码。为失败任务保留日志、中间帧和错误原因，不进入壁纸目录。FFmpeg 示例和整体交付约定见 [建议实施路线](03-proposed-pipeline.md)。

## 4. macOS 和许可证约束

截至调研时，官方 Movie File Out 页面明确写出 **macOS 支持 H.264 编码**。其菜单说明仍包含 NVIDIA 名称，不能把 Windows 的 `h264nvgpu` 参数照搬到 Mac，也不能继续引用旧教程断言 Mac 完全不能导出 H.264。HEVC、像素格式、容器和硬件加速情况须按安装 build 的菜单及短样片验证。[Movie File Out TOP](https://derivative.ca/UserGuide/Movie_File_Out_TOP)

| 项目 | 官方结论 / 本项目影响 |
| --- | --- |
| Non-Commercial | 仅非付费、非商业用途；分辨率上限为 1280 × 1280，不能原生输出 1920 × 1080 或 4K |
| Educational | 无上述分辨率限制；官方许可表称其功能与 Commercial 相同，但用途仍受教育和非商业条件限制 |
| Commercial / Pro | 可用于付费项目；本项目没有已知必须使用 Pro 的功能需求 |
| 编码许可文档不一致 | 许可表把 Educational 的 H.264/H.265 导出列为可用，Movie File Out 页只列 Commercial / Pro；Educational 在 Mac 上的具体组合仍需实测或向 Derivative 核实 |

依据：[Licensing](https://derivative.ca/UserGuide/Licensing)。外部编码不会提高 TD 本身的原生渲染分辨率，也不改变其许可证允许的用途。

TouchDesigner 2025 的官方系统要求是 macOS 13 或更高；NVIDIA/CUDA 专有能力不能作为 Mac 方案的依赖，Apple Silicon 不支持 Cineform SDK。首个模板应使用通用 TOP / GLSL 能力。[MacOS](https://derivative.ca/UserGuide/MacOS)

本次本机只读检查发现 `/Applications/TouchDesigner.app` 的版本为 **2025.33230**，许可证类型尚未读取，也没有连接 TD 运行时。另有维护者在 2026-10-15 确认：一项非实时录制掉帧问题将在 **2025.33360 及以后**修复；原报告随后发现问题也影响 ProRes，并与音频和连续录制相关。本机 build 低于该修复编号，是否受影响尚未复现，不能因此宣称本机导出一定失败或一定无损帧。[维护者确认及复现条件](https://forum.derivative.ca/t/fixed-encoding-h265-h264-drops-frames-even-if-not-in-realtime-appleprores-does-not-even-with-extreme-lag/1018935)

## 5. 批处理启动与“无界面”的边界

首版建议采用**登录的 macOS 图形会话中运行一个 TD 导出工程**：工程内读取项目相对路径的任务文件，外部调度器负责准备任务、监控超时和编码。最初可手动启动该工程；批量参数和导出不再逐项点击。后续才验证指定 app/build 打开指定 `.toe`、进程退出与重启的自动化。

这次未找到并验证可作为 macOS 稳定契约的任意 `--script` / `--headless` / 自定义命令行参数接口，因此不提供猜测出来的命令。任务传递优先使用已约定的 JSON 文件；环境变量和命令行参数透传留作实测项。Perform Mode 可以作为保存于项目的启动设置，但“隐藏编辑器”不能等同于“支持无登录会话的服务器渲染”。[Perform Mode](https://derivative.ca/UserGuide/Perform_Mode)

TouchPlayer 只运行项目，没有网络编辑界面；Mac 上是单独应用。免费版本具有控制面板，访问某些设备和网络服务会弹确认框。因此它适合测试作品分发，不能仅凭名称就把它当作无需维护的批处理渲染服务。[TouchPlayer](https://docs.derivative.ca/TouchPlayer)

更进一步的方案是 TouchEngine。官方 macOS SDK 可加载 `.tox`，支持由宿主传入时间并接收帧完成事件；这提供了比操纵 GUI 更明确的宿主控制方式。但需要自行开发原生宿主、管理 GPU 纹理和输出编码；用户机器仍需安装 TD/TouchPlayer 并拥有付费许可证。现阶段成本明显高于导出 `.toe`，适合作为第二阶段的可行性实验。[TouchEngine macOS SDK](https://github.com/TouchDesigner/TouchEngine-macOS)、[TouchEngine 许可与安装要求](https://derivative.ca/UserGuide/TouchEngine)

如果把同一组件改为 Engine COMP / TouchEngine 运行，要重审启动逻辑：Execute DAT 的 `onStart()`、`onExit()` 在这种加载生命周期中不会运行，应使用组件创建等相应机制。[Engine COMP](https://derivative.ca/UserGuide/Engine_COMP)

## 6. 最小验证实验

1. 选择一个周期性、无反馈模板，先生成 1280 × 720、30 FPS、20 秒、600 帧的无声序列；输出参数与许可证不符时明确报错。
2. 在同一 TD build 上用同一任务运行两次，比较源帧；再改变 seed / palette，验证变化确实来自任务输入。
3. 确认序列编号连续、无缺帧，电影可完整解码且为 600 帧 / 20 秒；把播放窗口跨越尾首接缝反复检查。
4. 让渲染速度低于 30 FPS，确认离线输出时长仍是 20 秒，帧内容没有被跳过或重复。图像序列可加入测试用帧编号，正式导出再移除。
5. 测试第二个任务紧接第一个任务时的重置和写盘收尾；模拟单次失败，确认不会把半成品交给壁纸端。
6. 通过后再增加分辨率、片段时长、模板数量和直接视频编码。保留 build、模板版本、种子、帧数、输出格式和验证结果，作为每个产物的记录。

这些实验尚未执行。首个里程碑应是“一份 JSON 能稳定产出一段可复现的循环视频”，随后再接入 macOS 播放和批量队列。
