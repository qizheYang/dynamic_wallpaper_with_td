import AppKit
import SwiftUI
import WallpaperCore

// Explicitly select the property wrapper. CLT-only SDKs can expose the newer
// State macro declaration without shipping Xcode's SwiftUIMacros plugin.
private typealias LocalState<Value> = SwiftUI.State<Value>

struct StudioView: View {
    @ObservedObject var model: StudioModel
    var body: some View {
        HStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 22) {
                VStack(alignment: .leading, spacing: 5) {
                    Image(systemName: "water.waves").font(.system(size: 30)).foregroundStyle(.teal)
                    Text("Wallpaper\nStudio").font(.system(size: 24, weight: .semibold, design: .rounded))
                    Text("让桌面慢下来").font(.caption).foregroundStyle(.secondary)
                }.padding(.bottom, 15)
                nav("library", "壁纸素材", "square.grid.2x2")
                nav("create", "制作壁纸", "sparkles")
                nav("weather", "天气与数据", "cloud.sun")
                nav("settings", "连接与设置", "slider.horizontal.3")
                Spacer()
                Text("0.1 · 本地开发预览").font(.caption).foregroundStyle(.secondary)
                Text("普通视频播放无需 TouchDesigner")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }.padding(25).frame(width: 200).frame(maxHeight: .infinity)
                .background(Color(nsColor: .controlBackgroundColor))
            Divider()
            VStack(spacing: 0) {
                ActiveStrip(model: model, host: model.host, scene: model.scene)
                Divider()
                ScrollView {
                    VStack(alignment: .leading, spacing: 22) {
                        switch model.selectedTab {
                        case "create": CreationView(model: model)
                        case "weather": WeatherView(model: model, weather: model.weather)
                        case "settings": SettingsView(model: model)
                        default: library
                        }
                        if !model.notice.isEmpty {
                            Label(model.notice, systemImage: "info.circle").font(.caption).foregroundStyle(.secondary)
                                .textSelection(.enabled)
                        }
                    }.padding(30).frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
        .frame(minWidth: 920, minHeight: 650)
        .alert("需要处理", isPresented: Binding(get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } })) {
            Button("知道了") { model.errorMessage = nil }
        } message: { Text(model.errorMessage ?? "") }
    }

    private func nav(_ tag: String, _ title: String, _ icon: String) -> some View {
        Button { model.selectedTab = tag } label: {
            Label(title, systemImage: icon).frame(maxWidth: .infinity, alignment: .leading)
                .padding(10).background(model.selectedTab == tag ? Color.teal.opacity(0.13) : Color.clear)
                .clipShape(RoundedRectangle(cornerRadius: 9))
        }.buttonStyle(.plain)
    }

    private var library: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack {
                VStack(alignment: .leading, spacing: 7) {
                    Text("你的桌面，安静一点。").font(.largeTitle.weight(.semibold))
                    Text("已验证的视频在本机循环播放，制作工具只在创作时启动。").foregroundStyle(.secondary)
                }
                Spacer()
                Button { model.chooseBundle() } label: { Label("导入素材包", systemImage: "plus") }
                    .buttonStyle(.borderedProminent).tint(.teal).disabled(model.busy)
            }
            if model.assets.isEmpty {
                VStack(spacing: 14) {
                    Image(systemName: "photo.on.rectangle.angled").font(.system(size: 52)).foregroundStyle(.teal)
                    Text("从一张会呼吸的壁纸开始").font(.title2)
                    Text("导入完成的素材文件夹，或使用制作页面的渐变模板。")
                        .foregroundStyle(.secondary)
                    Button("制作第一张壁纸") { model.selectedTab = "create" }
                }.frame(maxWidth: .infinity).padding(.vertical, 75)
            } else {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 230), spacing: 20)], spacing: 20) {
                    ForEach(model.assets) { asset in
                        VStack(alignment: .leading, spacing: 12) {
                            if let cover = NSImage(contentsOf: asset.coverURL) {
                                Image(nsImage: cover).resizable().aspectRatio(16/9, contentMode: .fit)
                                    .clipShape(RoundedRectangle(cornerRadius: 12))
                            }
                            Text(asset.title).font(.headline).lineLimit(2)
                            Text("本地视频 · 静音 · TD 无需常驻").font(.caption).foregroundStyle(.secondary)
                            HStack {
                                Button("应用到桌面") { model.apply(asset) }.buttonStyle(.borderedProminent).tint(.teal)
                                Spacer()
                                Button { NSWorkspace.shared.activateFileViewerSelecting([asset.directory]) } label: { Image(systemName: "folder") }
                                    .help("在 Finder 中查看素材")
                            }
                        }.padding(14).background(Color(nsColor: .controlBackgroundColor)).clipShape(RoundedRectangle(cornerRadius: 16))
                    }
                }
            }
        }
    }
}

