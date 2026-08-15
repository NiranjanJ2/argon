// swift-tools-version: 5.9
import PackageDescription

// SwiftPM rather than an Xcode project: this target is a menu bar item and an
// HTTP client, with no storyboards, no asset catalogue and one target. The
// widget extension in phase two needs a real .xcodeproj — extensions cannot be
// built by SwiftPM — and this code moves into it unchanged when that lands.
let package = Package(
  name: "ArgonMac",
  platforms: [.macOS(.v14)],
  targets: [
    .executableTarget(name: "ArgonMac", path: "Sources/ArgonMac")
  ]
)
