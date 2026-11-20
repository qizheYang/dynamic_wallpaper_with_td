import Foundation
import AppKit
import Darwin

public struct CreationConfiguration: Sendable {
    public var repository: URL
    public var python: URL
    public var ffmpeg: URL
    public var ffprobe: URL
    public var touchDesignerApplication: URL
    public init(repository: URL, python: URL, ffmpeg: URL, ffprobe: URL,
                touchDesignerApplication: URL = URL(fileURLWithPath: "/Applications/TouchDesigner.app")) {
        self.repository = repository; self.python = python; self.ffmpeg = ffmpeg; self.ffprobe = ffprobe
        self.touchDesignerApplication = touchDesignerApplication
    }
}

public enum CreationProgress: String, Sendable { case preparing, rendering, validating, complete }
public enum CreationError: Error, LocalizedError {
    case busy, missingDependency(String), invalidOutput, processFailed(Int32, URL), timeout, outputLimit, tdAlreadyRunning
    public var errorDescription: String? {
        switch self {
        case .busy: return "A creation is already running. Cancel it or wait for completion."
        case .missingDependency(let name): return "Creation requires \(name). Configure its installed location in Settings."
        case .invalidOutput: return "The pipeline did not publish a complete validated wallpaper bundle."
        case .processFailed(let status, let log): return "Creation stopped (exit \(status)). Details: \(log.path)"
        case .timeout: return "Creation exceeded its time limit and its worker was stopped."
        case .outputLimit: return "Creation exceeded its diagnostic output limit and its worker was stopped."
        case .tdAlreadyRunning: return "TouchDesigner is already open. The editable copy is saved; open it after preserving and closing your current project."
        }
    }
}

public struct CreationCommand: Sendable {
    public let executable: URL
    public let arguments: [String]
    public let directory: URL
    public let environment: [String: String]
    public let timeout: TimeInterval
    public let logDirectory: URL
    public let label: String
    public let outputLimit: Int
    public init(executable: URL, arguments: [String], directory: URL, environment: [String: String],
                timeout: TimeInterval, logDirectory: URL, label: String, outputLimit: Int = 524_288) {
        self.executable = executable; self.arguments = arguments; self.directory = directory
        self.environment = environment; self.timeout = timeout; self.logDirectory = logDirectory
        self.label = label; self.outputLimit = outputLimit
    }
}

public struct CreationCommandResult: Sendable {
    public let status: Int32
    public let stdout: Data
    public let stderrLog: URL
    public init(status: Int32, stdout: Data, stderrLog: URL) {
        self.status = status; self.stdout = stdout; self.stderrLog = stderrLog
    }
}
public protocol CreationCommandRunning {
    func run(_ command: CreationCommand) async throws -> CreationCommandResult
}

public struct LocalCreationCommandRunner: CreationCommandRunning {
    public init() {}
    public func run(_ command: CreationCommand) async throws -> CreationCommandResult {
        let execution = CreationExecution(command)
        return try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { continuation in
                DispatchQueue.global(qos: .utility).async {
                    do { continuation.resume(returning: try execution.run()) }
                    catch { continuation.resume(throwing: error) }
                }
            }
        }, onCancel: { execution.cancel() })
    }
}

private final class LimitedProcessOutput: @unchecked Sendable {
    private let lock = NSLock()
    private let file: FileHandle
    private let limit: Int
    private var bytes = Data()
    private(set) var readHandle: FileHandle?
    private var exceeded = false
    private let ended = DispatchGroup()
    init(url: URL, limit: Int) throws {
        self.limit = limit
        guard FileManager.default.createFile(atPath: url.path, contents: nil,
                                             attributes: [.posixPermissions: 0o600]) else { throw CreationError.invalidOutput }
        file = try FileHandle(forWritingTo: url)
    }
    func connect(_ pipe: Pipe) {
        readHandle = pipe.fileHandleForReading; ended.enter()
        pipe.fileHandleForReading.readabilityHandler = { [self] handle in
            let data = handle.availableData
            if data.isEmpty { handle.readabilityHandler = nil; ended.leave(); return }
            lock.lock(); defer { lock.unlock() }
            let remaining = max(0, limit - bytes.count)
            let accepted = data.prefix(remaining)
            bytes.append(accepted)
            do { try file.write(contentsOf: accepted) } catch { exceeded = true }
            if data.count > remaining { exceeded = true }
        }
    }
    var didExceed: Bool { lock.lock(); defer { lock.unlock() }; return exceeded }
    func finish() -> Data {
        _ = ended.wait(timeout: .now() + 2)
        readHandle?.readabilityHandler = nil
        try? readHandle?.close()
        lock.lock(); defer { lock.unlock() }
        try? file.close()
        return bytes
    }
}

