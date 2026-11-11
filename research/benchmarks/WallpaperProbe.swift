// Compile-only research probe. See README.md before running the GUI.
import AppKit
import AVFoundation
import CoreGraphics
import Darwin

struct Phase {
    let state: String
    let seconds: Double
}

struct Options {
    var video: URL?
    var output: String?
    var phases = [Phase(state: "idle", seconds: 10), Phase(state: "play", seconds: 20),
                  Phase(state: "pause", seconds: 20), Phase(state: "play", seconds: 20)]
    var allowCover = false
    var pauseOnOcclusion = false

    static let usage = """
    WallpaperProbe --video LOCAL_FILE --output NEW_JSONL
      [--schedule idle:10,play:20,pause:20,play:20]
      [--cover] [--pause-on-occlusion]
    Total duration must be 1...120 seconds. States: idle, play, pause, cover, uncover.
    --cover permits cover/uncover phases; a desktop-level cover never covers normal app windows.
    Idle-only schedules need no --video. Output must not already exist. No GUI runs with --help.
    """

    static func parse() throws -> Options {
        var result = Options()
        var args = Array(CommandLine.arguments.dropFirst())
        while !args.isEmpty {
            let flag = args.removeFirst()
            switch flag {
            case "--video", "--output", "--schedule":
                guard !args.isEmpty else { throw ProbeError.message("Missing value for \(flag)") }
                let value = args.removeFirst()
                if flag == "--video" {
                    result.video = URL(fileURLWithPath: value).standardizedFileURL
                } else if flag == "--output" {
                    result.output = value
                } else {
                    result.phases = try value.split(separator: ",").map { part in
                        let pair = part.split(separator: ":")
                        guard pair.count == 2,
                              ["idle", "play", "pause", "cover", "uncover"].contains(String(pair[0])),
                              let seconds = Double(pair[1]), seconds.isFinite, seconds > 0 else {
                            throw ProbeError.message("Invalid schedule segment: \(part)")
                        }
                        return Phase(state: String(pair[0]), seconds: seconds)
                    }
                }
            case "--cover": result.allowCover = true
            case "--pause-on-occlusion": result.pauseOnOcclusion = true
            default: throw ProbeError.message("Unknown argument: \(flag)")
            }
        }
        let total = result.phases.reduce(0) { $0 + $1.seconds }
        guard !result.phases.isEmpty, (1...120).contains(total) else {
            throw ProbeError.message("Schedule total must be 1...120 seconds")
        }
        guard result.output != nil else { throw ProbeError.message("--output is required") }
        if result.phases.contains(where: { $0.state != "idle" }) {
            guard let url = result.video, FileManager.default.isReadableFile(atPath: url.path) else {
                throw ProbeError.message("Playback requires a readable local --video file")
            }
        }
        if result.phases.contains(where: { ["cover", "uncover"].contains($0.state) }) && !result.allowCover {
            throw ProbeError.message("Cover/uncover phases require explicit --cover")
        }
        return result
    }
}

enum ProbeError: Error, CustomStringConvertible {
    case message(String)
    var description: String { if case let .message(message) = self { return message }; return "error" }
}

final class PassiveWindow: NSWindow {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

func processMetrics() -> [String: Any] {
    var usage = rusage()
    let usageResult = getrusage(RUSAGE_SELF, &usage)
    var vm = task_vm_info_data_t()
    var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<integer_t>.size)
    let vmResult = withUnsafeMutablePointer(to: &vm) { pointer in
        pointer.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
            task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count)
        }
    }
    var result: [String: Any] = ["rusage_result": usageResult, "vm_result": vmResult]
    if usageResult == 0 {
        result["cpu_seconds"] = Double(usage.ru_utime.tv_sec + usage.ru_stime.tv_sec)
            + Double(usage.ru_utime.tv_usec + usage.ru_stime.tv_usec) / 1_000_000
    }
    if vmResult == KERN_SUCCESS {
        result["physical_footprint_bytes"] = vm.phys_footprint
        result["resident_bytes"] = vm.resident_size
        result["compressed_bytes"] = vm.compressed
    }
    return result
}

@MainActor
final class Probe: NSObject, NSApplicationDelegate {
    let options: Options
    let output: FileHandle
    var started = ProcessInfo.processInfo.systemUptime
    var timer: Timer?
    var phaseIndex = -1
    var phaseName = "starting"
    var window: PassiveWindow?
    var cover: PassiveWindow?
    var player: AVQueuePlayer?
    var looper: AVPlayerLooper?
    var playerLayer: AVPlayerLayer?
    var wantsPlayback = false
    var lastAppliedPlay: Bool?
    var displaySleeping = false
    var lastSampleTime = -Double.infinity
    var lastCPUTime: Double?
    var lastCPUWall: Double?
    var finished = false
    var signalSources: [DispatchSourceSignal] = []

    init(options: Options, output: FileHandle) {
        self.options = options
        self.output = output
    }

