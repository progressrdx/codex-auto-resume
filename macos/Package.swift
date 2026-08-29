// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "CodexAutoResumeApp",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "CodexAutoResumeApp", targets: ["CodexAutoResumeApp"])],
    targets: [
        .executableTarget(name: "CodexAutoResumeApp"),
        .testTarget(name: "CodexAutoResumeAppTests", dependencies: ["CodexAutoResumeApp"])
    ]
)
