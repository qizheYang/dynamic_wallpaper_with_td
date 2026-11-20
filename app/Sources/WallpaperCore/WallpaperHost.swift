import AppKit
import AVFoundation
import Combine
import CoreGraphics
import ImageIO

public struct WallpaperScreen: Identifiable, Equatable, Sendable {
    public let id: CGDirectDisplayID
    public let name: String
    public init(id: CGDirectDisplayID, name: String) { self.id = id; self.name = name }
}

private final class DesktopWindow: NSWindow {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

/// One selected-screen player. This host never launches TD, shells out, changes the desktop
/// preference, or claims a lock signal. Call shutdown() from application termination.
@MainActor
public final class WallpaperHost: NSObject, ObservableObject {
    @Published public private(set) var status = "No wallpaper selected"
    @Published public private(set) var pauseReasons: Set<PauseReason> = []
    @Published public private(set) var currentAsset: WallpaperAsset?
    @Published public private(set) var availableScreens: [WallpaperScreen] = []
    @Published public private(set) var selectedScreenID: CGDirectDisplayID?
    private var policy = PlaybackPolicy()
    private let eventSink: ((PlaybackEvent) -> Void)?
    private let diagnosticAllowBatteryPlayback: Bool
    private var lifecycle: LifecycleMonitor!
    private var environmentReasons: Set<PauseReason> = []
    private var userPaused = false
    private var authoringSuspended = false
    private var window: DesktopWindow?
    private var coverLayer: CALayer?
    private var player: AVQueuePlayer?
    private var looper: AVPlayerLooper?
    private var playerLayer: AVPlayerLayer?
    private var failureObservation: NSKeyValueObservation?
    private var readyObservation: NSKeyValueObservation?
    private var itemFailureObserver: NSObjectProtocol?
    private var loadTask: Task<Void, Never>?
    private var loadingAsset: AVURLAsset?
    private var loadTimeoutTask: Task<Void, Never>?
    private var releaseTask: Task<Void, Never>?
    private var generation: UInt64 = 0
    private var isShutdown = false
    private var updating = false
    private var screenSelectionMade = false
    private var failureMessage: String?

    public init(eventSink: ((PlaybackEvent) -> Void)? = nil, diagnosticAllowBatteryPlayback: Bool = false) {
        self.eventSink = eventSink
        self.diagnosticAllowBatteryPlayback = diagnosticAllowBatteryPlayback
        super.init()
        lifecycle = LifecycleMonitor { [weak self] reasons in
            guard let self else { return }
            self.environmentReasons = reasons
            self.updatePlayback()
            self.emit("environment")
        }
        NotificationCenter.default.addObserver(self, selector: #selector(occlusionChanged),
            name: NSWindow.didChangeOcclusionStateNotification, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(screensChanged),
            name: NSApplication.didChangeScreenParametersNotification, object: nil)
        refreshScreens()
        lifecycle.start()
        environmentReasons = lifecycle.reasons
        updatePlayback()
    }

    public var diagnostics: [String: Any] {
        let time = player?.currentTime().seconds
        return ["status": status, "assetID": currentAsset?.id ?? NSNull(),
                "pauseReasons": pauseReasons.map(\.rawValue).sorted(),
                "observedReasons": observedPauseReasons.map(\.rawValue).sorted(),
                "diagnosticAllowBatteryPlayback": diagnosticAllowBatteryPlayback,
                "playerLoaded": player != nil, "loadPending": loadTask != nil,
                "rate": player?.rate ?? 0, "mediaSeconds": time.flatMap { $0.isFinite ? $0 : nil } ?? 0,
                "layerReady": playerLayer?.isReadyForDisplay ?? false,
                "loopCount": looper?.loopCount ?? 0,
                "windowVisible": window?.occlusionState.contains(.visible) ?? false,
                "screenID": selectedScreenID.map { NSNumber(value: $0) } ?? NSNull(),
                "loadGeneration": generation,
                "sessionSignalScope": "console/login + workspace activation; not lock detection"]
    }

