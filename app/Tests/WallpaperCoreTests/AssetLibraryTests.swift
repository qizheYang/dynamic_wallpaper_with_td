import XCTest
import CryptoKit
@testable import WallpaperCore

final class AssetLibraryTests: XCTestCase {
    private var temporary: URL!
    override func setUpWithError() throws {
        temporary = FileManager.default.temporaryDirectory.resolvingSymlinksInPath()
            .appendingPathComponent("wallpaper-library-tests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: temporary, withIntermediateDirectories: true)
    }
    override func tearDownWithError() throws { try FileManager.default.removeItem(at: temporary) }

    func testImportsOwnedCopyAndSurvivesSourceRemoval() async throws {
        let source = try fixture()
        let library = AssetLibrary(root: temporary.appendingPathComponent("library"))
        let asset = try await library.importBundle(at: source)
        XCTAssertEqual(asset.title, "test_wallpaper")
        XCTAssertNotEqual(asset.directory, source)
        XCTAssertEqual(try Data(contentsOf: asset.videoURL), Data("fake-video-for-import-tests".utf8))
        try FileManager.default.removeItem(at: source)
        XCTAssertEqual(try library.list(), [asset])
        XCTAssertEqual(Set(try FileManager.default.contentsOfDirectory(atPath: asset.directory.path)),
                       ["loop.mp4", "cover.png", "frames.sha256.txt", "manifest.json", "asset.json"])
        try library.remove(asset)
        XCTAssertTrue(try library.list().isEmpty)
    }

    func testChecksumFailureDoesNotPublishOrLeavePartialCopy() async throws {
        let source = try fixture()
        try Data("changed".utf8).write(to: source.appendingPathComponent("loop.mp4"))
        let library = AssetLibrary(root: temporary.appendingPathComponent("library"))
        await assertImportFails(library, source)
        XCTAssertTrue(try library.list().isEmpty)
        XCTAssertTrue(try FileManager.default.contentsOfDirectory(atPath: library.root.path).isEmpty)
    }

    func testRejectsTraversalAndIncompleteValidation() async throws {
        for badFile in ["../outside.mp4", "/tmp/loop.mp4", "folder/loop.mp4", "loop.mp4\u{0}.mp4"] {
            let source = try fixture(name: UUID().uuidString)
            try mutateManifest(source) { json in
                var video = json["video"] as! [String: Any]; video["file"] = badFile; json["video"] = video
            }
            await assertImportFails(AssetLibrary(root: temporary.appendingPathComponent("library")), source)
        }
        let source = try fixture(name: "incomplete")
        try mutateManifest(source) { json in
            var validation = json["validation"] as! [String: Any]
            validation["decode"] = ["ok": false]
            json["validation"] = validation
        }
        await assertImportFails(AssetLibrary(root: temporary.appendingPathComponent("library")), source)
    }

    func testRejectsFileAndDirectorySymlinks() async throws {
        let source = try fixture()
        let original = source.appendingPathComponent("loop.mp4")
        let outside = temporary.appendingPathComponent("outside.mp4")
        try FileManager.default.moveItem(at: original, to: outside)
        try FileManager.default.createSymbolicLink(at: original, withDestinationURL: outside)
        let library = AssetLibrary(root: temporary.appendingPathComponent("library"))
        await assertImportFails(library, source)
        let linked = temporary.appendingPathComponent("linked")
        try FileManager.default.createSymbolicLink(at: linked, withDestinationURL: source)
        await assertImportFails(library, linked)
    }

    func testAcceptsStandardMacOSTemporaryDirectoryAliases() async throws {
        // Foundation currently returns /var here even after resolvingSymlinksInPath().
        let source = try fixture()
        let privatePath = source.path.hasPrefix("/var/") ? "/private" + source.path : source.path
        let aliasPath = privatePath.replacingOccurrences(of: "/private/var/", with: "/var/")
        let library = AssetLibrary(root: temporary.appendingPathComponent("library"))
        let asset = try await library.importBundle(at: URL(fileURLWithPath: aliasPath))
        XCTAssertEqual(try library.list(), [asset])
        let second = try await library.importBundle(at: URL(fileURLWithPath: privatePath))
        XCTAssertEqual(try library.list().count, 2)
        XCTAssertNotEqual(asset.id, second.id)
    }

    func testRejectsArbitrarySymlinkAncestor() async throws {
        let source = try fixture()
        let ancestor = temporary.appendingPathComponent("shortcut")
        try FileManager.default.createSymbolicLink(at: ancestor, withDestinationURL: temporary)
        let redirected = ancestor.appendingPathComponent(source.lastPathComponent)
        await assertImportFails(AssetLibrary(root: temporary.appendingPathComponent("library")), redirected)
    }

    func testRejectsOversizedManifestAndInvalidCover() async throws {
        let source = try fixture()
        try Data(repeating: 32, count: 1_048_577).write(to: source.appendingPathComponent("manifest.json"))
        let library = AssetLibrary(root: temporary.appendingPathComponent("library"))
        await assertImportFails(library, source)
        let invalid = try fixture(name: "invalid-cover")
        let bytes = Data("not-an-image".utf8)
        try bytes.write(to: invalid.appendingPathComponent("cover.png"))
        try mutateManifest(invalid) { json in
            var cover = json["cover"] as! [String: Any]; cover["sha256"] = digest(bytes); json["cover"] = cover
        }
        await assertImportFails(library, invalid)
        XCTAssertTrue(try library.list().isEmpty)
    }

    func testCancellingImportDoesNotPublish() async throws {
        let source = try fixture()
        let library = AssetLibrary(root: temporary.appendingPathComponent("library"))
        let task = Task { try await library.importBundle(at: source) }
        task.cancel()
        do { _ = try await task.value; XCTFail("Cancelled import succeeded") }
        catch { XCTAssertTrue(error is CancellationError) }
        XCTAssertTrue(try library.list().isEmpty)
    }

    func testMetadataCannotRedirectLibraryOrRemovalOutsideRoot() async throws {
        let source = try fixture()
        let library = AssetLibrary(root: temporary.appendingPathComponent("library"))
        let asset = try await library.importBundle(at: source)
        let altered = WallpaperAsset(id: asset.id, title: asset.title, directory: source)
        try JSONEncoder().encode(altered).write(to: asset.directory.appendingPathComponent("asset.json"))
        XCTAssertEqual(try library.list().first?.directory, asset.directory)
        XCTAssertThrowsError(try library.remove(altered))
        XCTAssertTrue(FileManager.default.fileExists(atPath: source.path))
    }

    private func assertImportFails(_ library: AssetLibrary, _ source: URL,
                                   file: StaticString = #filePath, line: UInt = #line) async {
        do { _ = try await library.importBundle(at: source); XCTFail("Invalid bundle accepted", file: file, line: line) }
        catch { /* expected */ }
    }

    private func fixture(name: String = "bundle") throws -> URL {
        let directory = temporary.appendingPathComponent(name)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false)
        let video = Data("fake-video-for-import-tests".utf8)
        let cover = Data(base64Encoded: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")!
        let frames = Data("\(digest(cover))  frame_000000.png\n".utf8)
        try video.write(to: directory.appendingPathComponent("loop.mp4"))
        try cover.write(to: directory.appendingPathComponent("cover.png"))
        try frames.write(to: directory.appendingPathComponent("frames.sha256.txt"))
        let manifest: [String: Any] = [
            "manifest_version": 1, "job_id": "test_wallpaper", "published_utc": "2026-11-19T00:00:00Z",
            "video": ["file": "loop.mp4", "sha256": digest(video), "bytes": video.count,
                      "codec": "h264", "pix_fmt": "yuv420p", "width": 1280, "height": 720,
                      "frame_rate": "24/1", "frame_count": 480, "duration_seconds": 20, "audio": false],
            "cover": ["file": "cover.png", "sha256": digest(cover)],
            "source": ["frames_file": "frames.sha256.txt", "frames_digest": digest(frames)],
            "validation": ["sequence": ["ok": true, "expected_frames": 480, "found_frames": 480],
                           "probe": [["ok": true]], "decode": ["ok": true],
                           "round_trip": ["ok": true], "loop_seam": ["ok": true]]]
        try JSONSerialization.data(withJSONObject: manifest).write(to: directory.appendingPathComponent("manifest.json"))
        return directory
    }

    private func mutateManifest(_ source: URL, mutate: (inout [String: Any]) -> Void) throws {
        let file = source.appendingPathComponent("manifest.json")
        var json = try JSONSerialization.jsonObject(with: Data(contentsOf: file)) as! [String: Any]
        mutate(&json)
        try JSONSerialization.data(withJSONObject: json).write(to: file)
    }
}

private func digest(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