/// SIGTERM lets the existing TD runner/encoder clean their children before a bounded SIGKILL fallback.
private final class CreationExecution: @unchecked Sendable {
    private let command: CreationCommand
    private let lock = NSLock()
    private var cancelled = false
    init(_ command: CreationCommand) { self.command = command }
    func cancel() { lock.lock(); cancelled = true; lock.unlock() }
    private var isCancelled: Bool { lock.lock(); defer { lock.unlock() }; return cancelled }
    func run() throws -> CreationCommandResult {
        if isCancelled { throw CancellationError() }
        guard command.timeout.isFinite, command.timeout > 0, command.timeout <= 1800,
              command.outputLimit > 0, command.outputLimit <= 1_048_576 else { throw CreationError.invalidOutput }
        let fm = FileManager.default
        try fm.createDirectory(at: command.logDirectory, withIntermediateDirectories: true,
                               attributes: [.posixPermissions: 0o700])
        let stdoutURL = command.logDirectory.appendingPathComponent(command.label + ".stdout.log")
        let stderrURL = command.logDirectory.appendingPathComponent(command.label + ".stderr.log")
        let output = try LimitedProcessOutput(url: stdoutURL, limit: command.outputLimit)
        let errors = try LimitedProcessOutput(url: stderrURL, limit: command.outputLimit)
        let outPipe = Pipe(), errPipe = Pipe()
        let process = Process()
        process.executableURL = command.executable; process.arguments = command.arguments
        process.currentDirectoryURL = command.directory; process.environment = command.environment
        process.standardInput = FileHandle.nullDevice; process.standardOutput = outPipe; process.standardError = errPipe
        do { try process.run() }
        catch { _ = output.finish(); _ = errors.finish(); throw CreationError.missingDependency(command.executable.lastPathComponent) }
        output.connect(outPipe); errors.connect(errPipe)
        let started = ProcessInfo.processInfo.systemUptime
        var failure: Error?
        var terminatedAt: TimeInterval?
        while process.isRunning {
            let now = ProcessInfo.processInfo.systemUptime
            if failure == nil {
                if isCancelled { failure = CancellationError() }
                else if output.didExceed || errors.didExceed { failure = CreationError.outputLimit }
                else if now - started > command.timeout { failure = CreationError.timeout }
                if failure != nil { process.terminate(); terminatedAt = now }
            } else if let terminatedAt = terminatedAt, now - terminatedAt > 15 {
                // Only the Process instance created here is signalled; never a name/process group.
                _ = Darwin.kill(process.processIdentifier, SIGKILL)
            }
            Thread.sleep(forTimeInterval: 0.05)
        }
        process.waitUntilExit()
        let stdout = output.finish(); _ = errors.finish()
        if let failure = failure { throw failure }
        if isCancelled { throw CancellationError() }
        if output.didExceed || errors.didExceed { throw CreationError.outputLimit }
        return CreationCommandResult(status: process.terminationStatus, stdout: stdout, stderrLog: stderrURL)
    }
}

