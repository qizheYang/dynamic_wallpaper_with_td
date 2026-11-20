import Foundation
import Combine
import CoreFoundation

/// Weather values are model-derived Open-Meteo current conditions, not station observations.
public enum WeatherKind: String, Codable, Sendable {
    case clear, cloudy, fog, rain, snow, storm

    public static func from(code: Int) throws -> WeatherKind {
        switch code {
        case 0, 1: return .clear
        case 2, 3: return .cloudy
        case 45, 48: return .fog
        case 51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82: return .rain
        case 71, 73, 75, 77, 85, 86: return .snow
        case 95, 96, 99: return .storm
        default: throw DataServiceError.invalidResponse("Unsupported weather code")
        }
    }
}

public struct WeatherSnapshot: Codable, Equatable, Sendable {
    public let observedAt: Date
    public let receivedAt: Date
    public let temperatureCelsius: Double
    public let windMetresPerSecond: Double
    public let precipitationMillimetres: Double
    public let cloudFraction: Double
    public let isDay: Bool
    public let code: Int
    public let kind: WeatherKind
    public let timeZoneIdentifier: String

    public init(observedAt: Date, receivedAt: Date, temperatureCelsius: Double,
                windMetresPerSecond: Double, precipitationMillimetres: Double,
                cloudFraction: Double, isDay: Bool, code: Int,
                kind: WeatherKind, timeZoneIdentifier: String = "America/Los_Angeles") {
        self.observedAt = observedAt; self.receivedAt = receivedAt
        self.temperatureCelsius = temperatureCelsius
        self.windMetresPerSecond = windMetresPerSecond
        self.precipitationMillimetres = precipitationMillimetres
        self.cloudFraction = cloudFraction; self.isDay = isDay
        self.code = code; self.kind = kind; self.timeZoneIdentifier = timeZoneIdentifier
    }
}

public struct WeatherConfiguration: Equatable, Sendable {
    public var latitude: Double
    public var longitude: Double
    public var timeZoneIdentifier: String
    public var refreshInterval: TimeInterval
    public var cacheTTL: TimeInterval
    public var staleGrace: TimeInterval
    public static let losAngeles = WeatherConfiguration()

    public init(latitude: Double = 34.0522, longitude: Double = -118.2437,
                timeZoneIdentifier: String = "America/Los_Angeles",
                refreshInterval: TimeInterval = 900, cacheTTL: TimeInterval = 1800,
                staleGrace: TimeInterval = 7200) {
        self.latitude = latitude; self.longitude = longitude
        self.timeZoneIdentifier = timeZoneIdentifier
        self.refreshInterval = refreshInterval; self.cacheTTL = cacheTTL; self.staleGrace = staleGrace
    }

    public func validate() throws {
        guard latitude.isFinite, (-90...90).contains(latitude), longitude.isFinite,
              (-180...180).contains(longitude), TimeZone(identifier: timeZoneIdentifier) != nil,
              refreshInterval.isFinite, (300...86400).contains(refreshInterval),
              cacheTTL.isFinite, (300...86400).contains(cacheTTL),
              staleGrace.isFinite, staleGrace >= cacheTTL, staleGrace <= 86400 else {
            throw DataServiceError.invalidConfiguration
        }
    }
}

public enum DataServiceError: Error, LocalizedError, Equatable {
    case invalidConfiguration
    case invalidResponse(String)
    case responseTooLarge
    case httpStatus(Int)
    case rateLimited(TimeInterval)
    case staleResponse
    case connectionUnavailable

    public var errorDescription: String? {
        switch self {
        case .invalidConfiguration: return "Invalid data connection configuration."
        case .invalidResponse(let reason): return "Invalid data response: \(reason)."
        case .responseTooLarge: return "Data response exceeds the 256 KiB limit."
        case .httpStatus(let status): return "Data service returned HTTP \(status)."
        case .rateLimited: return "Data service rate limit; waiting before retry."
        case .staleResponse: return "The provider's weather data is too old."
        case .connectionUnavailable: return "Data connection unavailable."
        }
    }
}

