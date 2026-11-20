// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "WallpaperStudio",
    platforms: [.macOS(.v13)],
    products: [
        .library(name: "WallpaperCore", targets: ["WallpaperCore"]),
        .executable(name: "WallpaperStudio", targets: ["WallpaperStudio"]),
    ],
    targets: [
        .target(name: "WallpaperCore", linkerSettings: [
            .linkedFramework("AppKit"), .linkedFramework("AVFoundation"),
            .linkedFramework("IOKit"), .linkedFramework("Security"),
        ]),
        .executableTarget(name: "WallpaperStudio", dependencies: ["WallpaperCore"]),
        .testTarget(name: "WallpaperCoreTests", dependencies: ["WallpaperCore"]),
    ],
    swiftLanguageVersions: [.v5]
)
