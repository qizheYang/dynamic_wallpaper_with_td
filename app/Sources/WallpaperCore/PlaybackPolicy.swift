import Foundation

public enum PauseReason: String, Codable, CaseIterable, Sendable {
    case user, authoring, occluded, sessionInactive, sessionUnknown, displaySleeping
    case onBattery, powerUnknown, lowPower, thermal

    public var label: String {
        switch self {
        case .user: return "Paused by you"
        case .authoring: return "Creating wallpaper"
        case .occluded: return "Desktop covered"
        case .sessionInactive: return "Session inactive"
        case .sessionUnknown: return "Session state unavailable"
        case .displaySleeping: return "Display sleeping"
        case .onBattery: return "Battery: static wallpaper"
        case .powerUnknown: return "Power source unavailable"
        case .lowPower: return "Low Power Mode"
        case .thermal: return "Thermal protection"
        }
    }
}

/// Pure state rules. Session activity is deliberately not a lock detector.
public struct PlaybackPolicy: Equatable, Sendable {
    public var reasons: Set<PauseReason> = []
    public var assetAvailable = false
    public var screenAvailable = false
    public var failed = false
    public init() {}
    public var permitsPlayback: Bool {
        assetAvailable && screenAvailable && !failed && reasons.isEmpty
    }
    public mutating func set(_ reason: PauseReason, active: Bool) {
        if active { reasons.insert(reason) } else { reasons.remove(reason) }
    }
}

public struct PlaybackEvent: Sendable {
    public let date: Date
    public let name: String
    public let details: [String: String]
    public init(name: String, details: [String: String] = [:]) {
        self.date = Date(); self.name = name; self.details = details
    }
}
