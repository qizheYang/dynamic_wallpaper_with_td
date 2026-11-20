import XCTest
@testable import WallpaperCore

final class DataTests: XCTestCase {
    private let instant = Date(timeIntervalSince1970: 1_794_000_000)

    private func fixture(code: Int = 61, day: Int = 0, age: TimeInterval = 120,
                         unit: String = "m/s") throws -> Data {
        try JSONSerialization.data(withJSONObject: [
            "timezone": "America/Los_Angeles", "utc_offset_seconds": -28800,
            "current_units": ["time": "unixtime", "interval": "seconds", "temperature_2m": "°C",
                              "wind_speed_10m": unit, "precipitation": "mm", "cloud_cover": "%",
                              "is_day": "", "weather_code": "wmo code"],
            "current": ["time": instant.timeIntervalSince1970 - age, "interval": 900,
                        "temperature_2m": 17.5, "wind_speed_10m": 4.0, "precipitation": 1.2,
                        "cloud_cover": 80, "is_day": day, "weather_code": code]
        ])
    }

    func testWeatherNormalizesUnitsNightAndCategories() throws {
        let snapshot = try OpenMeteoClient.decode(fixture(), now: instant)
        XCTAssertEqual(snapshot.kind, .rain); XCTAssertFalse(snapshot.isDay)
        XCTAssertEqual(snapshot.temperatureCelsius, 17.5)
        XCTAssertEqual(snapshot.windMetresPerSecond, 4)
        XCTAssertEqual(snapshot.cloudFraction, 0.8)
        XCTAssertEqual(snapshot.observedAt, instant.addingTimeInterval(-120))
        let scene = NativeEffectState(weather: snapshot)
        XCTAssertEqual(scene.daylight, 0); XCTAssertGreaterThan(scene.intensity, 0)
        XCTAssertEqual(try WeatherKind.from(code: 99), .storm)
        XCTAssertEqual(try WeatherKind.from(code: 85), .snow)
        XCTAssertEqual(try WeatherKind.from(code: 48), .fog)
        XCTAssertThrowsError(try WeatherKind.from(code: 13))
    }

    func testWeatherRejectsBadUnitsStaleFutureAndMissingValues() throws {
        XCTAssertThrowsError(try OpenMeteoClient.decode(fixture(unit: "km/h"), now: instant))
        XCTAssertThrowsError(try OpenMeteoClient.decode(fixture(age: 7201), now: instant))
        XCTAssertThrowsError(try OpenMeteoClient.decode(fixture(age: -301), now: instant))
        XCTAssertThrowsError(try OpenMeteoClient.decode(fixture(day: 2), now: instant))
        XCTAssertThrowsError(try OpenMeteoClient.decode(Data("{}".utf8), now: instant))
        XCTAssertThrowsError(try OpenMeteoClient.decode(Data(repeating: 32, count: DataHTTPClient.maximumBytes + 1), now: instant))
    }

    func testQueryUsesExplicitUnitsAndEpochTime() throws {
        let parts = URLComponents(url: try OpenMeteoClient.requestURL(configuration: .losAngeles), resolvingAgainstBaseURL: false)!
        let query = Dictionary(uniqueKeysWithValues: parts.queryItems!.map { ($0.name, $0.value!) })
        XCTAssertEqual(parts.host, "api.open-meteo.com")
        XCTAssertEqual(query["timeformat"], "unixtime")
        XCTAssertEqual(query["timezone"], "America/Los_Angeles")
        XCTAssertEqual(query["wind_speed_unit"], "ms")
        XCTAssertNil(query["apikey"])
        XCTAssertThrowsError(try WeatherConfiguration(latitude: .nan).validate())
        XCTAssertThrowsError(try WeatherConfiguration(refreshInterval: 1).validate())
    }

