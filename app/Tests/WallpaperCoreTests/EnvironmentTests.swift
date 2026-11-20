import XCTest
@testable import WallpaperCore

final class EnvironmentTests: XCTestCase {
    func testLiteralValuesAreNeverExpanded() throws {
        let parsed = try EnvironmentFile.parse(Data("# explicit selection only\nAPI_KEY='$(do-not-run)'\nMODEL=${UNCHANGED}\n".utf8))
        XCTAssertEqual(parsed["API_KEY"], "$(do-not-run)")
        XCTAssertEqual(parsed["MODEL"], "${UNCHANGED}")
    }
    func testInvalidOrOversizedInputsAreRejected() {
        for text in ["KEY=a\nKEY=b", "export KEY=a", "bad key=value", "KEY='unterminated"] {
            XCTAssertThrowsError(try EnvironmentFile.parse(Data(text.utf8)))
        }
        XCTAssertThrowsError(try EnvironmentFile.parse(Data(repeating: 65, count: 65_537)))
    }
}
