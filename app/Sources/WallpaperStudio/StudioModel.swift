import AppKit
import Combine
import CryptoKit
import Foundation
import WallpaperCore

@MainActor
final class StudioModel: ObservableObject {
    let dataDirectory: URL
    let library: AssetLibrary
    let host: WallpaperHost
    let weather = WeatherController()
    let scene = SceneDesktop()
    @Published var preferences: StudioPreferences
    @Published var assets: [WallpaperAsset] = []
    @Published var errorMessage: String?
    @Published var notice = ""
    @Published var busy = false
    @Published var creationStatus = "准备好开始创作"
    @Published var brief = ""
    @Published var proposal: AuthoringProposal?
    @Published var seed = 42
    @Published var motionCycles = 1
    @Published var selectedTab = "library"
    private var task: Task<Void, Never>?
    private var customTask: Task<Void, Never>?
    private var customExpiryTask: Task<Void, Never>?
    private var customConnection: (CustomAPIConfiguration, String?)?
    private var customLastGoodAt: Date?
    private var customLastGoodEffect: NativeEffectState?
    private var customNextAllowedRefresh = Date.distantPast
    private var customFailureCount = 0
    private var customGeneration = 0
    private let customCacheLifetime: TimeInterval = 3600
    private var weatherObservation: AnyCancellable?
    private var saveURL: URL { dataDirectory.appendingPathComponent("settings.json") }
    private let credentials = KeychainCredentialStore()
    private let creation = CreationService()

    init(dataDirectory: URL, diagnosticAllowBatteryPlayback: Bool = false, eventSink: ((PlaybackEvent) -> Void)? = nil) throws {
        self.dataDirectory = dataDirectory
        try FileManager.default.createDirectory(at: dataDirectory, withIntermediateDirectories: true)
        self.library = AssetLibrary(root: dataDirectory.appendingPathComponent("Library", isDirectory: true))
        self.host = WallpaperHost(eventSink: eventSink, diagnosticAllowBatteryPlayback: diagnosticAllowBatteryPlayback)
        let saved = try? Data(contentsOf: dataDirectory.appendingPathComponent("settings.json"))
        var settings = saved.flatMap { try? JSONDecoder().decode(StudioPreferences.self, from: $0) } ?? StudioPreferences()
        if settings.ffmpegPath.isEmpty { settings.ffmpegPath = findExecutable("ffmpeg") }
        if settings.ffprobePath.isEmpty { settings.ffprobePath = findExecutable("ffprobe") }
        preferences = settings
        assets = try library.list()
        weatherObservation = weather.$snapshot.sink { [weak self] snapshot in
            guard self?.preferences.sceneMode == "weather" else { return }
            self?.scene.update(snapshot)
        }
        scene.onEnvironmentChange = { [weak self] _ in self?.reconcileDataActivity() }
    }

    func launch(restore: Bool = true) async {
        if assets.isEmpty, let demo = Bundle.main.resourceURL?.appendingPathComponent("Demo"), FileManager.default.fileExists(atPath: demo.path) {
            do { _ = try await library.importBundle(at: demo); assets = try library.list() }
            catch { errorMessage = "示例素材导入失败：\(error.localizedDescription)" }
        }
        guard restore else { return }
        if preferences.weatherEnabled && preferences.sceneMode == "weather" {
            activateWeather()
        } else if preferences.sceneMode == "custom", let configuration = preferences.customAPI,
                  let data = try? JSONEncoder().encode(configuration.mapping), let mapping = String(data: data, encoding: .utf8) {
            activateCustomAPI(endpoint: configuration.endpoint.absoluteString, mapping: mapping, token: "")
        } else if let selected = assets.first(where: { $0.id == preferences.selectedAssetID }) {
            host.selectScreen(id: preferences.screenID)
            host.setUserPaused(preferences.userPaused)
            host.apply(selected)
        }
    }

    func save() {
        do {
            let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            try encoder.encode(preferences).write(to: saveURL, options: .atomic)
        } catch { errorMessage = "无法保存设置：\(error.localizedDescription)" }
    }

    func chooseBundle() {
        guard !busy else { return }
        let picker = NSOpenPanel(); picker.canChooseDirectories = true; picker.canChooseFiles = false
        picker.message = "选择含 manifest.json、loop.mp4 和 cover.png 的完成素材包"
        guard picker.runModal() == .OK, let url = picker.url else { return }
        _ = startImport(url)
    }