private struct ActiveStrip: View {
    @ObservedObject var model: StudioModel
    @ObservedObject var host: WallpaperHost
    @ObservedObject var scene: SceneDesktop
    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: "desktopcomputer").font(.title2).foregroundStyle(.teal)
            VStack(alignment: .leading, spacing: 4) {
                Text(model.preferences.sceneMode == "video" ? "视频壁纸" : "原生实时场景").font(.headline)
                Text(model.preferences.sceneMode == "video" ? host.status : scene.status)
                    .font(.caption).foregroundStyle(.secondary).lineLimit(2)
            }
            Spacer()
            Button(model.preferences.userPaused ? "继续" : "暂停", systemImage: model.preferences.userPaused ? "play.fill" : "pause.fill") { model.pauseToggle() }
            Button("停止", systemImage: "stop.fill") { model.stop() }
        }.padding(.horizontal, 30).padding(.vertical, 18)
    }
}

private struct CreationView: View {
    @ObservedObject var model: StudioModel
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("把想法变成壁纸").font(.largeTitle.weight(.semibold))
            Text("描述颜色、运动和氛围。Agent 会阅读随应用提供的 skill，再给出建议或需要澄清的问题。")
                .foregroundStyle(.secondary)
            TextEditor(text: $model.brief).font(.body).frame(height: 110).padding(8)
                .background(Color(nsColor: .textBackgroundColor)).clipShape(RoundedRectangle(cornerRadius: 10))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(.gray.opacity(0.25)))
                .accessibilityLabel("壁纸需求")
            HStack {
                Button("让 Agent 整理需求", systemImage: "sparkles") { model.requestProposal() }
                    .disabled(model.busy || model.brief.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                if model.busy { ProgressView().controlSize(.small); Button("取消") { model.cancelCreation() } }
            }
            if let proposal = model.proposal {
                GroupBox("创作建议") {
                    VStack(alignment: .leading, spacing: 10) {
                        Text(proposal.brief).textSelection(.enabled)
                        ForEach(proposal.questions, id: \.self) { Text("• " + $0) }
                        if proposal.parameters == nil {
                            Text("将回答补充到需求中后再次请求。新视觉风格需要制作和校准对应模板。")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }.frame(maxWidth: .infinity, alignment: .leading).padding(8)
                }
            }
            Divider().padding(.vertical, 8)
            Text("本地模板 · 柔和暮色渐变").font(.title2.weight(.medium))
            Text("720p · 24 fps · 20 秒。当前模板支持随机种子和循环次数；不支持的风格不会被悄悄替换。")
                .font(.callout).foregroundStyle(.secondary)
            HStack(spacing: 25) {
                TextField("随机种子", value: $model.seed, format: .number).frame(width: 180)
                Stepper("循环次数 \(model.motionCycles)", value: $model.motionCycles, in: 1...8)
            }
            HStack {
                Button("生成并验证视频", systemImage: "film") { model.generateLocal() }
                    .buttonStyle(.borderedProminent).tint(.teal).disabled(model.busy)
                Button("创建副本并在 TD 编辑", systemImage: "arrow.up.forward.app") { model.editTemplate() }.disabled(model.busy)
            }
            Text(model.creationStatus).font(.callout).foregroundStyle(.secondary).textSelection(.enabled)
            Text("生成需要本机 TouchDesigner、Python 和 FFmpeg。视频完成后，普通播放不再需要这些工具。编辑后的模板需重新校准才能进入生产导出。")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}

private struct WeatherView: View {
    @ObservedObject var model: StudioModel
    @ObservedObject var weather: WeatherController
    @LocalState private var endpoint = ""
    @LocalState private var token = ""
    @LocalState private var mapping = "{\n  \"cloud\": {\"path\": [\"cloud_cover\"], \"inputMin\": 0, \"inputMax\": 100, \"outputMin\": 0, \"outputMax\": 1},\n  \"colorPath\": [\"color\"]\n}"
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("让桌面跟随真实世界").font(.largeTitle.weight(.semibold))
            Text("天气和 API 更新由应用直接处理，无需重复调用模型或启动 TD。")
                .foregroundStyle(.secondary)
            GroupBox {
                VStack(alignment: .leading, spacing: 12) {
                    Label("洛杉矶 · 天气天空", systemImage: "cloud.sun.fill").font(.title2)
                    Text(weather.statusText).foregroundStyle(.secondary)
                    if let snapshot = weather.snapshot {
                        Text("\(snapshot.temperatureCelsius, specifier: "%.1f") °C   ·   风速 \(snapshot.windMetresPerSecond, specifier: "%.1f") m/s   ·   \(snapshot.kind.rawValue)")
                        Text("数据时间：\(snapshot.observedAt.formatted())").font(.caption).foregroundStyle(.secondary)
                    }
                    HStack {
                        Button("启用天气壁纸") { model.activateWeather() }.buttonStyle(.borderedProminent).tint(.teal)
                        Button("刷新") { Task { await weather.refresh(force: true) } }
                        Link("Open-Meteo 数据来源", destination: URL(string: "https://open-meteo.com/")!)
                    }
                    Text("模型天气，每 15 分钟更新。使用原生渐变、云和降水动画；电池或低电量模式下保持静态。此实时模式的资源成本需单独验证。")
                        .font(.caption).foregroundStyle(.secondary)
                }.frame(maxWidth: .infinity, alignment: .leading).padding(12)
            }
            GroupBox("自定义 HTTPS JSON API") {
                VStack(alignment: .leading, spacing: 10) {
                    TextField("https://你的服务/数据", text: $endpoint)
                    SecureField("可选 Bearer token（保存在钥匙串）", text: $token)
                    Text("映射 daylight / cloud / wind / intensity 为 0…1，或用 colorPath 读取 #RRGGBB。路径是字段名数组，不运行代码。")
                        .font(.caption).foregroundStyle(.secondary)
                    TextEditor(text: $mapping).font(.system(.caption, design: .monospaced)).frame(height: 125)
                    Button("连接并应用场景") { model.activateCustomAPI(endpoint: endpoint, mapping: mapping, token: token); token = "" }
                    Text("每 15 分钟刷新，错误时使用近期缓存或静态默认画面。仅主动连接你选择的服务。")
                        .font(.caption).foregroundStyle(.secondary)
                }.padding(10)
            }
        }.onAppear {
            if let config = model.preferences.customAPI {
                endpoint = config.endpoint.absoluteString
                let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
                if let data = try? encoder.encode(config.mapping), let text = String(data: data, encoding: .utf8) { mapping = text }
            }
        }
    }
}

