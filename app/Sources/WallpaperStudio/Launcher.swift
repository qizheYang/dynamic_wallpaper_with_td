import AppKit
import SwiftUI
import WallpaperCore

@main
struct Launcher {
    static func main() {
        do {
            let options = try StudioOptions.parse(Array(CommandLine.arguments.dropFirst()))
            if options.help {
                print("""
                WallpaperStudio [--data-dir DIRECTORY] [--no-window]
                Diagnostics: --smoke-test COMPLETE_BUNDLE --data-dir ISOLATED_DIRECTORY
                             --event-log NEW_JSONL [--duration 90] [--no-window]
                Weather: --weather-smoke --data-dir ISOLATED_DIRECTORY --event-log NEW_JSONL
                Duration is 15...3600 seconds; smoke runs exit automatically.
                --diagnostic-battery-playback permits video smoke playback on battery for <=120s;
                normal launches always keep the default static-on-battery policy.
                Ordinary startup never launches TD/encoders/model requests. No login settings are changed.
                """)
                return
            }
            let application = NSApplication.shared
            application.setActivationPolicy(.accessory)
            let delegate = try StudioDelegate(options: options)
            application.delegate = delegate
            withExtendedLifetime(delegate) { application.run() }
        } catch {
            FileHandle.standardError.write(Data((error.localizedDescription + "\n").utf8))
            Darwin.exit(2)
        }
    }
}

@MainActor
final class StudioDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    let options: StudioOptions
    let model: StudioModel
    private let diagnostic: DiagnosticLog?
    private var window: NSWindow?
    private var statusItem: NSStatusItem?
    private var sampleTimer: Timer?
    private var signalSources: [DispatchSourceSignal] = []
    private var task: Task<Void, Never>?
    private var smokeStarted: TimeInterval?
    private var smokeStage = -1
    private var quitStarted = false

    init(options: StudioOptions) throws {
        self.options = options
        let log = try options.eventLog.map { try DiagnosticLog(url: $0) }
        diagnostic = log
        let data = options.dataDirectory ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("Wallpaper Studio")
        model = try StudioModel(dataDirectory: data, diagnosticAllowBatteryPlayback: options.diagnosticBatteryPlayback) { event in
            log?.write(["event": "playback_event", "detail": String(describing: event)])
        }
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        installMenu()
        for number in [SIGINT, SIGTERM] {
            signal(number, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: number, queue: .main)
            source.setEventHandler { [weak self] in self?.quit() }
            source.resume(); signalSources.append(source)
        }
        diagnostic?.write(["event": "launch", "os": ProcessInfo.processInfo.operatingSystemVersionString,
                           "authoring_processes_requested": false, "diagnostic_battery_override": options.diagnosticBatteryPlayback])
        if options.showWindow { showWindow() }
        task = Task {
            await model.launch(restore: options.smokeBundle == nil && !options.weatherSmoke)
            if let bundle = options.smokeBundle {
                await model.importBundle(bundle, apply: true)
                smokeStarted = ProcessInfo.processInfo.systemUptime
            } else if options.weatherSmoke {
                model.activateWeather(); smokeStarted = ProcessInfo.processInfo.systemUptime
            }
            if diagnostic != nil {
                sampleTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
                    Task { @MainActor in self?.sample() }
                }
                sampleTimer?.tolerance = 0.1
            }
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard model.busy else { return .terminateNow }
        model.cancelCreation()
        Task { [weak self] in
            for _ in 0..<600 {
                guard let self, self.model.busy else { break }
                try? await Task.sleep(nanoseconds: 100_000_000)
            }
            guard let self else { NSApp.reply(toApplicationShouldTerminate: true); return }
            let ready = !self.model.busy
            if !ready {
                self.quitStarted = false
                self.model.errorMessage = "创作进程仍在清理，暂时保留应用。请稍后再次退出。"
            }
            NSApp.reply(toApplicationShouldTerminate: ready)
        }
        return .terminateLater
    }
    func applicationWillTerminate(_ notification: Notification) {
        task?.cancel(); sampleTimer?.invalidate(); model.shutdown()
        diagnostic?.write(["event": "terminated"])
        signalSources.forEach { $0.cancel() }
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { showWindow(); return true }

    @objc func showWindow() {
        if window == nil {
            let win = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1050, height: 740),
                               styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
            win.title = "Wallpaper Studio"; win.isReleasedWhenClosed = false
            win.contentView = NSHostingView(rootView: StudioView(model: model))
            win.center(); window = win
        }
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
    @objc private func togglePause() { model.pauseToggle() }
    @objc private func stopWallpaper() { model.stop() }
    @objc private func importWallpaper() { showWindow(); model.chooseBundle() }
    @objc private func quit() {
        guard !quitStarted else { return }
        quitStarted = true
        diagnostic?.write(["event": "finish", "reason": "requested_exit", "diagnostics": model.host.diagnostics])
        NSApp.terminate(nil)
    }

    private func installMenu() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        item.button?.image = NSImage(systemSymbolName: "water.waves", accessibilityDescription: "Wallpaper Studio")
        let menu = NSMenu(); menu.delegate = self
        for (title, action, key) in [("打开 Wallpaper Studio", #selector(showWindow), ""),
                                      ("导入壁纸…", #selector(importWallpaper), ""),
                                      ("暂停 / 继续", #selector(togglePause), ""),
                                      ("停止壁纸", #selector(stopWallpaper), ""),
                                      ("退出", #selector(quit), "q")] {
            let entry = NSMenuItem(title: title, action: action, keyEquivalent: key); entry.target = self
            menu.addItem(entry)
        }
        item.menu = menu; statusItem = item
    }

    private func sample() {
        guard let diagnostic else { return }
        var record = diagnostic.metrics()
        record["event"] = "sample"; record["diagnostics"] = model.host.diagnostics
        record["scene_status"] = model.scene.status; record["weather_status"] = model.weather.statusText
        record["error"] = model.errorMessage ?? NSNull()
        diagnostic.write(record)
        guard let start = smokeStarted else { return }
        let elapsed = ProcessInfo.processInfo.systemUptime - start
        if elapsed >= options.duration { quit(); return }
        if options.smokeBundle != nil {
            // A long pause exercises decoder release, followed by normal resume.
            // Sustained mode repeats 80s play / 40s pause, never fakes lock events.
            let stage: Int
            if options.duration >= 120 { stage = Int(elapsed.truncatingRemainder(dividingBy: 120)) >= 80 ? 1 : 0 }
            else { stage = elapsed < 25 ? 0 : elapsed < 65 ? 1 : 0 }
            if stage != smokeStage {
                smokeStage = stage
                model.preferences.userPaused = stage == 1
                model.host.setUserPaused(stage == 1)
                diagnostic.write(["event": "smoke_phase", "user_paused": stage == 1])
            }
        }
    }
}
