import AppKit
import CoreGraphics
import IOKit.ps

/// Documented lifecycle signals only. Session activity and occlusion do not identify locking.
@MainActor
public final class LifecycleMonitor: NSObject {
    public private(set) var reasons: Set<PauseReason> = []
    private let onChange: (Set<PauseReason>) -> Void
    private var running = false
    private var sleeping = false
    private var sessionResigned = false
    private var powerSource: CFRunLoopSource?

    public init(onChange: @escaping (Set<PauseReason>) -> Void) {
        self.onChange = onChange
        super.init()
    }

    public func start() {
        guard !running else { return }
        running = true
        let workspace = NSWorkspace.shared.notificationCenter
        workspace.addObserver(self, selector: #selector(displaySleep), name: NSWorkspace.screensDidSleepNotification, object: nil)
        workspace.addObserver(self, selector: #selector(displayWake), name: NSWorkspace.screensDidWakeNotification, object: nil)
        workspace.addObserver(self, selector: #selector(displaySleep), name: NSWorkspace.willSleepNotification, object: nil)
        workspace.addObserver(self, selector: #selector(displayWake), name: NSWorkspace.didWakeNotification, object: nil)
        workspace.addObserver(self, selector: #selector(resignSession), name: NSWorkspace.sessionDidResignActiveNotification, object: nil)
        workspace.addObserver(self, selector: #selector(activateSession), name: NSWorkspace.sessionDidBecomeActiveNotification, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(environmentChanged),
            name: .NSProcessInfoPowerStateDidChange, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(environmentChanged),
            name: ProcessInfo.thermalStateDidChangeNotification, object: nil)
        if let source = IOPSNotificationCreateRunLoopSource({ context in
            guard let context else { return }
            MainActor.assumeIsolated {
                Unmanaged<LifecycleMonitor>.fromOpaque(context).takeUnretainedValue().refresh()
            }
        }, Unmanaged.passUnretained(self).toOpaque())?.takeRetainedValue() {
            powerSource = source
            CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes)
        }
        refresh()
    }

    public func stop() {
        guard running else { return }
        running = false
        NSWorkspace.shared.notificationCenter.removeObserver(self)
        NotificationCenter.default.removeObserver(self)
        if let powerSource {
            CFRunLoopRemoveSource(CFRunLoopGetMain(), powerSource, .commonModes)
            CFRunLoopSourceInvalidate(powerSource)
        }
        powerSource = nil
    }

    /// Refresh conditions before resuming; an inactive-session notification remains authoritative
    /// until a corresponding active notification. None of these signals assert "unlocked".
    public func refresh() {
        guard running else { return }
        var updated = Set<PauseReason>()
        if sleeping || CGDisplayIsAsleep(CGMainDisplayID()) != 0 { updated.insert(.displaySleeping) }
        if sessionResigned {
            updated.insert(.sessionInactive)
        } else if let dictionary = CGSessionCopyCurrentDictionary() as? [String: Any] {
            let console = dictionary[kCGSessionOnConsoleKey as String] as? Bool
            let login = dictionary[kCGSessionLoginDoneKey as String] as? Bool
            if console == false || login == false { updated.insert(.sessionInactive) }
            else if console != true || login != true { updated.insert(.sessionUnknown) }
        } else {
            updated.insert(.sessionUnknown)
        }
        if powerSource == nil {
            // A valid snapshot alone cannot promise detection of a subsequent battery transition.
            updated.insert(.powerUnknown)
        } else if let snapshot = IOPSCopyPowerSourcesInfo()?.takeRetainedValue(),
           let type = IOPSGetProvidingPowerSourceType(snapshot)?.takeUnretainedValue() as String? {
            if type == kIOPSBatteryPowerValue || type == "UPS Power" { updated.insert(.onBattery) }
            else if type != kIOPSACPowerValue { updated.insert(.powerUnknown) }
        } else { updated.insert(.powerUnknown) }
        if ProcessInfo.processInfo.isLowPowerModeEnabled { updated.insert(.lowPower) }
        if ProcessInfo.processInfo.thermalState == .serious || ProcessInfo.processInfo.thermalState == .critical {
            updated.insert(.thermal)
        }
        if updated != reasons { reasons = updated; onChange(updated) }
    }

    @objc private func displaySleep(_ notification: Notification) { sleeping = true; refresh() }
    @objc private func displayWake(_ notification: Notification) { sleeping = false; refresh() }
    @objc private func resignSession(_ notification: Notification) { sessionResigned = true; refresh() }
    @objc private func activateSession(_ notification: Notification) { sessionResigned = false; refresh() }
    @objc private func environmentChanged(_ notification: Notification) { refresh() }

    deinit {
        if let powerSource { CFRunLoopSourceInvalidate(powerSource) }
        NotificationCenter.default.removeObserver(self)
    }
}