    func testCustomMappingClampsAndDecodesColorWithoutExpressions() throws {
        let mapping = CustomEffectMapping(wind: JSONNumberBinding(path: ["current", "wind"], inputMin: 0, inputMax: 20),
                                         intensity: JSONNumberBinding(path: ["value"], inputMin: 0, inputMax: 100),
                                         colorPath: ["color"])
        let state = try mapping.apply(to: Data(##"{"current":{"wind":40},"value":-8,"color":"#3366CC"}"##.utf8))
        XCTAssertEqual(state.wind, 1); XCTAssertEqual(state.intensity, 0)
        XCTAssertEqual(state.accent, SceneColor(red: 0.2, green: 0.4, blue: 0.8))
        XCTAssertThrowsError(try mapping.apply(to: Data(##"{"current":{"wind":true},"value":50,"color":"#3366CC"}"##.utf8)))
        XCTAssertThrowsError(try mapping.apply(to: Data(##"{"current":{"wind":4},"value":50,"color":"red"}"##.utf8)))
        XCTAssertThrowsError(try mapping.apply(to: Data(##"{"current":{},"value":50,"color":"#3366CC"}"##.utf8)))
        let invalid = CustomEffectMapping(wind: JSONNumberBinding(path: Array(repeating: "a", count: 9), inputMin: 0, inputMax: 1))
        XCTAssertThrowsError(try invalid.apply(to: Data("{}".utf8)))
        let overflow = CustomEffectMapping(wind: JSONNumberBinding(path: ["wind"], inputMin: -.greatestFiniteMagnitude, inputMax: .greatestFiniteMagnitude))
        XCTAssertThrowsError(try overflow.apply(to: Data(##"{"wind":1}"##.utf8)))
    }

    func testRedirectOriginDoesNotForwardAuthToOtherHostPortOrScheme() {
        let origin = URL(string: "https://api.example.com/data")!
        XCTAssertTrue(DataHTTPClient.sameOrigin(origin, URL(string: "https://api.example.com:443/new")!))
        XCTAssertFalse(DataHTTPClient.sameOrigin(origin, URL(string: "https://other.example.com/new")!))
        XCTAssertFalse(DataHTTPClient.sameOrigin(origin, URL(string: "https://api.example.com:8443/new")!))
        XCTAssertFalse(DataHTTPClient.sameOrigin(origin, URL(string: "http://api.example.com/new")!))
        XCTAssertEqual(DataHTTPClient.retryDelay("1800", now: instant), 1800)
        XCTAssertEqual(DataHTTPClient.retryDelay("Sun, 06 Nov 1994 08:49:37 GMT", now: instant), 60)
        XCTAssertEqual(DataHTTPClient.retryDelay("infinity", now: instant), 900)
    }

    @MainActor func testControllerCachesRetainsLastGoodAndEventuallyFallsBack() async throws {
        let sample = try OpenMeteoClient.decode(fixture(), now: instant)
        let client = StubWeatherClient(sample)
        let clock = TestClock(instant)
        let controller = WeatherController(client: client, now: { clock.date })
        await controller.refresh()
        XCTAssertEqual(controller.status, .fresh)
        await controller.refresh()
        let cachedCount = await client.calls
        XCTAssertEqual(cachedCount, 1, "cache prevents redundant calls")
        await client.fail(.httpStatus(503))
        clock.date = instant.addingTimeInterval(901)
        await controller.refresh()
        XCTAssertEqual(controller.snapshot, sample)
        guard case .stale = controller.status else { return XCTFail("last good data must be marked stale") }
        clock.date = instant.addingTimeInterval(8000)
        await controller.refresh()
        XCTAssertNil(controller.snapshot)
        guard case .offline = controller.status else { return XCTFail("expired data must use offline fallback") }
        controller.stop()
        XCTAssertEqual(controller.status, .paused)
    }

    @MainActor func testRateLimitBlocksManualRefreshAndStopIgnoresLateResponse() async throws {
        let sample = try OpenMeteoClient.decode(fixture(), now: instant)
        let client = StubWeatherClient(sample)
        await client.fail(.rateLimited(1800))
        let clock = TestClock(instant)
        let controller = WeatherController(client: client, now: { clock.date })
        await controller.refresh()
        clock.date = instant.addingTimeInterval(900)
        await controller.refresh(force: true)
        let limitedCount = await client.calls
        XCTAssertEqual(limitedCount, 1)

        let waiting = SuspendedWeatherClient(sample)
        let cancelled = WeatherController(client: waiting, now: { clock.date })
        let refresh = Task { await cancelled.refresh() }
        await waiting.waitUntilStarted()
        cancelled.stop()
        await waiting.finish()
        await refresh.value
        XCTAssertNil(cancelled.snapshot)
        XCTAssertEqual(cancelled.status, .paused)
    }
}

@MainActor private final class TestClock {
    var date: Date
    init(_ date: Date) { self.date = date }
}

private actor StubWeatherClient: WeatherClient {
    let snapshot: WeatherSnapshot
    var calls = 0
    var failure: DataServiceError?
    init(_ snapshot: WeatherSnapshot) { self.snapshot = snapshot }
    func fail(_ error: DataServiceError) { failure = error }
    func fetch(configuration: WeatherConfiguration) async throws -> WeatherSnapshot {
        calls += 1
        if let failure { throw failure }
        return snapshot
    }
}

private actor SuspendedWeatherClient: WeatherClient {
    let snapshot: WeatherSnapshot
    var continuation: CheckedContinuation<WeatherSnapshot, Never>?
    init(_ snapshot: WeatherSnapshot) { self.snapshot = snapshot }
    func fetch(configuration: WeatherConfiguration) async throws -> WeatherSnapshot {
        await withCheckedContinuation { continuation = $0 }
    }
    func waitUntilStarted() async {
        while continuation == nil { await Task.yield() }
    }
    func finish() { continuation?.resume(returning: snapshot); continuation = nil }
}