/// Reject cross-origin redirects, including HTTPS→HTTP and alternate ports.
/// Authentication is never copied to another origin. Cookies and disk cache are disabled.
public final class DataHTTPClient: @unchecked Sendable {
    public static let maximumBytes = 256 * 1024
    private let session: URLSession
    public init() {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 15; config.timeoutIntervalForResource = 30
        config.httpCookieStorage = nil; config.httpShouldSetCookies = false; config.urlCache = nil
        // The delegate does not retain this client, so a client/session cycle cannot leak it.
        session = URLSession(configuration: config, delegate: DataRedirectGuard(), delegateQueue: nil)
    }

    public static func sameOrigin(_ lhs: URL, _ rhs: URL) -> Bool {
        lhs.scheme?.lowercased() == "https" && rhs.scheme?.lowercased() == "https" &&
        lhs.host?.lowercased() == rhs.host?.lowercased() && (lhs.port ?? 443) == (rhs.port ?? 443)
    }

    public func fetch(_ url: URL, bearerToken: String? = nil) async throws -> Data {
        do { return try await boundedFetch(url, bearerToken: bearerToken) }
        catch is CancellationError { throw CancellationError() }
        catch let error as DataServiceError { throw error }
        catch let error as URLError where error.code == .cancelled { throw CancellationError() }
        catch { throw DataServiceError.connectionUnavailable }
    }

    private func boundedFetch(_ url: URL, bearerToken: String?) async throws -> Data {
        guard url.scheme?.lowercased() == "https", url.host != nil,
              url.user == nil, url.password == nil, url.fragment == nil else {
            throw DataServiceError.invalidConfiguration
        }
        var request = URLRequest(url: url)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let token = bearerToken, !token.isEmpty {
            guard token.utf8.count <= 8192, !token.contains("\r"), !token.contains("\n") else {
                throw DataServiceError.invalidConfiguration
            }
            request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        }
        let (bytes, response) = try await session.bytes(for: request)
        defer { bytes.task.cancel() }
        guard let http = response as? HTTPURLResponse else {
            throw DataServiceError.invalidResponse("Missing HTTP response")
        }
        guard http.statusCode != 429 else {
            throw DataServiceError.rateLimited(Self.retryDelay(http.value(forHTTPHeaderField: "Retry-After")))
        }
        guard (200..<300).contains(http.statusCode) else { throw DataServiceError.httpStatus(http.statusCode) }
        let mime = (http.mimeType ?? "").lowercased()
        guard mime == "application/json" || mime.hasSuffix("+json") else {
            throw DataServiceError.invalidResponse("Expected JSON content type")
        }
        guard http.expectedContentLength <= Self.maximumBytes else { throw DataServiceError.responseTooLarge }
        var data = Data()
        for try await byte in bytes {
            try Task.checkCancellation()
            guard data.count < Self.maximumBytes else { throw DataServiceError.responseTooLarge }
            data.append(byte)
        }
        return data
    }

    public func close() { session.invalidateAndCancel() }
    deinit { session.invalidateAndCancel() }

    static func retryDelay(_ header: String?, now: Date = Date()) -> TimeInterval {
        guard let header else { return 900 }
        let seconds: TimeInterval
        if let number = Double(header), number.isFinite { seconds = number }
        else {
            let format = DateFormatter()
            format.locale = Locale(identifier: "en_US_POSIX"); format.timeZone = TimeZone(secondsFromGMT: 0)
            format.dateFormat = "EEE, dd MMM yyyy HH:mm:ss z"
            guard let date = format.date(from: header) else { return 900 }
            seconds = date.timeIntervalSince(now)
        }
        return min(86400, max(60, seconds))
    }
}

private final class DataRedirectGuard: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        guard let original = task.originalRequest?.url, let redirected = request.url,
              DataHTTPClient.sameOrigin(original, redirected), redirected.user == nil,
              redirected.password == nil else {
            completionHandler(nil); return
        }
        completionHandler(request)
    }
}

public protocol WeatherClient: Sendable {
    func fetch(configuration: WeatherConfiguration) async throws -> WeatherSnapshot
}

public final class OpenMeteoClient: WeatherClient, @unchecked Sendable {
    private let http: DataHTTPClient
    public init(http: DataHTTPClient = DataHTTPClient()) { self.http = http }
    deinit { http.close() }

