import AppKit
import CoreGraphics
import ScreenCaptureKit

enum ScreenCaptureError: LocalizedError {
    case noDisplay
    case encodingFailed
    case tooLarge

    var errorDescription: String? {
        switch self {
        case .noDisplay: return "No display available for capture"
        case .encodingFailed: return "Could not encode the screenshot"
        case .tooLarge: return "Screenshot exceeds the 5 MiB attachment limit"
        }
    }
}

/// On-demand display capture. The macOS Screen Recording permission is
/// requested by ScreenCaptureKit on the first call; nothing is captured in the
/// background.
@MainActor
enum ScreenCapture {
    static func jpeg() async throws -> Data {
        let content = try await SCShareableContent.excludingDesktopWindows(
            false,
            onScreenWindowsOnly: true
        )
        guard let display = content.displays.first else {
            throw ScreenCaptureError.noDisplay
        }
        let filter = SCContentFilter(display: display, excludingWindows: [])
        let configuration = SCStreamConfiguration()
        let width = min(display.width * 2, 2_560)
        let height = min(display.height * 2, 1_600)
        configuration.width = width
        configuration.height = height
        configuration.showsCursor = false
        let image = try await SCScreenshotManager.captureImage(
            contentFilter: filter,
            configuration: configuration
        )
        let representation = NSBitmapImageRep(cgImage: image)
        guard let data = representation.representation(
            using: .jpeg,
            properties: [.compressionFactor: 0.72]
        ) else {
            throw ScreenCaptureError.encodingFailed
        }
        guard data.count <= 5 * 1024 * 1024 else {
            throw ScreenCaptureError.tooLarge
        }
        return data
    }
}
