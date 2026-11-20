// Focused AppKit lifecycle checks, without launching NSApplication or creating a window.
// Compiled with unchanged production bodies by run_studio_checks.py. No XCTest dependency.
import AppKit
import Foundation

private struct StudioCheckFailure: Error, CustomStringConvertible {
    let description: String
}

@MainActor
@main
struct StudioLifecycleChecks {
    private static func require(_ condition: @autoclosure () throws -> Bool, _ message: String) throws {
        guard try condition() else { throw StudioCheckFailure(description: message) }
    }

    /// Observe the stored handle without altering access levels or adding production test hooks.
    /// No live generation job is needed: a busy model must preserve its empty operation slot too.
    private static func operationIsEmpty(_ model: StudioModel) throws -> Bool {
        guard let field = Mirror(reflecting: model).children.first(where: { $0.label == "task" }) else {
            throw StudioCheckFailure(description: "Cannot inspect StudioModel operation slot; update the harness explicitly")
        }
        let optional = Mirror(reflecting: field.value)
        guard optional.displayStyle == .optional else {
            throw StudioCheckFailure(description: "StudioModel operation slot changed type")
        }
        return optional.children.isEmpty
    }

    private static func checkImportAdmission(_ root: URL) async throws {
        let model = try StudioModel(dataDirectory: root.appendingPathComponent("isolated-data"))
        defer { model.shutdown() }
        try require(try operationIsEmpty(model), "A fresh model unexpectedly owns an operation")
        model.busy = true
        // The Python launcher checks the leading busy guard before running this call,
        // so removing that guard fails safely instead of opening an NSOpenPanel.
        model.chooseBundle()
        try require(model.busy, "Menu import cleared another operation's busy state")
        try require(try operationIsEmpty(model), "Menu import replaced the operation handle while busy")
        let missing = root.appendingPathComponent("nonexistent-bundle")
        await model.importBundle(missing)
        try require(model.busy, "Async import cleared another operation's busy state")
        try require(try operationIsEmpty(model), "Async import replaced the operation handle while busy")
        try require(model.errorMessage == nil, "Refused import nevertheless performed work")
        try require((NSApp?.windows.count ?? 0) == 0, "Import admission created an AppKit window")

        model.busy = false
        await model.importBundle(missing)
        try require(!model.busy, "Failed import did not release its busy state")
        try require(model.errorMessage != nil, "Invalid bundle did not produce an error")
        try require(model.assets.isEmpty, "Invalid bundle was published")
        try require(!((try? operationIsEmpty(model)) ?? true), "An admitted import never acquired its operation handle")
        print("PASS StudioModel.busy-import-admission-and-failure-cleanup")
    }

    private static func checkQueuedScreenNotification() async throws {
        let invalidScreen = UInt32.max
        try require(!NSScreen.screens.contains {
            ($0.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.uint32Value == invalidScreen
        }, "The supposedly invalid display ID exists; refusing to create a window")
        let scene = SceneDesktop()
        defer { scene.stop() }
        scene.start(screenID: invalidScreen)
        try require(scene.status == "所选显示器不可用", "Missing-screen setup did not reach the expected state")
        try require((NSApp?.windows.count ?? 0) == 0, "Missing-screen setup created a window")
        // Notification delivery queues a MainActor task. Stop before any suspension
        // point permits that task to run; it must be rejected as an old generation.
        NotificationCenter.default.post(name: NSApplication.didChangeScreenParametersNotification, object: nil)
        scene.stop()
        for _ in 0..<20 { await Task.yield() }
        try await Task.sleep(nanoseconds: 50_000_000)
        try require(scene.status == "未启用实时场景", "A queued screen callback resurrected the stopped scene")
        try require((NSApp?.windows.count ?? 0) == 0, "Screen callback created a window after stop")
        print("PASS SceneDesktop.queued-screen-change-after-stop")
    }

    private static func checkDiagnosticBounds(_ root: URL) throws {
        let base = ["--smoke-test", root.appendingPathComponent("bundle").path,
                    "--data-dir", root.appendingPathComponent("diagnostic-data").path,
                    "--event-log", root.appendingPathComponent("unused-events.jsonl").path]
        let allowed = try StudioOptions.parse(base + ["--duration", "120", "--diagnostic-battery-playback"])
        try require(allowed.diagnosticBatteryPlayback && allowed.duration == 120, "Bounded explicit override was rejected")
        try require(!StudioOptions.parse([]).diagnosticBatteryPlayback, "Ordinary startup enabled battery override")
        let rejected = [base + ["--duration", "121", "--diagnostic-battery-playback"],
                        ["--diagnostic-battery-playback"], base + ["--duration", "nan"],
                        base + ["--duration", "inf"], base + ["--duration", "0"],
                        base + ["--duration", "3601"]]
        for arguments in rejected {
            do {
                _ = try StudioOptions.parse(arguments)
            } catch { continue }
            throw StudioCheckFailure(description: "Unsafe diagnostic arguments were accepted: \(arguments)")
        }
        print("PASS StudioOptions.explicit-bounded-diagnostic-override")
    }

    static func main() async {
        let root = URL(fileURLWithPath: "/private/tmp", isDirectory: true)
            .appendingPathComponent("wallpaper-studio-lifecycle-" + UUID().uuidString, isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
            defer { try? FileManager.default.removeItem(at: root) }
            try require((NSApp?.windows.count ?? 0) == 0, "Harness started with an unexpected window")
            try await checkImportAdmission(root)
            try await checkQueuedScreenNotification()
            try checkDiagnosticBounds(root)
            print("Studio lifecycle checks: 3 passed; no windows, TD, network requests, or Keychain writes.")
        } catch {
            FileHandle.standardError.write(Data(("FAIL Studio lifecycle checks: \(error)\n").utf8))
            exit(1)
        }
    }
}