    public func fetch(configuration: WeatherConfiguration) async throws -> WeatherSnapshot {
        let url = try Self.requestURL(configuration: configuration)
        return try Self.decode(await http.fetch(url), configuration: configuration, now: Date())
    }

    public static func requestURL(configuration: WeatherConfiguration) throws -> URL {
        try configuration.validate()
        var parts = URLComponents(string: "https://api.open-meteo.com/v1/forecast")!
        parts.queryItems = [
            URLQueryItem(name: "latitude", value: String(configuration.latitude)),
            URLQueryItem(name: "longitude", value: String(configuration.longitude)),
            URLQueryItem(name: "current", value: "temperature_2m,is_day,precipitation,weather_code,cloud_cover,wind_speed_10m"),
            URLQueryItem(name: "temperature_unit", value: "celsius"),
            URLQueryItem(name: "wind_speed_unit", value: "ms"),
            URLQueryItem(name: "precipitation_unit", value: "mm"),
            URLQueryItem(name: "timezone", value: configuration.timeZoneIdentifier),
            URLQueryItem(name: "timeformat", value: "unixtime")
        ]
        return parts.url!
    }

    public static func decode(_ data: Data, configuration: WeatherConfiguration = .losAngeles,
                              now: Date = Date()) throws -> WeatherSnapshot {
        try configuration.validate()
        guard data.count <= DataHTTPClient.maximumBytes else { throw DataServiceError.responseTooLarge }
        guard let root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let current = root["current"] as? [String: Any],
              let units = root["current_units"] as? [String: Any],
              root["timezone"] as? String == configuration.timeZoneIdentifier else {
            throw DataServiceError.invalidResponse("Missing weather fields or wrong timezone")
        }
        let expected = ["time": "unixtime", "interval": "seconds", "temperature_2m": "°C",
                        "wind_speed_10m": "m/s", "precipitation": "mm", "cloud_cover": "%",
                        "is_day": "", "weather_code": "wmo code"]
        for (key, value) in expected where units[key] as? String != value {
            throw DataServiceError.invalidResponse("Unexpected weather units")
        }
        let stamp = try jsonNumber(current["time"], range: 0...4_102_444_800)
        let observed = Date(timeIntervalSince1970: stamp)
        guard observed <= now.addingTimeInterval(300) else {
            throw DataServiceError.invalidResponse("Future weather timestamp")
        }
        guard now.timeIntervalSince(observed) <= configuration.staleGrace else { throw DataServiceError.staleResponse }
        _ = try jsonNumber(current["interval"], range: 1...3600)
        let temperature = try jsonNumber(current["temperature_2m"], range: -100...70)
        let wind = try jsonNumber(current["wind_speed_10m"], range: 0...150)
        let rain = try jsonNumber(current["precipitation"], range: 0...500)
        let cloud = try jsonNumber(current["cloud_cover"], range: 0...100)
        let day = try jsonNumber(current["is_day"], range: 0...1)
        let code = try jsonNumber(current["weather_code"], range: 0...99)
        guard day.rounded() == day, code.rounded() == code else {
            throw DataServiceError.invalidResponse("Invalid weather code or daylight flag")
        }
        return WeatherSnapshot(observedAt: observed, receivedAt: now, temperatureCelsius: temperature,
                               windMetresPerSecond: wind, precipitationMillimetres: rain,
                               cloudFraction: cloud / 100, isDay: day == 1, code: Int(code),
                               kind: try WeatherKind.from(code: Int(code)),
                               timeZoneIdentifier: configuration.timeZoneIdentifier)
    }
}

private func jsonNumber(_ value: Any?, range: ClosedRange<Double>) throws -> Double {
    guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
          number.doubleValue.isFinite, range.contains(number.doubleValue) else {
        throw DataServiceError.invalidResponse("Missing or out-of-range numeric value")
    }
    return number.doubleValue
}

public enum WeatherStatus: Equatable {
    case idle, loading, fresh, paused
    case stale(String), offline(String)
}

