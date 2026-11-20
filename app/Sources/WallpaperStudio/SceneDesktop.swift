import AppKit
import WallpaperCore

private final class SceneWindow: NSWindow {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

@MainActor
final class SceneDesktop: ObservableObject {
    @Published private(set) var status = "未启用实时场景"
    private var window: SceneWindow?
    private var view: NativeSceneView?
    private var monitor: LifecycleMonitor?
    private var observer: NSObjectProtocol?
    private var screenObserver: NSObjectProtocol?
    private var reasons = Set<PauseReason>()
    private var userPaused = false
    private var authoringSuspended = false
    private var chosenScreen: UInt32?
    private var latestSnapshot: WeatherSnapshot?
    private var latestEffect: NativeEffectState?
    private var active = false
    private var generation = 0
    var environmentReasons: Set<PauseReason> { reasons }
    var onEnvironmentChange: ((Set<PauseReason>) -> Void)?

    func start(screenID: UInt32?) {
        stop()
        active = true
        let generation = self.generation
        chosenScreen = screenID
        monitor = LifecycleMonitor { [weak self] reasons in
            guard let self, self.active, self.generation == generation else { return }
            self.reasons = reasons; self.reconcile(); self.onEnvironmentChange?(reasons)
        }
        monitor?.start()
        reasons = monitor?.reasons ?? []
        onEnvironmentChange?(reasons)
        screenObserver = NotificationCenter.default.addObserver(forName: NSApplication.didChangeScreenParametersNotification, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in
                guard let self, self.active, self.generation == generation else { return }
                self.createWindow()
            }
        }
        createWindow()
    }

    func update(_ snapshot: WeatherSnapshot?) { latestSnapshot = snapshot; latestEffect = nil; view?.apply(snapshot: snapshot) }
    func update(effect: NativeEffectState) { latestEffect = effect; view?.apply(effect: effect) }
    func setPaused(_ value: Bool) { userPaused = value; monitor?.refresh(); reconcile() }
    func setAuthoringSuspended(_ value: Bool) { authoringSuspended = value; reconcile() }

    private func createWindow() {
        guard active else { return }
        view?.setRunning(false)
        if let observer { NotificationCenter.default.removeObserver(observer) }
        window?.orderOut(nil); window?.close(); window = nil; view = nil
        let screen = chosenScreen.flatMap { id in NSScreen.screens.first { ($0.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.uint32Value == id } }
            ?? (chosenScreen == nil ? NSScreen.main : nil)
        guard let screen else { status = "所选显示器不可用"; return }
        let win = SceneWindow(contentRect: screen.frame, styleMask: .borderless, backing: .buffered, defer: false)
        win.isReleasedWhenClosed = false
        win.level = NSWindow.Level(rawValue: Int(CGWindowLevelForKey(.desktopWindow)) + 1)
        win.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle]
        win.ignoresMouseEvents = true; win.hasShadow = false; win.isOpaque = true
        win.hidesOnDeactivate = false; win.animationBehavior = .none
        let scene = NativeSceneView(frame: NSRect(origin: .zero, size: screen.frame.size))
        scene.autoresizingMask = [.width, .height]
        scene.apply(snapshot: latestSnapshot)
        if let latestEffect { scene.apply(effect: latestEffect) }
        win.contentView = scene
        window = win; view = scene
        observer = NotificationCenter.default.addObserver(forName: NSWindow.didChangeOcclusionStateNotification, object: win, queue: .main) { [weak self] _ in
            Task { @MainActor in self?.reconcile() }
        }
        win.orderFrontRegardless(); reconcile()
    }

    private func reconcile() {
        guard let window, let view else { return }
        var all = reasons
        if userPaused { all.insert(.user) }
        if authoringSuspended { all.insert(.authoring) }
        if let screen = window.screen,
           let id = (screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.uint32Value,
           CGDisplayIsAsleep(id) != 0 { all.insert(.displaySleeping) }
        if !window.occlusionState.contains(.visible) { all.insert(.occluded) }
        view.setRunning(all.isEmpty)
        status = all.isEmpty ? "原生场景运行中" : "静态场景 · " + all.map(\.rawValue).sorted().joined(separator: ", ")
    }

    func stop() {
        active = false
        generation += 1
        monitor?.stop(); monitor = nil
        if let observer { NotificationCenter.default.removeObserver(observer) }
        if let screenObserver { NotificationCenter.default.removeObserver(screenObserver) }
        observer = nil; screenObserver = nil
        view?.setRunning(false); window?.orderOut(nil); window?.close()
        window = nil; view = nil; status = "未启用实时场景"
    }
}