    public func apply(_ asset: WallpaperAsset) {
        guard !isShutdown else { return }
        invalidateLoad()
        releasePlayer()
        currentAsset = asset
        failureMessage = nil
        if !screenSelectionMade { refreshScreens() }
        ensureWindow()
        updateCover()
        lifecycle.refresh()
        updatePlayback()
        emit("asset_applied")
    }

    public func setUserPaused(_ paused: Bool) {
        guard !isShutdown else { return }
        userPaused = paused
        if !paused { lifecycle.refresh() }
        updatePlayback()
        emit(paused ? "user_pause" : "user_resume")
    }

    /// Authoring may suspend animation without changing the user's independent pause choice.
    public func setAuthoringSuspended(_ value: Bool) {
        guard !isShutdown else { return }
        authoringSuspended = value
        if !value { lifecycle.refresh() }
        updatePlayback()
        emit(value ? "authoring_suspend" : "authoring_resume")
    }

    /// Nil explicitly chooses the current main screen; unplugging a selected display does not
    /// silently transfer playback to a different display.
    public func selectScreen(id: CGDirectDisplayID?) {
        guard !isShutdown else { return }
        screenSelectionMade = true
        selectedScreenID = id ?? Self.id(of: NSScreen.main ?? NSScreen.screens.first)
        invalidateLoad()
        releasePlayer()
        closeWindow()
        ensureWindow()
        updateCover()
        updatePlayback()
        emit("screen_selected")
    }

    public func stop() {
        invalidateLoad()
        releasePlayer()
        closeWindow()
        currentAsset = nil
        failureMessage = nil
        updatePlayback()
        emit("stopped")
    }

    public func shutdown() {
        guard !isShutdown else { return }
        stop()
        isShutdown = true
        lifecycle.stop()
        NotificationCenter.default.removeObserver(self)
        status = "Stopped"
        emit("shutdown")
    }

    private static func id(of screen: NSScreen?) -> CGDirectDisplayID? {
        (screen?.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.uint32Value
    }
    private var selectedScreen: NSScreen? {
        NSScreen.screens.first { Self.id(of: $0) == selectedScreenID }
    }
    private func refreshScreens() {
        availableScreens = NSScreen.screens.compactMap { screen in
            Self.id(of: screen).map { WallpaperScreen(id: $0, name: screen.localizedName) }
        }
        if !screenSelectionMade {
            selectedScreenID = Self.id(of: NSScreen.main ?? NSScreen.screens.first)
            if selectedScreenID != nil { screenSelectionMade = true }
        }
    }

    private func ensureWindow() {
        guard window == nil, currentAsset != nil, let screen = selectedScreen else { return }
        let result = DesktopWindow(contentRect: screen.frame, styleMask: .borderless,
                                   backing: .buffered, defer: false)
        result.isReleasedWhenClosed = false
        result.level = NSWindow.Level(rawValue: Int(CGWindowLevelForKey(.desktopWindow)) + 1)
        result.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle]
        result.ignoresMouseEvents = true
        result.hidesOnDeactivate = false
        result.isOpaque = true
        result.backgroundColor = .black
        result.hasShadow = false
        result.animationBehavior = .none
        let view = NSView(frame: NSRect(origin: .zero, size: screen.frame.size))
        view.wantsLayer = true
        let cover = CALayer()
        cover.frame = view.bounds
        cover.autoresizingMask = [.layerWidthSizable, .layerHeightSizable]
        cover.contentsGravity = .resizeAspectFill
        view.layer?.addSublayer(cover)
        result.contentView = view
        window = result
        coverLayer = cover
        updateCover()
        result.orderFrontRegardless()
    }

    private func updateCover() {
        guard let url = currentAsset?.coverURL else { coverLayer?.contents = nil; return }
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
            coverLayer?.contents = nil
            fail("Static cover is missing or invalid.")
            return
        }
        coverLayer?.contents = image
    }