    func importBundle(_ url: URL, apply: Bool = false) async {
        await startImport(url, apply: apply)?.value
    }

    private func startImport(_ url: URL, apply: Bool = false) -> Task<Void, Never>? {
        // Reserve the shared operation before yielding. Menu actions must not replace
        // a generation task or clear its busy state while its TD child is alive.
        guard !busy else { return nil }
        busy = true
        let operation = Task {
            defer { busy = false }
            do {
                let asset = try await library.importBundle(at: url)
                assets = try library.list()
                notice = "已验证并导入：\(asset.title)"
                if apply { self.apply(asset) }
            } catch is CancellationError { notice = "已取消导入" }
            catch { errorMessage = error.localizedDescription }
        }
        task = operation
        return operation
    }

    func apply(_ asset: WallpaperAsset) {
        cancelCustomPolling()
        weather.stop(); scene.stop()
        preferences.sceneMode = "video"; preferences.weatherEnabled = false
        preferences.selectedAssetID = asset.id; preferences.userPaused = false
        host.selectScreen(id: preferences.screenID)
        host.setUserPaused(false); host.apply(asset)
        save()
    }

    func pauseToggle() {
        preferences.userPaused.toggle()
        host.setUserPaused(preferences.userPaused); scene.setPaused(preferences.userPaused)
        save()
    }

    func stop() {
        cancelCustomPolling()
        host.stop(); scene.stop(); weather.stop()
        preferences.selectedAssetID = nil; preferences.weatherEnabled = false; preferences.sceneMode = "video"
        save()
    }

    func changeScreen(_ id: UInt32?) {
        preferences.screenID = id
        host.selectScreen(id: id)
        if preferences.sceneMode != "video" {
            scene.start(screenID: id)
            if preferences.sceneMode == "weather" { scene.update(weather.snapshot) }
            scene.setPaused(preferences.userPaused)
        }
        save()
    }

    func activateWeather() {
        cancelCustomPolling()
        host.stop()
        preferences.sceneMode = "weather"; preferences.weatherEnabled = true
        scene.start(screenID: preferences.screenID); scene.update(weather.snapshot)
        scene.setPaused(preferences.userPaused)
        reconcileDataActivity(); save()
    }

    func activateCustomAPI(endpoint: String, mapping: String, token: String) {
        do {
            guard let url = URL(string: endpoint) else { throw StudioFailure.message("API 地址无效") }
            let fields = try JSONDecoder().decode(CustomEffectMapping.self, from: Data(mapping.utf8))
            let config = CustomAPIConfiguration(endpoint: url, mapping: fields)
            let reference = "wallpaper-studio-custom-data"
            if !token.isEmpty { try credentials.save(secret: token, reference: reference, endpoint: url) }
            let saved = try? credentials.read(reference: reference, endpoint: url)
            let changed = customConnection?.0 != config || customConnection?.1 != saved
            cancelCustomPolling(); weather.stop(); host.stop()
            if changed {
                customLastGoodAt = nil; customLastGoodEffect = nil
                customNextAllowedRefresh = .distantPast; customFailureCount = 0
            }
            customConnection = (config, saved)
            preferences.sceneMode = "custom"; preferences.weatherEnabled = false
            preferences.customAPI = config
            scene.start(screenID: preferences.screenID); scene.setPaused(preferences.userPaused); save()
            reconcileDataActivity()
        } catch { errorMessage = error.localizedDescription }
    }

