import AppKit
import SwiftUI

/// Hardware-notch measurements and the window/content rectangles used by the
/// panel. Everything is in screen coordinates (AppKit's bottom-left origin) so
/// the controller can hit-test the pointer without asking SwiftUI for geometry.
struct IslandMetrics: Equatable {
    /// True when the chosen screen has a camera housing (safe-area inset).
    var hasNotch = false
    /// Width of the physical cutout. Ignored when `hasNotch` is false.
    var notchWidth: CGFloat = 0
    /// Height of the cutout, which is also the collapsed island height.
    var notchHeight: CGFloat = 32
    /// The black wings around the cutout make room for the mascot and timer.
    var compactWidth: CGFloat = 188
    var expandedWidth: CGFloat = 460
    var expandedHeight: CGFloat = 560

    /// Transparent breathing room in the panel for the island's shadow.
    static let panelPadding: CGFloat = 40
    static let alertHeight: CGFloat = 58
    static let minAlertWidth: CGFloat = 460
    static let bannerGap: CGFloat = 8

    var compactSize: CGSize { CGSize(width: compactWidth, height: notchHeight) }
    var expandedSize: CGSize { CGSize(width: expandedWidth, height: expandedHeight) }
    var alertSize: CGSize {
        CGSize(width: max(Self.minAlertWidth, notchWidth + 260), height: notchHeight + Self.alertHeight)
    }

    /// Expanded content starts below the camera housing so nothing sits under it.
    var expandedContentInset: CGFloat { hasNotch ? notchHeight + 2 : 10 }

    /// One fixed canvas for every island state. The window is positioned once;
    /// only SwiftUI morphs inside it, so expand/collapse never resize the
    /// NSWindow and never clip the outgoing shape.
    var panelSize: CGSize {
        CGSize(
            width: max(expandedWidth, alertSize.width) + Self.panelPadding * 2,
            height: expandedHeight + Self.panelPadding
        )
    }

    func windowFrame(in screen: NSScreen) -> NSRect {
        let area = screen.frame
        let size = panelSize
        return NSRect(
            x: area.midX - size.width / 2,
            y: area.maxY - size.height,
            width: size.width,
            height: size.height
        )
    }

    static func read(from screen: NSScreen) -> IslandMetrics {
        var metrics = IslandMetrics()
        let frame = screen.frame
        let safeTop = screen.safeAreaInsets.top

        if safeTop > 0 {
            metrics.hasNotch = true
            metrics.notchHeight = max(30, safeTop)
            if let left = screen.auxiliaryTopLeftArea,
               let right = screen.auxiliaryTopRightArea {
                // The auxiliary areas are the menu-bar strips to either side of
                // the cutout, so the gap between them is the cutout itself.
                metrics.notchWidth = max(120, frame.width - left.width - right.width)
            } else {
                // Older/odd compositor setups can omit the auxiliary areas even
                // when the safe-area inset reports a housing.
                metrics.notchWidth = min(220, max(150, frame.width * 0.13))
            }
            // 52pt of wing on each side, enough for the mascot and a timer.
            metrics.compactWidth = metrics.notchWidth + 104
        } else {
            metrics.hasNotch = false
            metrics.notchWidth = 0
            metrics.notchHeight = 32
            metrics.compactWidth = 188
        }
        return metrics
    }

    /// Collapsed/expanded island rect in screen coordinates.
    func islandRect(in screen: NSScreen, expanded: Bool) -> NSRect {
        let size = expanded ? expandedSize : compactSize
        return NSRect(
            x: screen.frame.midX - size.width / 2,
            y: screen.frame.maxY - size.height,
            width: size.width,
            height: size.height
        )
    }

    /// The alert island hangs below the notch and is wider than the compact
    /// state, matching the reference drop-down layout.
    func bannerRect(in screen: NSScreen) -> NSRect {
        let size = alertSize
        return NSRect(
            x: screen.frame.midX - size.width / 2,
            y: screen.frame.maxY - size.height,
            width: size.width,
            height: size.height
        )
    }
}
