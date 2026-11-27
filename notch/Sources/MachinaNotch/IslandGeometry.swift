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
    var expandedWidth: CGFloat = 420
    var expandedHeight: CGFloat = 560

    /// Transparent breathing room in the panel for the island's shadow.
    static let panelPadding: CGFloat = 40
    static let bannerWidth: CGFloat = 342
    static let bannerHeight: CGFloat = 54
    static let bannerGap: CGFloat = 8

    var compactSize: CGSize { CGSize(width: compactWidth, height: notchHeight) }
    var expandedSize: CGSize { CGSize(width: expandedWidth, height: expandedHeight) }

    /// Expanded content starts below the camera housing so nothing sits under it.
    var expandedContentInset: CGFloat { hasNotch ? notchHeight + 2 : 10 }

    /// The window grows with the island so the shadow has room and the panel
    /// never covers more of the screen than it needs to.
    func windowSize(expanded: Bool, banner: Bool) -> CGSize {
        if expanded {
            return CGSize(
                width: expandedWidth + Self.panelPadding * 2,
                height: expandedHeight + Self.panelPadding
            )
        }
        let bannerRoom = banner ? Self.bannerGap + Self.bannerHeight : 0
        let width = max(compactWidth, banner ? Self.bannerWidth : 0)
        return CGSize(
            width: width + Self.panelPadding * 2,
            height: notchHeight + bannerRoom + Self.panelPadding
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

    func windowFrame(in screen: NSScreen, expanded: Bool, banner: Bool) -> NSRect {
        let area = screen.frame
        let size = windowSize(expanded: expanded, banner: banner)
        return NSRect(
            x: area.midX - size.width / 2,
            y: area.maxY - size.height,
            width: size.width,
            height: size.height
        )
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

    /// The transient banner hangs just below the collapsed island.
    func bannerRect(in screen: NSScreen) -> NSRect {
        NSRect(
            x: screen.frame.midX - Self.bannerWidth / 2,
            y: screen.frame.maxY - notchHeight - Self.bannerGap - Self.bannerHeight,
            width: Self.bannerWidth,
            height: Self.bannerHeight
        )
    }
}

/// The black island silhouette. The top corners use concave quarter-arcs so
/// the surface appears to grow out of the menu bar; the lower corners are
/// convex arcs. All radii are animatable, so compact and expanded states morph
/// as a single continuous surface.
struct IslandSilhouette: Shape {
    var topFlare: CGFloat
    var bottomRadius: CGFloat

    var animatableData: AnimatablePair<CGFloat, CGFloat> {
        get { AnimatablePair(topFlare, bottomRadius) }
        set {
            topFlare = newValue.first
            bottomRadius = newValue.second
        }
    }

    func path(in rect: CGRect) -> Path {
        let width = rect.width
        let height = rect.height
        let flare = max(0.5, min(topFlare, height / 2, width / 4))
        let bottom = max(0.5, min(bottomRadius, height - flare, (width - 2 * flare) / 2))
        let left = rect.minX
        let right = rect.maxX
        let top = rect.minY
        let lower = rect.maxY

        var path = Path()
        path.move(to: CGPoint(x: left, y: top))

        // Concave top-left: a quarter circle whose centre is the inner
        // shoulder of the flare.
        path.addRelativeArc(
            center: CGPoint(x: left + flare, y: top),
            radius: flare,
            startAngle: .degrees(180),
            delta: .degrees(-90)
        )
        path.addLine(to: CGPoint(x: left + flare, y: lower - bottom))

        // Convex bottom-left.
        path.addRelativeArc(
            center: CGPoint(x: left + flare + bottom, y: lower - bottom),
            radius: bottom,
            startAngle: .degrees(180),
            delta: .degrees(-90)
        )
        path.addLine(to: CGPoint(x: right - flare - bottom, y: lower))

        // Convex bottom-right.
        path.addRelativeArc(
            center: CGPoint(x: right - flare - bottom, y: lower - bottom),
            radius: bottom,
            startAngle: .degrees(90),
            delta: .degrees(-90)
        )
        path.addLine(to: CGPoint(x: right - flare, y: top + flare))

        // Concave top-right, mirrored.
        path.addRelativeArc(
            center: CGPoint(x: right - flare, y: top),
            radius: flare,
            startAngle: .degrees(90),
            delta: .degrees(-90)
        )
        path.closeSubpath()
        return path
    }
}