    private func reconcileDataActivity() {
        let reasons = scene.environmentReasons
        let paused = reasons.contains(.displaySleeping) || reasons.contains(.sessionInactive) || reasons.contains(.sessionUnknown)
        if preferences.sceneMode == "weather" {
            if paused { weather.stop() } else { weather.start() }
        }
        if paused || preferences.sceneMode != "custom" {
            cancelCustomPolling(); return
        }
        guard customTask == nil, let (config, saved) = customConnection else { return }
        let generation = customGeneration
        customTask = Task {
            // Deadlines and cached data belong to the connection, not to this polling task.
            // Waking or changing screens must neither bypass Retry-After nor extend cache life.
            rescheduleCustomExpiry(generation: generation)
            while !Task.isCancelled && generation == customGeneration {
                let remaining = customNextAllowedRefresh.timeIntervalSinceNow
                if remaining > 0 {
                    do { try await Task.sleep(nanoseconds: UInt64(min(86400, remaining) * 1_000_000_000)) }
                    catch { return }
                    continue // Re-read the deadline after wake/clock changes.
                }
                do {
                    let effect = try await CustomAPIClient().fetch(configuration: config, bearerToken: saved)
                    try Task.checkCancellation()
                    guard generation == customGeneration, preferences.sceneMode == "custom" else { return }
                    customLastGoodAt = Date(); customLastGoodEffect = effect; customFailureCount = 0
                    customNextAllowedRefresh = Date().addingTimeInterval(900)
                    rescheduleCustomExpiry(generation: generation)
                    notice = "自定义 API 已更新"
                } catch is CancellationError { return }
                catch {
                    guard !Task.isCancelled, generation == customGeneration else { return }
                    customFailureCount = min(4, customFailureCount + 1)
                    var delay = min(3600, 900 * pow(2, Double(customFailureCount - 1)))
                    if case DataServiceError.rateLimited(let retry) = error, retry.isFinite {
                        delay = max(delay, min(retry, 86400))
                    }
                    customNextAllowedRefresh = Date().addingTimeInterval(delay)
                    rescheduleCustomExpiry(generation: generation)
                    notice = customCacheIsFresh ? "自定义 API 暂不可用，保留近期画面" : "自定义 API 暂不可用，使用静态默认画面"
                }
            }
        }
    }

    private var customCacheIsFresh: Bool {
        guard let at = customLastGoodAt, customLastGoodEffect != nil else { return false }
        let age = Date().timeIntervalSince(at)
        return age >= 0 && age < customCacheLifetime
    }

    private func rescheduleCustomExpiry(generation: Int) {
        customExpiryTask?.cancel(); customExpiryTask = nil
        guard generation == customGeneration, preferences.sceneMode == "custom" else { return }
        guard customCacheIsFresh, let effect = customLastGoodEffect, let at = customLastGoodAt else {
            scene.update(nil); scene.setPaused(preferences.userPaused)
            return
        }
        scene.update(effect: effect); scene.setPaused(preferences.userPaused)
        let expiry = at.addingTimeInterval(customCacheLifetime)
        customExpiryTask = Task {
            // Expiry remains independent of a server-directed 24-hour retry delay.
            while !Task.isCancelled && generation == customGeneration {
                let remaining = expiry.timeIntervalSinceNow
                if remaining <= 0 { break }
                do { try await Task.sleep(nanoseconds: UInt64(min(customCacheLifetime, remaining) * 1_000_000_000)) }
                catch { return }
            }
            guard !Task.isCancelled, generation == customGeneration, preferences.sceneMode == "custom" else { return }
            scene.update(nil); scene.setPaused(preferences.userPaused)
            notice = "自定义 API 数据已过期，使用静态默认画面"
        }
    }

    private func cancelCustomPolling() {
        customGeneration += 1
        customTask?.cancel(); customTask = nil
        customExpiryTask?.cancel(); customExpiryTask = nil
        // Preserve connection timestamps/backoff across sleep, mode and screen changes.
    }

    func requestProposal() {
        guard !busy else { return }
        guard let configuration = preferences.provider else { selectedTab = "settings"; errorMessage = "请先配置模型服务和 API key。也可以直接使用本地渐变模板。"; return }
        guard let resources = Bundle.main.resourceURL?.appendingPathComponent("Authoring") else { return }
        let request = brief
        busy = true; creationStatus = "正在阅读制作规范并整理需求…"
        task = Task {
            defer { busy = false }
            do {
                let service = AgentService(credentials: credentials)
                let response = try await service.propose(request: request, configuration: configuration, resourcesURL: resources)
                guard !Task.isCancelled else { return }
                proposal = response
                if let values = response.parameters { seed = values.seed; motionCycles = values.motionCycles }
                creationStatus = "创作建议已准备好；检查后再生成"
            } catch is CancellationError { creationStatus = "已取消模型请求" }
            catch { creationStatus = "模型请求失败"; errorMessage = error.localizedDescription }
        }
    }

