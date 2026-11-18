# Agent 创作、实时数据与 Wallpaper Engine 功能范围

核验日期：2026-11-17。用户将最终目标扩展为：App 内接入自选 agent API、按 skill 制作壁纸、主动打开 TD 模板编辑、接入天气等自定义 API，并长期对标 Wallpaper Engine 的功能。本页区分官方已有能力与本项目的拟议实现；没有安装、运行或测试下列新后端。

## 1. 功能对标需要多种运行方式

Wallpaper Engine 官方目前面向 Windows 与 Android，没有原生 macOS 版本。因此它是功能参考，不能直接成为本项目的 Mac 运行时。[官方平台说明](https://help.wallpaperengine.io/en/functionality/linuxmacos.html)

| 功能类别 | 官方资料确认的能力 | 对本项目的影响 |
| --- | --- | --- |
| 视频 | 本地视频播放，支持格式与 Workshop 发布格式有区别 | 保留 AVFoundation 路线；按实际 macOS 解码能力声明格式，而不是照抄 Windows 列表。[商店功能](https://store.steampowered.com/app/431960/Wallpaper_Engine/) |
| 实时 2D/3D | 粒子、视差、模型、灯光、时间线、shader、脚本 | MP4 只能保留已渲染动画；需要实时后端才能保留参数、输入与动态效果。[Scene 编辑器](https://docs.wallpaperengine.io/en/scene/overview.html) |
| Web 壁纸 | HTML/CSS/JavaScript 与专用宿主接口 | 可评估 WebKit 后端；使用浏览器技术本身不代表兼容 WE 的全部接口。[Web 文档](https://docs.wallpaperengine.io/en/web/overview.html) |
| 音频／鼠标 | 音频频谱、鼠标移动／点击等输入 | 音频采集、权限、输入坐标和桌面图标交互都要单独实现；完全鼠标穿透窗口无法直接提供点击控件。[音频接口](https://docs.wallpaperengine.io/en/web/audio/visualizer.html)、[鼠标事件](https://docs.wallpaperengine.io/en/scene/scenescript/tutorial/cursor.html) |
| 可编辑参数 | 颜色、滑杆、开关、文字、选项、文件等 | 需要共享的属性 schema，让设置界面、agent 和运行时使用同一份定义。[用户属性](https://docs.wallpaperengine.io/en/web/customization/properties.html) |
| 时间与外部数据 | 时间脚本、可联网的 Web 内容；SceneScript 不包含 Web 功能 | 天气等统一 HTTP 数据源是本项目自己的能力设计，不宣称 WE 自带通用天气服务。[时间](https://docs.wallpaperengine.io/en/scene/scenescript/tutorial/timeofday.html)、[SceneScript 边界](https://docs.wallpaperengine.io/en/scene/scenescript/introduction.html) |
| 多屏／播放列表 | 多屏配置、定时和按应用切换 | 需要每屏生命周期、素材配置与共享数据采样；必须重新验收总体成本。[配置](https://help.wallpaperengine.io/en/functionality/wallpaperperapp.html)、[官方功能](https://www.wallpaperengine.io/en) |
| Workshop／项目兼容 | 编译后的 Scene `.pkg` 不包含完整可编辑项目数据 | 功能对标、包运行兼容、源工程转换、Steam 内容访问是不同工程。不能承诺任意 Workshop 壁纸自动转为 `.toe`。[编辑下载壁纸](https://help.wallpaperengine.io/en/functionality/editingwallpapers.html)、[Steam Workshop 实现](https://partner.steamgames.com/doc/features/workshop/implementation) |

这不是“所有功能已覆盖”的声明。完整对标还需固定版本、枚举编辑器效果／脚本／宿主接口及测试样例；Windows 应用壁纸、专有硬件联动、Android 配套和系统锁屏等需分别标记平台相关性与适用范围。

## 2. TD 的三种接入边界

**离线制作。** 保留 `.toe`／外部源码供用户编辑，导出视频供普通播放器使用。视频不会保存原网络对鼠标、声音或未来天气的响应逻辑。参数化的晴／阴／雨／夜间视频集合可以响应天气分类，但效果是预先制作的。

**TouchEngine 嵌入。** 官方 TouchEngine 可在宿主中加载 `.tox`，macOS SDK 提供相应集成。官方文档要求目标机器安装 TouchDesigner 或 TouchPlayer，并具有可用于 TouchEngine 的付费许可证；编辑器窗口无需一直打开，但运行时和许可依赖仍然存在。该路径是实时 TD 壁纸候选，不能套用视频模式的性能结果。[TouchEngine](https://docs.derivative.ca/TouchEngine)、[官方 macOS SDK](https://github.com/TouchDesigner/TouchEngine-macOS)

**外部 TD／TouchPlayer live 会话。** 可评估同机纹理共享与控制数据桥接。Syphon、OSC 或 WebSocket 是交换纹理／数据的方法，不会把 TD 网络编译成独立 Mac 程序。用户明确选择这一路径时，需要额外进程，性能与退出清理单独测量。[Syphon 输出](https://derivative.ca/UserGuide/Syphon_Spout_Out_TOP)、[OSC In CHOP](https://docs.derivative.ca/OSC_In_CHOP)、[WebSocket DAT](https://docs.derivative.ca/WebSocket_DAT)

未找到受文档支持的“任意 `.toe`／`.tox` 一键编译成无 TD 运行时依赖的 macOS App”路线。Derivative 的 Project Packager 文档描述 Windows 安装器，不能当成 Mac 原生导出能力。[Project Packager](https://derivative.ca/UserGuide/Project_Packager)

无需 TD 运行时的实时效果，必须由我们自己的受限场景／Metal 后端或 Web 后端执行。少量 shader／参数逻辑可以人工移植并验证；不能许诺任意 TD 网络自动转换。首个 TouchEngine spike 应检查 `.tox` 输入输出、Metal/IOSurface 同步、许可证失败、实例退出和暂停后资源释放，而不是先集成整套作品库。

2026-11-17 价格核验：TouchEngine SDK 集成本身无费用／版税，但外部宿主用户仍需要适用的付费许可。普通 TouchPlayer Commercial 为 **US$300／许可**，TouchDesigner Commercial 为 **US$600／许可**；Commercial 许可永久有效并包含一年更新，之后升级到更新版本需按覆盖期处理。免费 Non-Commercial 不能据此解锁外部 App 的 TouchEngine。个人学习可使用免费 TD，但用途／分辨率限制仍适用。播放已合法制作的 MP4 不需要 TD 运行时许可；制作端的用途许可需独立满足。这使 TouchEngine 更适合作为可选高级后端，而非所有用户的默认依赖。[SDK 许可](https://derivative.ca/UserGuide/TouchEngine)、[官方 Commercial 价格](https://derivative.ca/product/touchdesigner-commercial/34)、[许可类型](https://derivative.ca/UserGuide/Licensing)

## 3. Agent API 与数据 API 分开

模型接口负责理解需求和提出工具调用，App 负责读取授权项目中的 skill／参考文件、执行本地工具并返回结果。仅把 API key 和 SKILL.md 发给模型，不会自动赋予它本机文件或 TD 操作能力。Anthropic 的工具文档也明确区分模型返回的客户端工具调用和由应用实际执行的操作。[官方工具调用说明](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview)

我们的方案是提供者适配层、有限的项目工具和可取消任务。不同厂商的工具调用／流式／鉴权协议要分别验证；“兼容接口”不是任意 endpoint 都能直接工作的保证。API 调用与已安装 Claude/Codex 客户端会话是不同入口。

天气、时钟等常态更新由独立数据服务完成，模型只在创建／修改作品时参与。密钥由宿主配置保存，渲染层只收到清洗后的字段；桌面凭据存储拟采用 Keychain。开发时可导入明确选择的 `.env`，公开工程只放 `.env.example`。[Apple Keychain](https://developer.apple.com/documentation/security/keychain-services)

## 4. 洛杉矶天气的第一个完整例子

候选数据源为 Open-Meteo：`current` 可返回温度、天气代码、云量、降水、风速和昼夜状态。其“当前天气”基于天气模型数据，不能描述成来自用户窗外的实时传感器。初始方案每 15 分钟查询、缓存并记录数据时间，用稳定字段决定晴／阴／雨／夜间画面，之后可驱动云量、风速等连续参数。[天气接口](https://open-meteo.com/en/docs)

免费托管接口面向非商业用途；付费应用／商业展示不能自动沿用这一前提，需要选择符合用途的服务方案。数据署名和提供者条款随配置记录。[服务条款](https://open-meteo.com/en/terms)

网络失败时保留有时限的缓存，超过上限显示明确的离线默认画面；不把请求失败当成“晴天”。数据刷新不触发 LLM 请求、重新生成视频或启动 TD。详见 [平台设计](../plan/agent-platform.md) 与 [示例配置](../examples/weather-los-angeles/README.md)。这些仍是设计资料，不是已运行的天气壁纸。
