// A small assertion adapter for macOS Command Line Tools, which do not ship XCTest.
// This is NOT an XCTest implementation. run_checks.py compiles the repository's
// original test bodies with this adapter; normal XCTest sources remain unchanged.
// There are no expectations, performance tests, discovery magic or parallel cases.
import Foundation

class XCTestCase {
    func setUpWithError() throws {}
    func setUp() {}
    func tearDown() {}
    func tearDownWithError() throws {}
}

private struct PortableAssertionFailure: Error {}

final class PortableCheckResults {
    static let shared = PortableCheckResults()
    private let lock = NSLock()
    private var failures: [String] = []
    private(set) var passed = 0
    private(set) var failed = 0

    func record(_ text: String, file: StaticString = #filePath, line: UInt = #line) {
        lock.lock(); defer { lock.unlock() }
        failures.append("\(file):\(line): \(text)")
    }

    func capture(_ error: Error, stage: String) {
        if error is PortableAssertionFailure { return } // XCTUnwrap already recorded the assertion.
        record("\(stage) threw: \(error)")
    }

    private func beginCase() { lock.lock(); failures = []; lock.unlock() }
    private func caseFailures() -> [String] { lock.lock(); defer { lock.unlock() }; return failures }

    @MainActor func run(_ name: String, instance: XCTestCase,
                        body: () async throws -> Void) async {
        beginCase()
        do {
            try instance.setUpWithError()
            instance.setUp()
            try await body()
        } catch { capture(error, stage: "setup/test") }
        instance.tearDown()
        do { try instance.tearDownWithError() }
        catch { capture(error, stage: "teardown") }
        let messages = caseFailures()
        if messages.isEmpty {
            passed += 1; print("PASS \(name)")
        } else {
            failed += 1; print("FAIL \(name)")
            for message in messages { print("  " + message) }
        }
    }

    func finish() -> Int32 {
        print("Portable CLT checks: \(passed) passed, \(failed) failed, \(passed + failed) total. This was not an XCTest runtime run.")
        return failed == 0 && passed > 0 ? 0 : 1
    }
}

private func portableAssert(_ label: String, _ message: () -> String, file: StaticString, line: UInt,
                            condition: () throws -> Bool) {
    do {
        if try !condition() {
            let detail = message()
            PortableCheckResults.shared.record(label + (detail.isEmpty ? "" : ": " + detail), file: file, line: line)
        }
    } catch is PortableAssertionFailure { /* already recorded */ }
    catch { PortableCheckResults.shared.record(label + " threw: \(error)", file: file, line: line) }
}

func XCTFail(_ message: @autoclosure () -> String = "Explicit failure", file: StaticString = #filePath, line: UInt = #line) {
    PortableCheckResults.shared.record(message(), file: file, line: line)
}