/// One controller is shared by every screen. No model calls or TD work occurs here.
@MainActor public final class WeatherController: ObservableObject {
    @Published public private(set) var snapshot: WeatherSnapshot?
    @Published public private(set) var status: WeatherStatus = .idle
    public let configuration: WeatherConfiguration
    private let client: any WeatherClient
    private let now: () -> Date
    private var lastGood: WeatherSnapshot?
    private var nextAllowedRefresh = Date.distantPast
    private var failureCount = 0
    private var generation = 0
    private var polling: Task<Void, Never>?
    private var pending: Task<WeatherSnapshot, Error>?

    public init(configuration: WeatherConfiguration = .losAngeles,
                client: any WeatherClient = OpenMeteoClient(), now: @escaping () -> Date = Date.init) {
        self.configuration = configuration; self.client = client; self.now = now
    }

    public var statusText: String {
        switch status {
        case .idle: return "Weather is off"
        case .loading: return "Updating weather…"
        case .fresh: return "Open-Meteo model data · updated"
        case .paused: return "Weather updates paused"
        case .stale(let text): return "Last known weather · " + text
        case .offline(let text): return "Offline scene · " + text
        }
    }

    public func start() {
        guard polling == nil else { return }
        do { try configuration.validate() } catch {
            snapshot = nil; status = .offline("Invalid configuration"); return
        }
        // Expire data synchronously on wake before the asynchronous request can finish.
        updateFreshness(message: "Waiting for current weather")
        polling = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refresh()
                guard let seconds = self?.nextDelay else { return }
                do { try await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000)) }
                catch { return }
            }
        }
    }

    /// Call on sleep and when the data mode is unloaded. Cancels in-flight network work.
    public func stop() {
        generation += 1; polling?.cancel(); polling = nil; pending?.cancel(); pending = nil
        status = .paused
    }

    public func refresh(force: Bool = false) async {
        guard pending == nil else { return }
        let instant = now()
        // Manual refresh may bypass a successful cache, but never a failed-request backoff.
        if instant < nextAllowedRefresh && (!force || failureCount > 0) {
            updateFreshness(message: "Waiting for the next update")
            return
        }
        do { try configuration.validate() } catch {
            snapshot = nil; status = .offline("Invalid configuration"); return
        }
        let token = generation
        status = .loading
        let configuration = self.configuration, client = self.client
        let task = Task { try await client.fetch(configuration: configuration) }
        pending = task
        do {
            let fetched = try await withTaskCancellationHandler(operation: { try await task.value },
                                                              onCancel: { task.cancel() })
            guard token == generation else { return }
            pending = nil
            if Task.isCancelled { updateFreshness(message: "Update cancelled"); return }
            guard now().timeIntervalSince(fetched.observedAt) <= configuration.staleGrace else {
                throw DataServiceError.staleResponse
            }
            lastGood = fetched; failureCount = 0
            nextAllowedRefresh = now().addingTimeInterval(configuration.refreshInterval)
            updateFreshness(message: "Provider data is older than the freshness limit")
        } catch {
            guard token == generation else { return }
            pending = nil
            if error is CancellationError || (error as? URLError)?.code == .cancelled {
                updateFreshness(message: "Update cancelled"); return
            }
            failureCount = min(8, failureCount + 1)
            var delay = min(3600, 60 * pow(2, Double(failureCount - 1)))
            if case DataServiceError.rateLimited(let retry) = error { delay = max(delay, retry) }
            nextAllowedRefresh = now().addingTimeInterval(delay)
            // Arbitrary NSError descriptions can contain request URLs/secrets; do not publish them.
            let message = (error as? DataServiceError)?.errorDescription ?? "Connection unavailable"
            updateFreshness(message: message, failed: true)
        }
    }

    private var nextDelay: TimeInterval {
        var delay = max(1, min(configuration.refreshInterval, nextAllowedRefresh.timeIntervalSince(now())))
        if let cached = lastGood {
            let expiry = cached.observedAt.addingTimeInterval(configuration.staleGrace).timeIntervalSince(now())
            if expiry > 0 { delay = min(delay, expiry + 0.1) }
        }
        return delay
    }

    private func updateFreshness(message: String, failed: Bool = false) {
        guard let cached = lastGood, now().timeIntervalSince(cached.observedAt) <= configuration.staleGrace else {
            snapshot = nil; status = .offline(message); return
        }
        snapshot = cached
        status = !failed && failureCount == 0 && now().timeIntervalSince(cached.observedAt) <= configuration.cacheTTL
            ? .fresh : .stale(message)
    }

    deinit { polling?.cancel(); pending?.cancel() }
}