private struct SettingsView: View {
    @ObservedObject var model: StudioModel
    @LocalState private var provider = AgentProviderKind.anthropic
    @LocalState private var endpoint = "https://api.anthropic.com/v1/messages"
    @LocalState private var modelName = ""
    @LocalState private var apiKey = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("连接与偏好").font(.largeTitle.weight(.semibold))
            GroupBox("模型服务 · 仅在请求创作时调用") {
                VStack(alignment: .leading, spacing: 12) {
                    Picker("协议", selection: $provider) {
                        Text("Anthropic Messages").tag(AgentProviderKind.anthropic)
                        Text("OpenAI-compatible Chat").tag(AgentProviderKind.openAICompatible)
                    }.onChange(of: provider) { value in
                        endpoint = value == .anthropic ? "https://api.anthropic.com/v1/messages" : "https://api.openai.com/v1/chat/completions"
                    }
                    TextField("完整 HTTPS API 地址", text: $endpoint)
                    TextField("模型标识（由你的服务提供）", text: $modelName)
                    SecureField("API key；留空保留已存凭据", text: $apiKey)
                    HStack {
                        Button("保存连接") { model.storeProvider(kind: provider, endpoint: endpoint, model: modelName, key: apiKey); apiKey = "" }
                        Button("读取所选 .env") { importEnvironment() }
                        Button("移除凭据", role: .destructive) { model.deleteProviderKey() }
                    }
                    Text("不会读取 Claude/Codex 客户端登录。Key 与服务来源绑定，不写入素材、提示词或普通设置。API 费用由所选服务收取。")
                        .font(.caption).foregroundStyle(.secondary)
                }.padding(10)
            }
            GroupBox("显示与资源策略") {
                VStack(alignment: .leading, spacing: 10) {
                    Picker("显示器", selection: Binding(get: { model.preferences.screenID ?? 0 }, set: { model.changeScreen($0 == 0 ? nil : $0) })) {
                        Text("主显示器").tag(UInt32(0))
                        ForEach(NSScreen.screens, id: \.localizedName) { screen in
                            Text(screen.localizedName).tag((screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.uint32Value ?? 0)
                        }
                    }
                    Text("一个显示器播放；电池、低电量、严重发热、屏幕睡眠、会话不活跃或完全遮挡时暂停。长暂停会释放视频队列。实际锁屏行为仍待目标系统验收。")
                        .font(.caption).foregroundStyle(.secondary)
                }.padding(10)
            }
            GroupBox("制作工具 · 视频播放无需配置") {
                VStack(alignment: .leading, spacing: 10) {
                    pathField("Python", value: $model.preferences.pythonPath)
                    pathField("FFmpeg", value: $model.preferences.ffmpegPath)
                    pathField("ffprobe", value: $model.preferences.ffprobePath)
                    TextField("可选开发仓库路径（留空使用随应用提供的制作工具）", text: $model.preferences.repositoryOverride)
                    Button("保存工具设置") { model.save() }
                }.padding(10)
            }
        }.textFieldStyle(.roundedBorder).onAppear {
            if let config = model.preferences.provider { provider = config.kind; endpoint = config.endpoint.absoluteString; modelName = config.model }
        }
    }

    private func pathField(_ label: String, value: Binding<String>) -> some View {
        HStack {
            Text(label).frame(width: 70, alignment: .leading)
            TextField("可执行文件路径", text: value)
            Button("选择") {
                let panel = NSOpenPanel(); panel.canChooseFiles = true; panel.canChooseDirectories = false
                if panel.runModal() == .OK, let url = panel.url { value.wrappedValue = url.path }
            }
        }
    }

    private func importEnvironment() {
        let panel = NSOpenPanel(); panel.canChooseFiles = true; panel.canChooseDirectories = false; panel.showsHiddenFiles = true
        panel.message = "只读取你选择的文件，不执行 shell 或变量展开"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            let values = try EnvironmentFile.parse(Data(contentsOf: url, options: .mappedIfSafe))
            apiKey = values["AGENT_API_KEY"] ?? values["API_KEY"] ?? values[provider == .anthropic ? "ANTHROPIC_API_KEY" : "OPENAI_API_KEY"] ?? ""
            if let value = values["AGENT_API_URL"] { endpoint = value }
            if let value = values["AGENT_MODEL"] { modelName = value }
        } catch { model.errorMessage = error.localizedDescription }
    }
}