    func emit(_ values: [String: Any]) {
        var object = values
        object["elapsed_seconds"] = ProcessInfo.processInfo.systemUptime - started
        object["pid"] = ProcessInfo.processInfo.processIdentifier
        guard let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]) else { return }
        output.write(data)
        output.write(Data([10]))
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        started = ProcessInfo.processInfo.systemUptime
        var metadata: [String: Any] = [
            "event": "start", "schedule": options.phases.map { ["state": $0.state, "seconds": $0.seconds] },
            "pause_on_occlusion": options.pauseOnOcclusion, "cover_allowed": options.allowCover,
            "cpu_percent_basis": "one logical core = 100%; process only",
            "memory_basis": "Mach TASK_VM_INFO physical footprint and resident size; process only",
            "gpu_measurement": "not collected", "os": ProcessInfo.processInfo.operatingSystemVersionString,
            "video_path": options.video?.path ?? NSNull(), "logical_cpu_count": ProcessInfo.processInfo.processorCount,
            "screen_count": NSScreen.screens.count,
            "screens": NSScreen.screens.map { screen -> [String: Any] in
                let displayID = (screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.uint32Value ?? 0
                let displayMode = CGDisplayCopyDisplayMode(displayID)
                return ["width_points": screen.frame.width, "height_points": screen.frame.height,
                        "backing_scale": screen.backingScaleFactor, "display_id": displayID,
                        "pixel_width": displayMode?.pixelWidth ?? 0, "pixel_height": displayMode?.pixelHeight ?? 0,
                        "cg_display_width": CGDisplayPixelsWide(displayID), "cg_display_height": CGDisplayPixelsHigh(displayID),
                        "refresh_rate_hz": displayMode?.refreshRate ?? 0]
            }
        ]
        if let screen = NSScreen.main ?? NSScreen.screens.first {
            metadata["screen_width_points"] = screen.frame.width
            metadata["screen_height_points"] = screen.frame.height
            metadata["screen_backing_scale"] = screen.backingScaleFactor
        }
        emit(metadata)
        NotificationCenter.default.addObserver(self, selector: #selector(occlusionChanged),
            name: NSWindow.didChangeOcclusionStateNotification, object: nil)
        NSWorkspace.shared.notificationCenter.addObserver(self, selector: #selector(screenSleep),
            name: NSWorkspace.screensDidSleepNotification, object: nil)
        NSWorkspace.shared.notificationCenter.addObserver(self, selector: #selector(screenWake),
            name: NSWorkspace.screensDidWakeNotification, object: nil)
        for sig in [SIGINT, SIGTERM] {
            signal(sig, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            source.setEventHandler { [weak self] in self?.finish(reason: "signal_\(sig)", exitCode: 128 + sig) }
            source.resume()
            signalSources.append(source)
        }
        timer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.tick() }
        }
        tick()
    }

    func makeWindow(level: Int) throws -> PassiveWindow {
        guard let screen = NSScreen.main ?? NSScreen.screens.first else {
            throw ProbeError.message("No available screen")
        }
        let result = PassiveWindow(contentRect: screen.frame, styleMask: .borderless,
            backing: .buffered, defer: false)
        result.isReleasedWhenClosed = false
        result.level = NSWindow.Level(rawValue: level)
        result.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle]
        result.ignoresMouseEvents = true
        result.hidesOnDeactivate = false
        result.isOpaque = true
        result.backgroundColor = .black
        result.hasShadow = false
        result.animationBehavior = .none
        return result
    }

    func preparePlayer() throws {
        guard player == nil, let url = options.video else { return }
        let result = try makeWindow(level: Int(CGWindowLevelForKey(.desktopWindow)) + 1)
        let view = NSView(frame: NSRect(origin: .zero, size: result.frame.size))
        view.wantsLayer = true
        let queue = AVQueuePlayer()
        queue.isMuted = true
        queue.allowsExternalPlayback = false
        queue.preventsDisplaySleepDuringVideoPlayback = false
        let layer = AVPlayerLayer(player: queue)
        layer.videoGravity = .resizeAspectFill
        layer.frame = view.bounds
        layer.autoresizingMask = [.layerWidthSizable, .layerHeightSizable]
        view.layer?.addSublayer(layer)
        result.contentView = view
        player = queue
        playerLayer = layer
        window = result
        looper = AVPlayerLooper(player: queue, templateItem: AVPlayerItem(url: url))
        result.orderFrontRegardless()
        lastAppliedPlay = nil
    }

    func teardownPlayer() {
        player?.pause()
        playerLayer?.player = nil
        looper?.disableLooping()
        player?.removeAllItems()
        cover?.orderOut(nil)
        cover?.close()
        cover = nil
        window?.orderOut(nil)
        window?.close()
        window = nil
        playerLayer = nil
        looper = nil
        player = nil
        lastAppliedPlay = nil
    }

    func applyPhase(_ phase: Phase) throws {
        phaseName = phase.state
        if phase.state == "idle" {
            wantsPlayback = false
            teardownPlayer()
        } else {
            try preparePlayer()
            wantsPlayback = phase.state != "pause"
            if phase.state == "cover" {
                if cover == nil { cover = try makeWindow(level: Int(CGWindowLevelForKey(.desktopWindow)) + 2) }
                cover?.orderFrontRegardless()
            } else {
                cover?.orderOut(nil)
            }
            updatePlayback()
        }
        emit(["event": "phase", "phase_index": phaseIndex, "phase": phaseName,
              "scheduled_seconds": phase.seconds])
    }

    func updatePlayback() {
        let occluded = !(window?.occlusionState.contains(.visible) ?? false)
        let shouldPlay = wantsPlayback && !displaySleeping && !(options.pauseOnOcclusion && occluded)
        if shouldPlay != lastAppliedPlay {
            if shouldPlay { player?.play() } else { player?.pause() }
            lastAppliedPlay = shouldPlay
        }
    }

    @objc func occlusionChanged(_ notification: Notification) {
        guard let changed = notification.object as? NSWindow, changed === window else { return }
        emit(["event": "occlusion", "phase": phaseName,
              "window_visible": changed.occlusionState.contains(.visible)])
        updatePlayback()
    }

    @objc func screenSleep(_ notification: Notification) {
        displaySleeping = true
        emit(["event": "display_sleep"])
        updatePlayback()
    }

    @objc func screenWake(_ notification: Notification) {
        displaySleeping = false
        emit(["event": "display_wake"])
        updatePlayback()
    }

    func sample(at elapsed: Double) {
        var metrics = processMetrics()
        if let cpu = metrics["cpu_seconds"] as? Double {
            if let previousCPU = lastCPUTime, let previousWall = lastCPUWall, elapsed > previousWall {
                metrics["cpu_percent_one_core"] = max(0, 100 * (cpu - previousCPU) / (elapsed - previousWall))
            }
            lastCPUTime = cpu
            lastCPUWall = elapsed
        }
        metrics["event"] = "sample"
        metrics["phase"] = phaseName
        metrics["phase_index"] = phaseIndex
        metrics["window_visible"] = window?.occlusionState.contains(.visible) ?? false
        metrics["cover_ordered"] = cover?.isVisible ?? false
        metrics["layer_ready"] = playerLayer?.isReadyForDisplay ?? false
        metrics["player_rate"] = player?.rate ?? 0
        metrics["player_status"] = player?.status.rawValue ?? -1
        metrics["time_control_status"] = player?.timeControlStatus.rawValue ?? -1
        metrics["looper_status"] = looper?.status.rawValue ?? -1
        metrics["loop_count"] = looper?.loopCount ?? 0
        if let seconds = player?.currentTime().seconds, seconds.isFinite { metrics["media_seconds"] = seconds }
        if let error = player?.error ?? player?.currentItem?.error ?? looper?.error {
            metrics["playback_error"] = error.localizedDescription
        }
        emit(metrics)
    }

    func tick() {
        guard !finished else { return }
        let elapsed = ProcessInfo.processInfo.systemUptime - started
        var boundary = 0.0
        var target: Int?
        for (index, phase) in options.phases.enumerated() {
            boundary += phase.seconds
            if elapsed < boundary { target = index; break }
        }
        guard let target else { finish(reason: "schedule_complete"); return }
        if target != phaseIndex {
            phaseIndex = target
            do { try applyPhase(options.phases[target]) }
            catch { emit(["event": "error", "message": String(describing: error)]); finish(reason: "error", exitCode: 3); return }
        }
        updatePlayback()
        if let error = player?.error ?? player?.currentItem?.error ?? looper?.error {
            emit(["event": "error", "message": error.localizedDescription])
            finish(reason: "playback_failed", exitCode: 3)
            return
        }
        if elapsed - lastSampleTime >= 1 {
            sample(at: elapsed)
            lastSampleTime = elapsed
        }
    }

    func finish(reason: String, exitCode: Int32 = 0) {
        guard !finished else { return }
        finished = true
        timer?.invalidate()
        timer = nil
        sample(at: ProcessInfo.processInfo.systemUptime - started)
        emit(["event": "finish", "reason": reason])
        teardownPlayer()
        NotificationCenter.default.removeObserver(self)
        NSWorkspace.shared.notificationCenter.removeObserver(self)
        try? output.synchronize()
        // All resources are explicitly cleaned above; preserve failure status for the wrapper.
        Darwin.exit(exitCode)
    }

    func applicationWillTerminate(_ notification: Notification) {
        teardownPlayer()
        NotificationCenter.default.removeObserver(self)
        NSWorkspace.shared.notificationCenter.removeObserver(self)
        try? output.synchronize()
    }
}

if CommandLine.arguments.contains("--help") {
    print(Options.usage)
    exit(0)
}
do {
    let options = try Options.parse()
    let fd = open(options.output!, O_WRONLY | O_CREAT | O_EXCL, S_IRUSR | S_IWUSR)
    guard fd >= 0 else { throw ProbeError.message("Cannot create new output: \(String(cString: strerror(errno)))") }
    let output = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
    MainActor.assumeIsolated {
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let delegate = Probe(options: options, output: output)
        app.delegate = delegate
        withExtendedLifetime(delegate) { app.run() }
    }
} catch {
    FileHandle.standardError.write(Data("\(error)\n\(Options.usage)\n".utf8))
    exit(2)
}
