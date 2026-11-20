import XCTest
@testable import WallpaperCore

final class PlaybackTests: XCTestCase {
    func testEachReasonIndependentlyBlocksPlayback() {
        for reason in PauseReason.allCases {
            var policy = readyPolicy()
            policy.set(reason, active: true)
            XCTAssertFalse(policy.permitsPlayback, reason.rawValue)
            policy.set(reason, active: false)
            XCTAssertTrue(policy.permitsPlayback, reason.rawValue)
        }
    }

    func testUncoverAndWakeDoNotClearUserPowerOrSessionPauses() {
        var policy = readyPolicy()
        policy.reasons = [.user, .occluded, .onBattery, .displaySleeping, .sessionInactive]
        for reason in [PauseReason.occluded, .displaySleeping, .onBattery, .sessionInactive] {
            policy.set(reason, active: false)
            XCTAssertFalse(policy.permitsPlayback)
            XCTAssertTrue(policy.reasons.contains(.user))
        }
        policy.set(.user, active: false)
        XCTAssertTrue(policy.permitsPlayback)
    }

    func testUnknownConditionsAndResourceFailureFailClosed() {
        var policy = readyPolicy()
        policy.reasons = [.sessionUnknown, .powerUnknown]
        policy.set(.sessionUnknown, active: false)
        XCTAssertFalse(policy.permitsPlayback)
        policy.set(.powerUnknown, active: false)
        XCTAssertTrue(policy.permitsPlayback)
        policy.assetAvailable = false
        XCTAssertFalse(policy.permitsPlayback)
        policy.assetAvailable = true
        policy.screenAvailable = false
        XCTAssertFalse(policy.permitsPlayback)
        policy.screenAvailable = true
        policy.failed = true
        XCTAssertFalse(policy.permitsPlayback)
    }

    func testAllPairsRemainBlockedUntilBothClear() {
        for a in PauseReason.allCases {
            for b in PauseReason.allCases where a != b {
                var policy = readyPolicy()
                policy.reasons = [a, b]
                policy.set(a, active: false)
                XCTAssertEqual(policy.reasons, [b])
                XCTAssertFalse(policy.permitsPlayback)
                policy.set(b, active: false)
                XCTAssertTrue(policy.permitsPlayback)
            }
        }
    }

    private func readyPolicy() -> PlaybackPolicy {
        var policy = PlaybackPolicy()
        policy.assetAvailable = true
        policy.screenAvailable = true
        return policy
    }
}
