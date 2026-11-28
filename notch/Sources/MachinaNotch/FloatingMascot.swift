import AppKit
import SwiftUI

/// A small, non-activating companion that can leave the notch, sit near a
/// screen target, and return. The window is only as large as the mascot, so it
/// is clickable there and never blocks the rest of the screen.
@MainActor
final class FloatingMascotController {
    private let model: NotchModel
    private let panel: MascotPanel
    private var hideWork: DispatchWorkItem?
    private var annotationPanel: NSPanel?
    private var annotationHideWork: DispatchWorkItem?

    private let mascotSize: CGFloat = 46

    init(model: NotchModel) {
        self.model = model
        panel = MascotPanel(
            contentRect: NSRect(x: 0, y: 0, width: mascotSize, height: mascotSize),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )

        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.isMovable = false
        panel.isMovableByWindowBackground = false
        panel.hidesOnDeactivate = false
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
        panel.animationBehavior = .none
        panel.ignoresMouseEvents = false

        let host = NSHostingView(
            rootView: FloatingMascotView(model: model)
                .frame(width: mascotSize, height: mascotSize)
        )
        host.frame = NSRect(x: 0, y: 0, width: mascotSize, height: mascotSize)
        host.autoresizingMask = [.width, .height]
        panel.contentView = host
        panel.orderOut(nil)
    }

    func show(at point: CGPoint) {
        hideWork?.cancel()
        let screen = NSScreen.screens.first { $0.frame.contains(point) }
            ?? NSScreen.screens.first { $0.safeAreaInsets.top > 0 }
            ?? NSScreen.main
            ?? NSScreen.screens.first
        guard let screen else { return }

        let target = clampedFrame(center: point, in: screen)
        if panel.isVisible {
            animate(to: target, alpha: 1)
        } else {
            let start = notchFrame(in: screen)
            panel.setFrame(start, display: true)
            panel.alphaValue = 0
            panel.orderFrontRegardless()
            animate(to: target, alpha: 1)
        }
        scheduleHide(after: 5.5)
    }

    func hide() {
        hideWork?.cancel()
        hideWork = nil
        guard panel.isVisible else { return }
        let screen = panel.screen ?? NSScreen.main
        guard let screen else {
            panel.orderOut(nil)
            return
        }
        let target = notchFrame(in: screen)
        NSAnimationContext.runAnimationGroup { context in
            context.duration = 0.32
            context.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
            panel.animator().setFrame(target, display: true)
            panel.animator().alphaValue = 0
        } completionHandler: { [weak panel] in
            Task { @MainActor in
                panel?.orderOut(nil)
            }
        }
    }

    func annotate(rect: CGRect, label: String, detail: String) {
        annotationHideWork?.cancel()
        annotationPanel?.orderOut(nil)

        guard let screen = NSScreen.screens.first(where: { $0.frame.intersects(rect) })
            ?? NSScreen.main
            ?? NSScreen.screens.first
        else { return }

        let panel = NSPanel(
            contentRect: screen.frame,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.isFloatingPanel = true
        panel.hidesOnDeactivate = false
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
        panel.animationBehavior = .none
        panel.ignoresMouseEvents = true

        // The annotation view lays out in window-local, top-left coordinates.
        let local = CGRect(
            x: rect.minX - screen.frame.minX,
            y: screen.frame.maxY - rect.maxY,
            width: rect.width,
            height: rect.height
        )
        panel.contentView = NSHostingView(
            rootView: AnnotationOverlay(
                rect: local,
                canvas: screen.frame.size,
                label: label,
                detail: detail
            )
        )
        panel.orderFrontRegardless()
        annotationPanel = panel

        let work = DispatchWorkItem { [weak self] in
            self?.annotationPanel?.orderOut(nil)
            self?.annotationPanel = nil
            self?.annotationHideWork = nil
        }
        annotationHideWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 8, execute: work)
    }

    func hideAnnotation() {
        annotationHideWork?.cancel()
        annotationHideWork = nil
        annotationPanel?.orderOut(nil)
        annotationPanel = nil
    }

    private func scheduleHide(after seconds: TimeInterval) {
        hideWork?.cancel()
        let work = DispatchWorkItem { [weak self] in
            self?.hide()
        }
        hideWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds, execute: work)
    }

    private func animate(to frame: NSRect, alpha: CGFloat) {
        NSAnimationContext.runAnimationGroup { context in
            context.duration = 0.34
            context.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
            panel.animator().setFrame(frame, display: true)
            panel.animator().alphaValue = alpha
        }
    }

    private func clampedFrame(center: CGPoint, in screen: NSScreen) -> NSRect {
        let visible = screen.visibleFrame.insetBy(dx: mascotSize / 2, dy: mascotSize / 2)
        let x = min(max(center.x - mascotSize / 2, visible.minX), visible.maxX - mascotSize)
        let y = min(max(center.y - mascotSize / 2, visible.minY), visible.maxY - mascotSize)
        return NSRect(x: x, y: y, width: mascotSize, height: mascotSize)
    }

    private func notchFrame(in screen: NSScreen) -> NSRect {
        NSRect(
            x: screen.frame.midX - mascotSize / 2,
            y: screen.frame.maxY - 38 - mascotSize - 6,
            width: mascotSize,
            height: mascotSize
        )
    }
}

/// The floating mascot should never take keyboard focus from the user's app.
private final class MascotPanel: NSPanel {
    nonisolated override var canBecomeKey: Bool { false }
    nonisolated override var canBecomeMain: Bool { false }
}

private struct FloatingMascotView: View {
    @ObservedObject var model: NotchModel

    var body: some View {
        MascotView(state: model.mascot, size: 34, calm: true, idlePaused: true)
            .frame(width: 46, height: 46)
            .contentShape(Circle())
            .onTapGesture { model.pet() }
            .help("Maki")
            .accessibilityLabel("Maki")
            .accessibilityHint("Tap to say hi")
    }
}

private struct AnnotationOverlay: View {
    let rect: CGRect
    let canvas: CGSize
    let label: String
    let detail: String

    var body: some View {
        ZStack(alignment: .topLeading) {
            Color.clear

            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .stroke(
                    LinearGradient(
                        colors: [amber, Color.white.opacity(0.85), amber],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    ),
                    lineWidth: 2
                )
                .background(
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .fill(amber.opacity(0.06))
                )
                .frame(width: rect.width, height: rect.height)
                .position(x: rect.midX, y: rect.midY)

            VStack(alignment: .leading, spacing: 2) {
                Text(label)
                    .font(.system(size: 12, weight: .semibold, design: .rounded))
                    .foregroundStyle(.white)
                if !detail.isEmpty {
                    Text(detail)
                        .font(.system(size: 10.5, design: .rounded))
                        .foregroundStyle(.white.opacity(0.65))
                        .lineLimit(2)
                }
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            .background(
                RoundedRectangle(cornerRadius: 9, style: .continuous)
                    .fill(Color.black.opacity(0.82))
                    .overlay(
                        RoundedRectangle(cornerRadius: 9, style: .continuous)
                            .stroke(amber.opacity(0.5), lineWidth: 0.8)
                    )
            )
            .fixedSize(horizontal: false, vertical: true)
            .position(
                x: min(max(rect.minX + 70, 90), canvas.width - 90),
                y: max(rect.minY - 24, 28)
            )
        }
        .frame(width: canvas.width, height: canvas.height)
        .ignoresSafeArea()
    }
}
