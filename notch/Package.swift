// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "MachinaNotch",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "MachinaNotch", targets: ["MachinaNotch"])
    ],
    targets: [
        .executableTarget(
            name: "MachinaNotch",
            path: "Sources/MachinaNotch"
        )
    ]
)