public actor CreationService {
    private let runner: CreationCommandRunning
    private var busy = false
    public init(runner: CreationCommandRunning = LocalCreationCommandRunner()) { self.runner = runner }

    /// Called only by the explicit Generate action. Never executes model text or shell command strings.
    public func generate(parameters: SoftGradientParameters, configuration: CreationConfiguration,
                         progress: @escaping @Sendable (CreationProgress) -> Void = { _ in }) async throws -> URL {
        guard !busy else { throw CreationError.busy }
        busy = true; defer { busy = false }
        try Task.checkCancellation(); try parameters.validate()
        try Self.validate(configuration)
        let repo = configuration.repository.standardizedFileURL
        let identifier = UUID().uuidString.lowercased()
        let jobID = "app_" + identifier
        let attemptID = "app-" + identifier
        let work = repo.appendingPathComponent("renders/app_creation/" + identifier)
        try FileManager.default.createDirectory(at: work, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let jobURL = work.appendingPathComponent("job.json")
        let job: [String: Any] = ["schema_version": 1, "job_id": jobID,
            "template": ["id": "soft_gradient", "version": 1, "path": "td/wallpaper_template.toe"],
            "seed": parameters.seed, "width": 1280, "height": 720, "fps": 24, "frame_count": 480,
            "parameters": ["palette": "dusk", "motion_cycles": parameters.motionCycles],
            "source": ["format": "png", "bit_depth": 8, "color": "rgb_rec709_bt709trc_full"],
            "output_dir": "renders/" + jobID]
        try JSONSerialization.data(withJSONObject: job, options: [.prettyPrinted, .sortedKeys]).write(to: jobURL, options: .atomic)
        let attempt = repo.appendingPathComponent("renders/\(jobID)/attempts/\(attemptID)")
        let bundle = repo.appendingPathComponent("renders/\(jobID)/bundles/\(attemptID)")
        // Do not inherit model/data keys, PYTHONPATH hooks, or arbitrary user environment.
        let environment = ["PATH": [configuration.python.deletingLastPathComponent().path,
                                    configuration.ffmpeg.deletingLastPathComponent().path,
                                    configuration.ffprobe.deletingLastPathComponent().path,
                                    "/usr/bin", "/bin", "/usr/sbin", "/sbin"].joined(separator: ":"),
                           "HOME": FileManager.default.homeDirectoryForCurrentUser.path,
                           "TMPDIR": NSTemporaryDirectory(), "PYTHONUNBUFFERED": "1", "PYTHONNOUSERSITE": "1",
                           "TOUCHDESIGNER_APP": configuration.touchDesignerApplication.path]
        func command(_ arguments: [String], _ label: String, _ timeout: TimeInterval) -> CreationCommand {
            CreationCommand(executable: configuration.python, arguments: arguments, directory: repo,
                            environment: environment, timeout: timeout, logDirectory: work, label: label)
        }
        do {
            progress(.preparing)
            try await execute(command(["-m", "pipeline", "prepare", jobURL.path, "--attempt-id", attemptID, "--root", repo.path], "prepare", 30))
            progress(.rendering)
            try await execute(command(["td/run_td.py", "export", attempt.path, "--timeout", "900"], "export", 930))
            progress(.validating)
            try await execute(command(["-m", "pipeline", "finalize", attempt.path, "--ffmpeg", configuration.ffmpeg.path,
                                       "--ffprobe", configuration.ffprobe.path], "finalize", 300))
            let stateData = try Data(contentsOf: attempt.appendingPathComponent("state.json"))
            guard let state = try JSONSerialization.jsonObject(with: stateData) as? [String: Any], state["state"] as? String == "complete",
                  ["manifest.json", "loop.mp4", "cover.png"].allSatisfy({ FileManager.default.fileExists(atPath: bundle.appendingPathComponent($0).path) })
            else { throw CreationError.invalidOutput }
            try Task.checkCancellation()
            progress(.complete); return bundle
        } catch {
            if FileManager.default.fileExists(atPath: attempt.appendingPathComponent("state.json").path) {
                let cleanup = command(["-m", "pipeline", "cancel", attempt.path, "--reason", "App creation stopped"], "cancel", 15)
                let runner = self.runner
                // The cancelled caller must still allow the state cleanup command to run.
                _ = await Task.detached { try? await runner.run(cleanup) }.value
            }
            throw error
        }
    }

    private func execute(_ command: CreationCommand) async throws {
        try Task.checkCancellation()
        let result = try await runner.run(command)
        guard result.status == 0 else { throw CreationError.processFailed(result.status, result.stderrLog) }
        try Task.checkCancellation()
    }

    private static func validate(_ configuration: CreationConfiguration) throws {
        let fm = FileManager.default
        for (name, file) in [("Python 3", configuration.python), ("FFmpeg", configuration.ffmpeg), ("ffprobe", configuration.ffprobe),
                             ("TouchDesigner", configuration.touchDesignerApplication.appendingPathComponent("Contents/MacOS/TouchDesigner"))] {
            guard file.isFileURL, fm.isExecutableFile(atPath: file.path) else { throw CreationError.missingDependency(name) }
        }
        for path in ["pipeline/__main__.py", "td/run_td.py", "td/wallpaper_template.toe"] {
            guard fm.fileExists(atPath: configuration.repository.appendingPathComponent(path).path)
            else { throw CreationError.missingDependency("the versioned authoring workspace") }
        }
    }

    /// Copies only editable template sources. Does not open TD, install MCP, overwrite files or copy renders/secrets.
    public static func copyTemplate(repository: URL, projectsDirectory: URL) throws -> URL {
        let folder = projectsDirectory.appendingPathComponent("soft-gradient-" + UUID().uuidString.lowercased())
        let paths = ["td/wallpaper_template.toe", "td/shaders/soft_gradient.frag", "td/controller/soft_gradient_build.py"]
        for relative in paths {
            let source = repository.appendingPathComponent(relative).resolvingSymlinksInPath()
            guard source.path.hasPrefix(repository.resolvingSymlinksInPath().path + "/"),
                  FileManager.default.fileExists(atPath: source.path) else { throw CreationError.missingDependency("template sources") }
        }
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        for relative in paths {
            let destination = folder.appendingPathComponent(relative)
            try FileManager.default.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
            try FileManager.default.copyItem(at: repository.appendingPathComponent(relative), to: destination)
        }
        return folder.appendingPathComponent("td/wallpaper_template.toe")
    }

    @MainActor public static func openTemplate(url: URL,
        application: URL = URL(fileURLWithPath: "/Applications/TouchDesigner.app")) async throws {
        let alreadyOpen = NSWorkspace.shared.runningApplications.contains {
            $0.bundleIdentifier == "com.derivative.TouchDesigner" ||
            $0.bundleURL?.lastPathComponent == "TouchDesigner.app" || $0.localizedName == "TouchDesigner"
        }
        guard !alreadyOpen else { throw CreationError.tdAlreadyRunning }
        guard FileManager.default.isExecutableFile(atPath: application.appendingPathComponent("Contents/MacOS/TouchDesigner").path)
        else { throw CreationError.missingDependency("TouchDesigner") }
        guard url.isFileURL, url.pathExtension == "toe", FileManager.default.fileExists(atPath: url.path)
        else { throw CreationError.missingDependency("the editable template copy") }
        _ = try await NSWorkspace.shared.open([url], withApplicationAt: application, configuration: NSWorkspace.OpenConfiguration())
    }
}