    func generateLocal() {
        guard !busy else { return }
        busy = true; creationStatus = "正在准备导出…"
        host.setAuthoringSuspended(true); scene.setAuthoringSuspended(true)
        let parameters = SoftGradientParameters(seed: seed, motionCycles: motionCycles)
        task = Task {
            defer { busy = false; host.setAuthoringSuspended(false); scene.setAuthoringSuspended(false) }
            do {
                let repository = try authoringWorkspace()
                let config = CreationConfiguration(repository: repository,
                    python: URL(fileURLWithPath: preferences.pythonPath),
                    ffmpeg: URL(fileURLWithPath: preferences.ffmpegPath),
                    ffprobe: URL(fileURLWithPath: preferences.ffprobePath))
                let bundle = try await creation.generate(parameters: parameters, configuration: config) { [weak self] progress in
                    Task { @MainActor in self?.creationStatus = String(describing: progress) }
                }
                try Task.checkCancellation()
                let asset = try await library.importBundle(at: bundle)
                assets = try library.list()
                creationStatus = "已生成并验证，回到素材库选择应用"
                notice = "新壁纸：\(asset.title)"; selectedTab = "library"
            } catch is CancellationError { creationStatus = "已取消创作；当前壁纸保持可用" }
            catch { creationStatus = "创作未完成"; errorMessage = error.localizedDescription }
        }
    }

    func cancelCreation() { task?.cancel(); creationStatus = "正在取消并清理本次任务…" }

    func editTemplate() {
        do {
            let repository = try authoringWorkspace()
            let project = try CreationService.copyTemplate(repository: repository, projectsDirectory: dataDirectory.appendingPathComponent("Projects"))
            notice = "可编辑副本：\(project.lastPathComponent)"
            Task {
                do { try await CreationService.openTemplate(url: project) }
                catch { errorMessage = error.localizedDescription; NSWorkspace.shared.activateFileViewerSelecting([project]) }
            }
        } catch { errorMessage = error.localizedDescription }
    }

    func storeProvider(kind: AgentProviderKind, endpoint: String, model: String, key: String) {
        do {
            guard let url = URL(string: endpoint), !model.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                throw StudioFailure.message("请填写有效的服务地址和模型名称")
            }
            _ = try credentialOrigin(url)
            let reference = "wallpaper-studio-" + kind.rawValue
            if !key.isEmpty { try credentials.save(secret: key, reference: reference, endpoint: url) }
            preferences.provider = AgentProviderConfiguration(kind: kind, endpoint: url, model: model, credentialReference: reference)
            save(); notice = "连接设置已保存；API key 保存在钥匙串中"
        } catch { errorMessage = error.localizedDescription }
    }

    func deleteProviderKey() {
        guard let reference = preferences.provider?.credentialReference else { return }
        do { try credentials.delete(reference: reference); preferences.provider = nil; save(); notice = "已移除模型凭据" }
        catch { errorMessage = error.localizedDescription }
    }

    func authoringWorkspace() throws -> URL {
        if !preferences.repositoryOverride.isEmpty { return URL(fileURLWithPath: preferences.repositoryOverride) }
        guard let resources = Bundle.main.resourceURL else { throw StudioFailure.message("应用缺少制作资源") }
        let versionData = try Data(contentsOf: resources.appendingPathComponent("authoring-version.json"))
        struct Version: Decodable { let id: String; let files: [String: String] }
        let version = try JSONDecoder().decode(Version.self, from: versionData)
        guard version.id.count == 64, version.id.allSatisfy(\.isHexDigit) else { throw StudioFailure.message("制作资源版本无效") }
        let source = resources.appendingPathComponent("Authoring")
        let parent = dataDirectory.appendingPathComponent("Authoring")
        let destination = parent.appendingPathComponent(version.id)
        if FileManager.default.fileExists(atPath: destination.path) { return destination }
        try FileManager.default.createDirectory(at: parent, withIntermediateDirectories: true)
        let staging = parent.appendingPathComponent(UUID().uuidString + ".partial")
        do {
            try FileManager.default.copyItem(at: source, to: staging)
            for (name, expected) in version.files {
                let data = try Data(contentsOf: staging.appendingPathComponent(name))
                let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
                guard digest == expected else { throw StudioFailure.message("制作资源校验失败：\(name)") }
            }
            try FileManager.default.moveItem(at: staging, to: destination)
        } catch { try? FileManager.default.removeItem(at: staging); throw error }
        return destination
    }

    func shutdown() { task?.cancel(); cancelCustomPolling(); weather.stop(); scene.stop(); host.shutdown() }
}
