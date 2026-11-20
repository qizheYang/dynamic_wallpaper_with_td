import Foundation
import WallpaperCore

struct StudioPreferences: Codable {
    var selectedAssetID: String?
    var userPaused = false
    var screenID: UInt32?
    var provider: AgentProviderConfiguration?
    var pythonPath = "/usr/bin/python3"
    var ffmpegPath = ""
    var ffprobePath = ""
    var repositoryOverride = ""
    var weatherEnabled = false
    var sceneMode = "video"
    var customAPI: CustomAPIConfiguration?
}

enum StudioFailure: LocalizedError {
    case message(String)
    var errorDescription: String? { if case let .message(text) = self { return text }; return nil }
}

struct StudioOptions {
    var dataDirectory: URL?
    var smokeBundle: URL?
    var eventLog: URL?
    var duration = 90.0
    var showWindow = true
    var weatherSmoke = false
    var diagnosticBatteryPlayback = false
    var help = false

    static func parse(_ arguments: [String]) throws -> Self {
        var result = Self(), args = arguments, seen = Set<String>()
        while !args.isEmpty {
            let flag = args.removeFirst()
            guard seen.insert(flag).inserted else { throw StudioFailure.message("Repeated option: \(flag)") }
            switch flag {
            case "--help": result.help = true
            case "--no-window": result.showWindow = false
            case "--weather-smoke": result.weatherSmoke = true
            case "--diagnostic-battery-playback": result.diagnosticBatteryPlayback = true
            case "--data-dir", "--smoke-test", "--event-log", "--duration":
                guard let value = args.first, !value.hasPrefix("--") else { throw StudioFailure.message("Missing value for \(flag)") }
                args.removeFirst()
                switch flag {
                case "--data-dir": result.dataDirectory = URL(fileURLWithPath: value)
                case "--smoke-test": result.smokeBundle = URL(fileURLWithPath: value)
                case "--event-log": result.eventLog = URL(fileURLWithPath: value)
                default:
                    guard let duration = Double(value), duration.isFinite, (15...3600).contains(duration) else {
                        throw StudioFailure.message("Duration must be 15...3600 seconds")
                    }
                    result.duration = duration
                }
            default: throw StudioFailure.message("Unknown option: \(flag)")
            }
        }
        if result.smokeBundle != nil || result.weatherSmoke {
            guard result.dataDirectory != nil, result.eventLog != nil else {
                throw StudioFailure.message("Smoke tests require isolated --data-dir and new --event-log")
            }
        }
        if result.diagnosticBatteryPlayback && (result.smokeBundle == nil || result.duration > 120) {
            throw StudioFailure.message("Battery playback override is limited to an explicit video smoke test of at most 120 seconds")
        }
        if let log = result.eventLog, FileManager.default.fileExists(atPath: log.path) {
            throw StudioFailure.message("Event log already exists")
        }
        return result
    }
}

func findExecutable(_ name: String) -> String {
    let directories = (ProcessInfo.processInfo.environment["PATH"] ?? "/usr/bin:/bin").split(separator: ":").map(String.init)
        + ["/opt/homebrew/bin", "/usr/local/bin"]
    return directories.map { URL(fileURLWithPath: $0).appendingPathComponent(name).path }
        .first { FileManager.default.isExecutableFile(atPath: $0) } ?? ""
}