    private func updatePlayback() {
        guard !isShutdown, !updating else { return }
        updating = true
        defer { updating = false }
        var reasons = observedPauseReasons
        if diagnosticAllowBatteryPlayback { reasons.remove(.onBattery) }
        pauseReasons = reasons
        policy.reasons = reasons
        policy.assetAvailable = currentAsset != nil
        policy.screenAvailable = selectedScreen != nil && window != nil
        policy.failed = failureMessage != nil
        if currentAsset == nil { status = "No wallpaper selected"; return }
        if let failureMessage { status = failureMessage; return }
        if !policy.screenAvailable {
            invalidateLoad(); releasePlayer(); status = "Selected display unavailable"; return
        }
        if !policy.permitsPlayback {
            if loadTask != nil { invalidateLoad() }
            loadTimeoutTask?.cancel(); loadTimeoutTask = nil
            player?.pause()
            status = "Static · " + reasons.sorted { $0.rawValue < $1.rawValue }.map(\.label).joined(separator: ", ")
            scheduleRelease()
            return
        }
        releaseTask?.cancel(); releaseTask = nil
        if let player {
            player.play()
            status = playerLayer?.isReadyForDisplay == true ? "Playing" : "Preparing video"
            if playerLayer?.isReadyForDisplay != true, loadTimeoutTask == nil {
                let request = generation
                loadTimeoutTask = Task { [weak self] in
                    do { try await Task.sleep(nanoseconds: 20_000_000_000) } catch { return }
                    guard let self, self.generation == request, self.policy.permitsPlayback,
                          self.playerLayer?.isReadyForDisplay != true else { return }
                    self.fail("Video did not produce a frame. The static cover is retained.")
                }
            }
        } else if loadTask == nil { startLoad() }
    }

    private func startLoad() {
        guard let asset = currentAsset else { return }
        generation &+= 1
        let request = generation
        status = "Loading video"
        let media = AVURLAsset(url: asset.videoURL)
        loadingAsset = media
        loadTimeoutTask = Task { [weak self] in
            do { try await Task.sleep(nanoseconds: 20_000_000_000) } catch { return }
            guard let self, self.generation == request, self.loadTask != nil else { return }
            self.fail("Video loading timed out. The static cover is retained.")
        }
        loadTask = Task { [weak self] in
            do {
                let playable = try await media.load(.isPlayable)
                let duration = try await media.load(.duration)
                let tracks = try await media.loadTracks(withMediaType: .video)
                guard playable, duration.seconds.isFinite, duration.seconds > 0, duration.seconds <= 600,
                      let track = tracks.first else { throw AssetLibraryError.invalid("Video is not playable.") }
                let size = try await track.load(.naturalSize)
                guard size.width > 0, size.height > 0, size.width <= 4096, size.height <= 2160 else {
                    throw AssetLibraryError.invalid("Video dimensions are unsupported.")
                }
                try Task.checkCancellation()
                guard let self, self.generation == request, self.currentAsset?.id == asset.id,
                      self.policy.permitsPlayback, !self.isShutdown else { return }
                self.loadTask = nil
                self.loadingAsset = nil
                self.loadTimeoutTask?.cancel(); self.loadTimeoutTask = nil
                self.installPlayer(media, generation: request)
            } catch {
                guard let self, self.generation == request, !Task.isCancelled, !self.isShutdown else { return }
                self.loadTask = nil
                self.fail("Unable to load video: \(error.localizedDescription)")
            }
        }
    }

    private var observedPauseReasons: Set<PauseReason> {
        var reasons = environmentReasons
        if userPaused { reasons.insert(.user) }
        if authoringSuspended { reasons.insert(.authoring) }
        if let window, !window.occlusionState.contains(.visible) { reasons.insert(.occluded) }
        if let screenID = selectedScreenID, CGDisplayIsAsleep(screenID) != 0 { reasons.insert(.displaySleeping) }
        return reasons
    }