func XCTAssertTrue(_ value: @autoclosure () throws -> Bool, _ message: @autoclosure () -> String = "",
                   file: StaticString = #filePath, line: UInt = #line) {
    portableAssert("XCTAssertTrue failed", message, file: file, line: line) { try value() }
}
func XCTAssertFalse(_ value: @autoclosure () throws -> Bool, _ message: @autoclosure () -> String = "",
                    file: StaticString = #filePath, line: UInt = #line) {
    portableAssert("XCTAssertFalse failed", message, file: file, line: line) { try !value() }
}
func XCTAssertEqual<T: Equatable>(_ lhs: @autoclosure () throws -> T, _ rhs: @autoclosure () throws -> T,
                                  _ message: @autoclosure () -> String = "", file: StaticString = #filePath, line: UInt = #line) {
    portableAssert("XCTAssertEqual failed", message, file: file, line: line) { try lhs() == rhs() }
}
func XCTAssertEqual<T: BinaryFloatingPoint>(_ lhs: @autoclosure () throws -> T, _ rhs: @autoclosure () throws -> T,
                                            accuracy: T, _ message: @autoclosure () -> String = "",
                                            file: StaticString = #filePath, line: UInt = #line) {
    portableAssert("XCTAssertEqual accuracy failed", message, file: file, line: line) {
        let a = try lhs(), b = try rhs()
        return accuracy >= 0 && (a == b || abs(a - b) <= accuracy)
    }
}
func XCTAssertNotEqual<T: Equatable>(_ lhs: @autoclosure () throws -> T, _ rhs: @autoclosure () throws -> T,
                                     _ message: @autoclosure () -> String = "", file: StaticString = #filePath, line: UInt = #line) {
    portableAssert("XCTAssertNotEqual failed", message, file: file, line: line) { try lhs() != rhs() }
}
func XCTAssertNil<T>(_ value: @autoclosure () throws -> T?, _ message: @autoclosure () -> String = "",
                     file: StaticString = #filePath, line: UInt = #line) {
    portableAssert("XCTAssertNil failed", message, file: file, line: line) { try value() == nil }
}
func XCTAssertNotNil<T>(_ value: @autoclosure () throws -> T?, _ message: @autoclosure () -> String = "",
                        file: StaticString = #filePath, line: UInt = #line) {
    portableAssert("XCTAssertNotNil failed", message, file: file, line: line) { try value() != nil }
}
func XCTAssertLessThan<T: Comparable>(_ lhs: @autoclosure () throws -> T, _ rhs: @autoclosure () throws -> T,
                                     _ message: @autoclosure () -> String = "", file: StaticString = #filePath, line: UInt = #line) {
    portableAssert("XCTAssertLessThan failed", message, file: file, line: line) { try lhs() < rhs() }
}
func XCTAssertLessThanOrEqual<T: Comparable>(_ lhs: @autoclosure () throws -> T, _ rhs: @autoclosure () throws -> T,
                                            _ message: @autoclosure () -> String = "", file: StaticString = #filePath, line: UInt = #line) {
    portableAssert("XCTAssertLessThanOrEqual failed", message, file: file, line: line) { try lhs() <= rhs() }
}
func XCTAssertGreaterThan<T: Comparable>(_ lhs: @autoclosure () throws -> T, _ rhs: @autoclosure () throws -> T,
                                        _ message: @autoclosure () -> String = "", file: StaticString = #filePath, line: UInt = #line) {
    portableAssert("XCTAssertGreaterThan failed", message, file: file, line: line) { try lhs() > rhs() }
}
func XCTAssertGreaterThanOrEqual<T: Comparable>(_ lhs: @autoclosure () throws -> T, _ rhs: @autoclosure () throws -> T,
                                               _ message: @autoclosure () -> String = "", file: StaticString = #filePath, line: UInt = #line) {
    portableAssert("XCTAssertGreaterThanOrEqual failed", message, file: file, line: line) { try lhs() >= rhs() }
}

func XCTAssertThrowsError<T>(_ expression: @autoclosure () throws -> T, _ message: @autoclosure () -> String = "",
                             file: StaticString = #filePath, line: UInt = #line,
                             _ errorHandler: (Error) -> Void = { _ in }) {
    do {
        _ = try expression()
        PortableCheckResults.shared.record("XCTAssertThrowsError failed: " + message(), file: file, line: line)
    } catch { errorHandler(error) }
}

func XCTUnwrap<T>(_ expression: @autoclosure () throws -> T?, _ message: @autoclosure () -> String = "",
                  file: StaticString = #filePath, line: UInt = #line) throws -> T {
    do {
        if let value = try expression() { return value }
        PortableCheckResults.shared.record("XCTUnwrap found nil: " + message(), file: file, line: line)
    } catch is PortableAssertionFailure { throw PortableAssertionFailure() }
    catch { PortableCheckResults.shared.record("XCTUnwrap threw: \(error)", file: file, line: line) }
    throw PortableAssertionFailure()
}