/// Object-key traversal only; at most eight keys. No expressions, wildcard or executable adapters.
public struct JSONNumberBinding: Codable, Equatable, Sendable {
    public var path: [String]
    public var inputMin: Double
    public var inputMax: Double
    public var outputMin: Double
    public var outputMax: Double
    public init(path: [String], inputMin: Double, inputMax: Double,
                outputMin: Double = 0, outputMax: Double = 1) {
        self.path = path; self.inputMin = inputMin; self.inputMax = inputMax
        self.outputMin = outputMin; self.outputMax = outputMax
    }
    func map(_ object: Any) throws -> Double {
        guard inputMin.isFinite, inputMax.isFinite, inputMin < inputMax, (inputMax - inputMin).isFinite,
              outputMin.isFinite, outputMax.isFinite, (0...1).contains(outputMin),
              (0...1).contains(outputMax) else { throw DataServiceError.invalidConfiguration }
        let value = try jsonNumber(try jsonValue(object, at: path), range: -Double.greatestFiniteMagnitude...Double.greatestFiniteMagnitude)
        let ratio = min(1, max(0, (value - inputMin) / (inputMax - inputMin)))
        return outputMin + ratio * (outputMax - outputMin)
    }
}

private func jsonValue(_ object: Any, at path: [String]) throws -> Any {
    guard (1...8).contains(path.count), path.allSatisfy({ !$0.isEmpty && $0.utf8.count <= 128 }) else {
        throw DataServiceError.invalidConfiguration
    }
    var current: Any = object
    for key in path {
        guard let next = (current as? [String: Any])?[key] else {
            throw DataServiceError.invalidResponse("Mapped JSON field is missing")
        }
        current = next
    }
    return current
}

public struct CustomEffectMapping: Codable, Equatable, Sendable {
    public var daylight: JSONNumberBinding?
    public var cloud: JSONNumberBinding?
    public var wind: JSONNumberBinding?
    public var intensity: JSONNumberBinding?
    public var colorPath: [String]?
    public init(daylight: JSONNumberBinding? = nil, cloud: JSONNumberBinding? = nil,
                wind: JSONNumberBinding? = nil, intensity: JSONNumberBinding? = nil, colorPath: [String]? = nil) {
        self.daylight = daylight; self.cloud = cloud; self.wind = wind
        self.intensity = intensity; self.colorPath = colorPath
    }

    public func apply(to data: Data) throws -> NativeEffectState {
        guard data.count <= DataHTTPClient.maximumBytes else { throw DataServiceError.responseTooLarge }
        let object = try JSONSerialization.jsonObject(with: data)
        var effect = NativeEffectState()
        if let daylight { effect.daylight = try daylight.map(object) }
        if let cloud { effect.cloud = try cloud.map(object) }
        if let wind { effect.wind = try wind.map(object) }
        if let intensity { effect.intensity = try intensity.map(object) }
        if let colorPath {
            guard let text = try jsonValue(object, at: colorPath) as? String,
                  let color = SceneColor(hex: text) else {
                throw DataServiceError.invalidResponse("Mapped color must be #RRGGBB")
            }
            effect.accent = color
        }
        return effect
    }
}

public struct CustomAPIConfiguration: Codable, Equatable, Sendable {
    public var endpoint: URL
    public var mapping: CustomEffectMapping
    public init(endpoint: URL, mapping: CustomEffectMapping) { self.endpoint = endpoint; self.mapping = mapping }
}

public final class CustomAPIClient: @unchecked Sendable {
    private let http: DataHTTPClient
    public init(http: DataHTTPClient = DataHTTPClient()) { self.http = http }
    deinit { http.close() }
    /// Caller resolves the selected Keychain credential. Tokens never enter saved configurations or scenes.
    public func fetch(configuration: CustomAPIConfiguration, bearerToken: String? = nil) async throws -> NativeEffectState {
        try configuration.mapping.apply(to: await http.fetch(configuration.endpoint, bearerToken: bearerToken))
    }
}