    private func installPlayer(_ asset: AVAsset, generation request: UInt64) {
        guard let view = window?.contentView else { return }
        let queue = AVQueuePlayer()
        queue.isMuted = true
        queue.allowsExternalPlayback = false
        queue.preventsDisplaySleepDuringVideoPlayback = false
        let layer = AVPlayerLayer(player: queue)
        layer.videoGravity = .resizeAspectFill
        layer.frame = view.bounds
        layer.autoresizingMask = [.layerWidthSizable, .layerHeightSizable]
        layer.isHidden = true // Show the static cover until the first frame is actually ready.
        view.layer?.addSublayer(layer)
        player = queue; playerLayer = layer
        let loop = AVPlayerLooper(player: queue, templateItem: AVPlayerItem(asset: asset))
        looper = loop
        failureObservation = loop.observe(\.status, options: [.initial, .new]) { [weak self, weak loop] _, _ in
            Task { @MainActor [weak self, weak loop] in
                guard let self, self.generation == request, loop?.status == .failed else { return }
                self.fail("Playback failed: \(loop?.error?.localizedDescription ?? "unknown media error")")
            }
        }
        readyObservation = layer.observe(\.isReadyForDisplay, options: [.initial, .new]) { [weak self] _, _ in
            Task { @MainActor [weak self] in
                guard let self, self.generation == request, self.playerLayer?.isReadyForDisplay == true else { return }
                self.playerLayer?.isHidden = false
                self.loadTimeoutTask?.cancel(); self.loadTimeoutTask = nil
                self.updatePlayback()
                self.emit("first_frame_ready")
            }
        }
        itemFailureObserver = NotificationCenter.default.addObserver(forName: AVPlayerItem.failedToPlayToEndTimeNotification,
            object: nil, queue: .main) { [weak self, weak queue] notification in
                MainActor.assumeIsolated {
                    guard let self, self.generation == request,
                          let item = notification.object as? AVPlayerItem,
                          queue?.items().contains(where: { $0 === item }) == true else { return }
                    self.fail("Video playback failed.")
                }
            }
        updatePlayback()
        emit("player_loaded")
    }

    private func scheduleRelease() {
        guard player != nil, releaseTask == nil else { return }
        let request = generation
        releaseTask = Task { [weak self] in
            do { try await Task.sleep(nanoseconds: 30_000_000_000) } catch { return }
            guard let self, self.generation == request, !self.policy.permitsPlayback else { return }
            self.releaseTask = nil
            self.invalidateLoad()
            self.releasePlayer()
            self.emit("long_pause_released")
        }
    }

    private func invalidateLoad() {
        generation &+= 1
        loadTask?.cancel(); loadTask = nil
        loadingAsset?.cancelLoading(); loadingAsset = nil
        loadTimeoutTask?.cancel(); loadTimeoutTask = nil
    }
    private func releasePlayer() {
        releaseTask?.cancel(); releaseTask = nil
        failureObservation?.invalidate(); failureObservation = nil
        readyObservation?.invalidate(); readyObservation = nil
        if let itemFailureObserver { NotificationCenter.default.removeObserver(itemFailureObserver) }
        itemFailureObserver = nil
        player?.pause()
        playerLayer?.player = nil
        playerLayer?.removeFromSuperlayer()
        looper?.disableLooping()
        player?.removeAllItems()
        playerLayer = nil; looper = nil; player = nil
    }
    private func closeWindow() {
        let old = window
        window = nil; coverLayer = nil
        old?.orderOut(nil); old?.close()
    }
    private func fail(_ message: String) {
        failureMessage = message
        invalidateLoad(); releasePlayer()
        updatePlayback()
        emit("failure", ["message": message])
    }
    private func emit(_ name: String, _ details: [String: String] = [:]) {
        var values = details
        values["status"] = status
        values["pause_reasons"] = pauseReasons.map(\.rawValue).sorted().joined(separator: ",")
        values["asset_id"] = currentAsset?.id
        eventSink?(PlaybackEvent(name: name, details: values))
    }
    @objc private func occlusionChanged(_ notification: Notification) {
        guard let changed = notification.object as? NSWindow, changed === window else { return }
        if changed.occlusionState.contains(.visible) { lifecycle.refresh() }
        updatePlayback()
        emit("occlusion")
    }
    @objc private func screensChanged(_ notification: Notification) {
        refreshScreens()
        if let screen = selectedScreen {
            if let window { window.setFrame(screen.frame, display: true) }
            else { ensureWindow(); updateCover() }
        } else {
            invalidateLoad(); releasePlayer(); closeWindow()
        }
        lifecycle.refresh()
        updatePlayback()
        emit("screens_changed")
    }
}
