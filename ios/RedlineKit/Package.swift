// swift-tools-version: 6.2

import PackageDescription

// Everything the app does that a test can check without a simulator: the API client,
// the models, the formatting, the session logic. It builds for macOS too, so that
// `swift test` runs on the host.
let package = Package(
    name: "RedlineKit",
    defaultLocalization: "fr",
    platforms: [.iOS(.v26), .macOS(.v26)],
    products: [
        .library(name: "RedlineKit", targets: ["RedlineKit"]),
    ],
    targets: [
        .target(name: "RedlineKit"),
        .testTarget(
            name: "RedlineKitTests",
            dependencies: ["RedlineKit"],
            resources: [.copy("Fixtures")]
        ),
    ]
)
