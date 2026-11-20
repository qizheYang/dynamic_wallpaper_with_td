import Foundation
import CryptoKit
import Darwin
import ImageIO

public struct WallpaperAsset: Codable, Identifiable, Equatable, Sendable {
    public let id: String
    public let title: String
    public let directory: URL
    public var videoURL: URL { directory.appendingPathComponent("loop.mp4") }
    public var coverURL: URL { directory.appendingPathComponent("cover.png") }
    public init(id: String, title: String, directory: URL) {
        self.id = id; self.title = title; self.directory = directory
    }
}

public enum AssetLibraryError: LocalizedError {
    case invalid(String)
    public var errorDescription: String? {
        switch self { case .invalid(let message): return message }
    }
}

/// Imports only the four inert files in pipeline manifest v1. No package code is executed.
/// Hashing and copying run off the main actor; a same-volume rename publishes a complete copy.
public final class AssetLibrary: @unchecked Sendable {
    public let root: URL
    private let lock = NSLock()
    private let manifestLimit = 1_048_576
    public init(root: URL) { self.root = Self.systemCanonicalURL(root) }

    public func list() throws -> [WallpaperAsset] {
        lock.lock(); defer { lock.unlock() }
        try prepareRoot()
        return try FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)
            .filter { UUID(uuidString: $0.lastPathComponent) != nil }
            .compactMap { directory in
                // A corrupt entry must not make the remaining library inaccessible.
                try? readOwnedAsset(directory)
            }.sorted { $0.title.localizedStandardCompare($1.title) == .orderedAscending }
    }

    public func importBundle(at source: URL) async throws -> WallpaperAsset {
        try Task.checkCancellation()
        let task = Task.detached(priority: .utility) { try self.importSynchronously(source) }
        return try await withTaskCancellationHandler(operation: { try await task.value },
                                                     onCancel: { task.cancel() })
    }

    /// The caller must stop playback before removing its currently selected asset.
    public func remove(_ asset: WallpaperAsset) throws {
        lock.lock(); defer { lock.unlock() }
        try prepareRoot()
        guard UUID(uuidString: asset.id) != nil,
              asset.directory.standardizedFileURL == root.appendingPathComponent(asset.id).standardizedFileURL else {
            throw AssetLibraryError.invalid("Asset is not owned by this library.")
        }
        _ = try readOwnedAsset(asset.directory)
        try FileManager.default.removeItem(at: asset.directory)
    }

    private func prepareRoot() throws {
        try Self.rejectSymlinkComponents(root)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        try Self.rejectSymlinkComponents(root)
        let values = try root.resourceValues(forKeys: [.isDirectoryKey])
        guard values.isDirectory == true else { throw AssetLibraryError.invalid("Library is not a directory.") }
    }

    private func readOwnedAsset(_ directory: URL) throws -> WallpaperAsset {
        let fd = try Self.openDirectory(directory)
        defer { Darwin.close(fd) }
        let metadata = try Self.readFile(fd, "asset.json", limit: 16_384)
        let asset = try JSONDecoder().decode(WallpaperAsset.self, from: metadata)
        guard asset.id == directory.lastPathComponent, asset.title.count <= 256 else {
            throw AssetLibraryError.invalid("Invalid library entry.")
        }
        for file in ["loop.mp4", "cover.png", "manifest.json"] {
            let descriptor = try Self.openRegular(fd, file, limit: file == "loop.mp4" ? 2_147_483_648 : 33_554_432)
            Darwin.close(descriptor)
        }
        return WallpaperAsset(id: asset.id, title: asset.title, directory: directory)
    }

    private func importSynchronously(_ source: URL) throws -> WallpaperAsset {
        lock.lock(); defer { lock.unlock() }
        try Task.checkCancellation()
        guard source.isFileURL else { throw AssetLibraryError.invalid("Select a local bundle folder.") }
        let sourceFD = try Self.openDirectory(source)
        defer { Darwin.close(sourceFD) }
        let manifestData = try Self.readFile(sourceFD, "manifest.json", limit: manifestLimit)
        let manifest = try JSONDecoder().decode(BundleManifest.self, from: manifestData)
        try manifest.validate()
        try prepareRoot()
        let id = UUID().uuidString
        let staging = root.appendingPathComponent(".import-\(id)", isDirectory: true)
        let destination = root.appendingPathComponent(id, isDirectory: true)
        try FileManager.default.createDirectory(at: staging, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        var published = false
        defer { if !published { try? FileManager.default.removeItem(at: staging) } }
        try copyVerified(sourceFD, name: manifest.video.file, to: staging,
                         digest: manifest.video.sha256, limit: 2_147_483_648, exactBytes: manifest.video.bytes)
        try copyVerified(sourceFD, name: manifest.cover.file, to: staging,
                         digest: manifest.cover.sha256, limit: 33_554_432)
        try copyVerified(sourceFD, name: manifest.source.frames_file, to: staging,
                         digest: manifest.source.frames_digest, limit: 16_777_216)
        let coverURL = staging.appendingPathComponent("cover.png")
        guard let image = CGImageSourceCreateWithURL(coverURL as CFURL, nil),
              CGImageSourceGetType(image) as String? == "public.png",
              let properties = CGImageSourceCopyPropertiesAtIndex(image, 0, nil) as? [CFString: Any],
              let width = properties[kCGImagePropertyPixelWidth] as? Int,
              let height = properties[kCGImagePropertyPixelHeight] as? Int,
              width > 0, height > 0, width <= 8192, height <= 8192, width * height <= 33_554_432 else {
            throw AssetLibraryError.invalid("The static cover is not a supported PNG image.")
        }
        try manifestData.write(to: staging.appendingPathComponent("manifest.json"), options: .withoutOverwriting)
        let asset = WallpaperAsset(id: id, title: manifest.job_id, directory: destination)
        try JSONEncoder().encode(asset).write(to: staging.appendingPathComponent("asset.json"), options: .withoutOverwriting)
        try Task.checkCancellation()
        try FileManager.default.moveItem(at: staging, to: destination)
        published = true
        return asset
    }

    private func copyVerified(_ directory: Int32, name: String, to destination: URL,
                              digest: String, limit: Int, exactBytes: Int? = nil) throws {
        let input = try Self.openRegular(directory, name, limit: limit)
        defer { Darwin.close(input) }
        let output = Darwin.open(destination.appendingPathComponent(name).path,
                                 O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0o600)
        guard output >= 0 else { throw AssetLibraryError.invalid("Cannot create imported media.") }
        defer { Darwin.close(output) }
        var hasher = SHA256()
        var buffer = [UInt8](repeating: 0, count: 1_048_576)
        var total = 0
        while true {
            try Task.checkCancellation()
            let count = Darwin.read(input, &buffer, buffer.count)
            if count < 0 { if errno == EINTR { continue }; throw AssetLibraryError.invalid("Cannot read \(name).") }
            if count == 0 { break }
            total += count
            guard total <= limit else { throw AssetLibraryError.invalid("\(name) exceeds the import size limit.") }
            let data = Data(buffer.prefix(count))
            hasher.update(data: data)
            try data.withUnsafeBytes { bytes in
                var offset = 0
                while offset < count {
                    let written = Darwin.write(output, bytes.baseAddress!.advanced(by: offset), count - offset)
                    if written < 0 && errno == EINTR { continue }
                    guard written > 0 else { throw AssetLibraryError.invalid("Cannot copy \(name).") }
                    offset += written
                }
            }
        }
        let actual = hasher.finalize().map { String(format: "%02x", $0) }.joined()
        guard actual == digest, total > 0, exactBytes == nil || exactBytes == total else {
            throw AssetLibraryError.invalid("\(name) does not match its manifest checksum or size.")
        }
    }

    private static func openRegular(_ directory: Int32, _ name: String, limit: Int) throws -> Int32 {
        guard !name.isEmpty, name != ".", name != "..", !name.contains("/"), !name.contains("\\"),
              !name.utf8.contains(0) else { throw AssetLibraryError.invalid("Unsafe bundle file path.") }
        let fd = name.withCString { openat(directory, $0, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC) }
        guard fd >= 0 else { throw AssetLibraryError.invalid("Missing or unsafe bundle file: \(name).") }
        var info = stat()
        guard fstat(fd, &info) == 0, info.st_mode & S_IFMT == S_IFREG,
              info.st_size > 0, info.st_size <= limit else {
            Darwin.close(fd)
            throw AssetLibraryError.invalid("Invalid type or size for \(name).")
        }
        return fd
    }

    private static func readFile(_ directory: Int32, _ name: String, limit: Int) throws -> Data {
        let fd = try openRegular(directory, name, limit: limit)
        let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        let data = try handle.read(upToCount: limit + 1) ?? Data()
        guard !data.isEmpty, data.count <= limit else { throw AssetLibraryError.invalid("\(name) is too large.") }
        return data
    }

    private static func rejectSymlinkComponents(_ url: URL) throws {
        guard url.isFileURL else { throw AssetLibraryError.invalid("Expected a local file URL.") }
        var component = URL(fileURLWithPath: "/", isDirectory: true)
        for part in systemCanonicalURL(url).pathComponents.dropFirst() {
            component.appendPathComponent(part)
            var info = stat()
            if lstat(component.path, &info) == 0 {
                if info.st_mode & S_IFMT == S_IFLNK { throw AssetLibraryError.invalid("Symbolic links are not accepted in bundle or library paths.") }
            } else if errno != ENOENT { throw AssetLibraryError.invalid("Cannot inspect the selected path.") }
        }
    }

    /// Foundation may shorten /private/var back to /var even after resolvingSymlinksInPath().
    /// Expand only the three standard macOS root aliases, never a selected bundle/member link.
    /// Do not call standardizedFileURL on the result: it can reintroduce those aliases.
    private static func systemCanonicalURL(_ url: URL) -> URL {
        guard url.isFileURL else { return url }
        let path = url.standardizedFileURL.path
        let components = path.split(separator: "/")
        if let first = components.first, ["var", "tmp", "etc"].contains(String(first)) {
            return URL(fileURLWithPath: "/private" + path, isDirectory: true)
        }
        return URL(fileURLWithPath: path, isDirectory: true)
    }

    /// Pin each directory component before descending, so swapping a path ancestor for a
    /// symlink cannot redirect manifest/media reads between validation and open().
    private static func openDirectory(_ url: URL) throws -> Int32 {
        guard url.isFileURL else { throw AssetLibraryError.invalid("Expected a local file URL.") }
        var fd = Darwin.open("/", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard fd >= 0 else { throw AssetLibraryError.invalid("Cannot open the filesystem root.") }
        do {
            for component in systemCanonicalURL(url).pathComponents.dropFirst() {
                let child = component.withCString { openat(fd, $0, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC) }
                guard child >= 0 else { throw AssetLibraryError.invalid("Missing directory or symbolic link in bundle/library path.") }
                Darwin.close(fd)
                fd = child
            }
            return fd
        } catch { Darwin.close(fd); throw error }
    }
}

private struct BundleManifest: Decodable {
    struct Video: Decodable {
        let file: String; let sha256: String; let bytes: Int; let codec: String; let pix_fmt: String
        let width: Int; let height: Int; let frame_rate: String; let frame_count: Int
        let duration_seconds: Double; let audio: Bool
    }
    struct Cover: Decodable { let file: String; let sha256: String }
    struct Source: Decodable { let frames_file: String; let frames_digest: String }
    struct Check: Decodable { let ok: Bool }
    struct Sequence: Decodable { let ok: Bool; let expected_frames: Int; let found_frames: Int }
    struct Validation: Decodable {
        let sequence: Sequence; let probe: [Check]; let decode: Check
        let round_trip: Check; let loop_seam: Check
    }
    let manifest_version: Int; let job_id: String; let published_utc: String
    let video: Video; let cover: Cover; let source: Source; let validation: Validation

    func validate() throws {
        let checksums = [video.sha256, cover.sha256, source.frames_digest]
        guard manifest_version == 1, !job_id.isEmpty, job_id.count <= 256, !published_utc.isEmpty,
              video.file == "loop.mp4", cover.file == "cover.png", source.frames_file == "frames.sha256.txt",
              checksums.allSatisfy({ $0.count == 64 && $0.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) } }),
              video.bytes > 0, video.bytes <= 2_147_483_648,
              video.codec == "h264", video.pix_fmt == "yuv420p", !video.audio,
              video.width > 0, video.width <= 4096, video.height > 0, video.height <= 2160,
              video.frame_count > 0, video.frame_count <= 36_000,
              video.duration_seconds.isFinite, video.duration_seconds > 0, video.duration_seconds <= 600,
              validation.sequence.ok, validation.sequence.expected_frames == video.frame_count,
              validation.sequence.found_frames == video.frame_count,
              !validation.probe.isEmpty, validation.probe.allSatisfy({ $0.ok }),
              validation.decode.ok, validation.round_trip.ok, validation.loop_seam.ok else {
            throw AssetLibraryError.invalid("Bundle is incomplete or outside the supported validated H.264 profile.")
        }
        let rate = video.frame_rate.split(separator: "/", omittingEmptySubsequences: false)
        guard rate.count == 2, let numerator = Double(rate[0]), let denominator = Double(rate[1]),
              numerator.isFinite, denominator.isFinite, numerator > 0, denominator > 0,
              numerator / denominator <= 60,
              abs(Double(video.frame_count) / (numerator / denominator) - video.duration_seconds) < 0.1 else {
            throw AssetLibraryError.invalid("Invalid video timing in manifest.")
        }
    }
}
